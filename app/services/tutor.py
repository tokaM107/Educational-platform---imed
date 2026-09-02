"""Conversational RAG: contextualize -> retrieve -> budget -> grounded answer."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import get_settings
from app.services import prompts, query_cache, retrieval
from app.services.chat_memory import MemoryMessage, select_passages, select_recent_turns
from app.services.embeddings import Embedder
from app.services.llm import ChatModel, GeneratedReply, LLMUnavailable
from app.services.token_budget import (
    PromptTooLarge, ProviderTokenCounter, effective_input_limit,
    validate_prompt_size,
)


logger = logging.getLogger(__name__)


@dataclass
class TutorAnswer:
    answer: str
    grounded: bool
    standalone_query: str | None = None
    passages: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    notice: str | None = None
    model_name: str | None = None
    prompt_version: str = prompts.ANSWER_PROMPT_VERSION
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    prompt_token_count: int | None = None
    prompt_tokenizer_name: str | None = None
    prompt_tokens_estimated: bool = False
    rewrite_model_name: str | None = None
    rewrite_input_tokens: int | None = None
    rewrite_output_tokens: int | None = None
    rewrite_total_tokens: int | None = None


class TutorService:
    """Stateless orchestration; all durable memory remains in Postgres."""

    def __init__(self, embedder=None, chat_model=None, settings=None, token_counter=None):
        self.settings = settings or get_settings()
        self.embedder = embedder or Embedder(self.settings)
        self.chat_model = chat_model or ChatModel(self.settings)
        self.token_counter = token_counter or ProviderTokenCounter(
            self.settings.chat_model, getattr(self.chat_model, "client", None),
            retry_attempts=getattr(
                self.settings, "chat_token_count_retry_attempts", 2
            ),
            retry_delay_seconds=getattr(
                self.settings, "chat_token_count_retry_delay_seconds", 0.25
            ),
        )

    def _generate(self, **kwargs) -> GeneratedReply:
        if hasattr(self.chat_model, "generate_with_metadata"):
            return self.chat_model.generate_with_metadata(**kwargs)
        return GeneratedReply(
            parsed=self.chat_model.generate(**kwargs),
            model_name=self.settings.chat_model,
        )

    @staticmethod
    def _normalise_history(history):
        normalised = []
        for index, item in enumerate(history or [], start=1):
            if isinstance(item, MemoryMessage):
                normalised.append(item)
            else:
                role, content = item
                normalised.append(MemoryMessage(
                    id=index, message_order=index,
                    role="assistant" if role in ("assistant", "tutor", "model") else "user",
                    content=content, token_count=0, tokenizer_name="unknown",
                ))
        return normalised

    def contextualize(self, question, history=None, summary=""):
        bounded = select_recent_turns(
            self._normalise_history(history),
            self.settings.chat_rewrite_history_tokens,
            self.token_counter,
        )
        pairs = [(message.role, message.content) for message in bounded]
        try:
            generated = self._generate(
                system_instruction=prompts.REWRITE_SYSTEM_INSTRUCTION,
                user_prompt=prompts.build_rewrite_prompt(question, pairs, summary),
                response_schema=prompts.StandaloneQueryReply,
                history=None,
                max_output_tokens=self.settings.chat_rewrite_max_output_tokens,
            )
            standalone = (generated.parsed.standalone_query or "").strip()
            return standalone or question, generated
        except Exception as error:
            logger.warning("follow-up contextualization failed; using original: %s", error)
            return question, None

    def ask(self, conn, question, video_id=None, accessible_video_ids=None,
            history=None, summary="", continuity_chunk_ids=None):
        question = (question or "").strip()
        if not question:
            return TutorAnswer(answer=prompts.NOT_IN_LECTURE, grounded=False)

        standalone, rewrite_usage = self.contextualize(question, history, summary)
        query_embedding = query_cache.embed_query(
            conn, self.embedder, standalone, commit=False
        )
        scope = list(dict.fromkeys(accessible_video_ids or [video_id]))
        if video_id not in scope:
            scope.insert(0, video_id)

        candidates = retrieval.keep_relevant(retrieval.search(
            conn, query_embedding,
            top_k=self.settings.chat_retrieval_candidate_limit,
            video_id=video_id,
        ))
        fallback_ids = [item for item in scope if item != video_id]
        if not candidates and fallback_ids:
            candidates = retrieval.keep_relevant(retrieval.search_videos(
                conn, query_embedding, fallback_ids,
                top_k=self.settings.chat_retrieval_candidate_limit,
            ))
        continuity = retrieval.by_chunk_ids(
            conn, list(continuity_chunk_ids or [])[:3], scope
        )
        rewrite_input = rewrite_usage.input_tokens if rewrite_usage else None
        rewrite_output = rewrite_usage.output_tokens if rewrite_usage else None
        rewrite_model = rewrite_usage.model_name if rewrite_usage else None
        rewrite_total = rewrite_usage.total_tokens if rewrite_usage else None

        if not candidates and not continuity:
            return TutorAnswer(
                answer=prompts.NOT_IN_LECTURE, grounded=False,
                standalone_query=standalone,
                rewrite_model_name=rewrite_model,
                rewrite_input_tokens=rewrite_input,
                rewrite_output_tokens=rewrite_output,
                rewrite_total_tokens=rewrite_total,
            )

        bounded_history = select_recent_turns(
            self._normalise_history(history),
            self.settings.chat_answer_history_tokens,
            self.token_counter,
        )
        history_pairs = [(message.role, message.content) for message in bounded_history]
        active_summary = summary
        # There is no transcript-only ceiling. Start with the safe product/model
        # input capacity, subtract conservative estimates for everything that
        # is not evidence, then let ranked relevant transcript chunks consume
        # all remaining room. Exact provider validation below is authoritative.
        sources = candidates + continuity

        def provisionally_select():
            empty_prompt = prompts.build_conversational_prompt(
                question, standalone, [], active_summary
            )
            provisional_base = self.token_counter.count_messages(
                history_pairs + [("user", empty_prompt)],
                prompts.SYSTEM_INSTRUCTION,
            ).count
            transcript_allowance = max(
                0, effective_input_limit(self.settings) - provisional_base
            )
            return select_passages(
                sources, transcript_allowance, self.token_counter
            )

        passages = provisionally_select()
        fresh_ids = {passage.chunk_id for passage in candidates}
        selected_fresh_ids = {
            passage.chunk_id for passage in passages
            if passage.chunk_id in fresh_ids
        }
        # If relevant fresh retrieval is being excluded by memory, evidence
        # wins. Recompute after dropping the summary, then oldest complete
        # turns, matching the production priority contract.
        while len(selected_fresh_ids) < len(fresh_ids):
            if active_summary:
                active_summary = ""
            elif history_pairs:
                remove = 2 if (
                    len(history_pairs) >= 2
                    and history_pairs[0][0] == "user"
                    and history_pairs[1][0] == "assistant"
                ) else 1
                history_pairs = history_pairs[remove:]
            else:
                break
            passages = provisionally_select()
            selected_fresh_ids = {
                passage.chunk_id for passage in passages
                if passage.chunk_id in fresh_ids
            }
        if not passages:
            return TutorAnswer(
                answer=prompts.NOT_IN_LECTURE, grounded=False,
                standalone_query=standalone,
                rewrite_model_name=rewrite_model,
                rewrite_input_tokens=rewrite_input,
                rewrite_output_tokens=rewrite_output,
                rewrite_total_tokens=rewrite_total,
            )

        max_resize_attempts = getattr(
            self.settings, "chat_prompt_resize_max_attempts", 8
        )
        prompt_count = None
        user_prompt = None
        for resize_attempt in range(max_resize_attempts):
            user_prompt = prompts.build_conversational_prompt(
                question, standalone, passages, active_summary
            )
            complete_messages = history_pairs + [("user", user_prompt)]
            prompt_count = self.token_counter.count_complete_prompt(
                prompts.SYSTEM_INSTRUCTION, complete_messages
            )
            try:
                validate_prompt_size(prompt_count.count, self.settings)
                break
            except PromptTooLarge:
                if active_summary:
                    active_summary = ""
                elif history_pairs:
                    # Remove exactly the oldest turn, never an assistant alone.
                    remove = 2 if (
                        len(history_pairs) >= 2
                        and history_pairs[0][0] == "user"
                        and history_pairs[1][0] == "assistant"
                    ) else 1
                    history_pairs = history_pairs[remove:]
                elif any(item.chunk_id not in fresh_ids for item in passages):
                    # Previous-answer continuity is lower priority than the new
                    # authorized course-scoped retrieval.
                    for index in range(len(passages) - 1, -1, -1):
                        if passages[index].chunk_id not in fresh_ids:
                            passages.pop(index)
                            break
                elif len(passages) > 1:
                    # Lowest-ranked fresh evidence is the final removable tier.
                    passages.pop()
                else:
                    return TutorAnswer(
                        answer=prompts.NOT_IN_LECTURE, grounded=False,
                        standalone_query=standalone,
                        rewrite_model_name=rewrite_model,
                        rewrite_input_tokens=rewrite_input,
                        rewrite_output_tokens=rewrite_output,
                        rewrite_total_tokens=rewrite_total,
                        prompt_token_count=prompt_count.count,
                        prompt_tokenizer_name=prompt_count.tokenizer_name,
                        prompt_tokens_estimated=False,
                    )
        else:
            raise PromptTooLarge(
                f"prompt did not fit after {max_resize_attempts} resize attempts"
            )

        try:
            generated = self._generate(
                system_instruction=prompts.SYSTEM_INSTRUCTION,
                user_prompt=user_prompt,
                response_schema=prompts.TutorReply,
                history=history_pairs,
                max_output_tokens=self.settings.chat_max_output_tokens,
                allow_fallback=False,
            )
            reply = generated.parsed
        except LLMUnavailable as error:
            logger.error("chat model unavailable: %s", error)
            return TutorAnswer(
                answer=prompts.LLM_DOWN, grounded=True,
                standalone_query=standalone, passages=passages,
                segments=retrieval.to_segments(passages),
                notice="المساعد الذكي مش متاح دلوقتي — الفيديو والمقاطع شغالة عادي.",
                rewrite_model_name=rewrite_model,
                rewrite_input_tokens=rewrite_input,
                rewrite_output_tokens=rewrite_output,
                rewrite_total_tokens=rewrite_total,
                prompt_token_count=prompt_count.count,
                prompt_tokenizer_name=prompt_count.tokenizer_name,
                prompt_tokens_estimated=prompt_count.estimated,
            )

        common = dict(
            standalone_query=standalone, model_name=generated.model_name,
            input_tokens=generated.input_tokens, output_tokens=generated.output_tokens,
            total_tokens=generated.total_tokens,
            rewrite_model_name=rewrite_model,
            rewrite_input_tokens=rewrite_input,
            rewrite_output_tokens=rewrite_output,
            rewrite_total_tokens=rewrite_total,
            prompt_token_count=prompt_count.count,
            prompt_tokenizer_name=prompt_count.tokenizer_name,
            prompt_tokens_estimated=prompt_count.estimated,
        )
        if not reply.found:
            return TutorAnswer(
                answer=reply.answer or prompts.NOT_IN_LECTURE,
                grounded=False, **common,
            )

        used = self._used_passages(passages, reply.used_excerpts)
        notice = None
        if any(passage.video_id != video_id for passage in used):
            notice = "الإجابة دي من فيديو تاني مرتبط بنفس الكورس."
        return TutorAnswer(
            answer=reply.answer, grounded=True, passages=used,
            segments=retrieval.to_segments(used), notice=notice, **common,
        )

    @staticmethod
    def _used_passages(passages, used_excerpts):
        used = [passages[index - 1] for index in used_excerpts or []
                if 1 <= index <= len(passages)]
        return used or passages
