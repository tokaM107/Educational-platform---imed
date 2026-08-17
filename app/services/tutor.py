"""The RAG orchestration: question -> passages -> grounded answer + video segments."""

import logging
from dataclasses import dataclass, field

from app.config import get_settings
from app.services import prompts, query_cache, retrieval
from app.services.embeddings import Embedder
from app.services.llm import ChatModel, LLMUnavailable


logger = logging.getLogger(__name__)


@dataclass
class TutorAnswer:

    answer: str
    grounded: bool
    passages: list = field(default_factory=list)
    segments: list = field(default_factory=list)

    # Set when the answer is degraded (e.g. the model was unreachable)
    notice: str | None = None


class TutorService:
    """Stateless per request — safe to keep one instance for the whole app."""

    def __init__(self, embedder=None, chat_model=None, settings=None):

        self.settings = settings or get_settings()
        self.embedder = embedder or Embedder(self.settings)
        self.chat_model = chat_model or ChatModel(self.settings)

    def ask(self, conn, question, lecture_id=None, history=None):

        question = (question or "").strip()

        if not question:
            return TutorAnswer(answer=prompts.NOT_IN_LECTURE, grounded=False)

        # Chunk embeddings live in the database already; only the question has
        # to be turned into a vector, and even that is cached.
        query_embedding = query_cache.embed_query(conn, self.embedder, question)

        passages = retrieval.search(
            conn,
            query_embedding,
            top_k=self.settings.top_k,
            lecture_id=lecture_id,
        )

        relevant = retrieval.keep_relevant(passages)

        # Nothing close enough: refuse here rather than letting the model
        # improvise around weak context.
        if not relevant:
            return TutorAnswer(
                answer=prompts.NOT_IN_LECTURE,
                grounded=False,
                passages=[],
                segments=[],
            )

        try:
            reply = self.chat_model.generate(
                system_instruction=prompts.SYSTEM_INSTRUCTION,
                user_prompt=prompts.build_user_prompt(question, relevant),
                response_schema=prompts.TutorReply,
                history=history,
            )

        except LLMUnavailable as error:

            # Retrieval already succeeded, so the student still gets the right
            # stretch of video — only the simplified explanation is missing.
            logger.error("chat model unavailable: %s", error)

            return TutorAnswer(
                answer=prompts.LLM_DOWN,
                grounded=True,
                passages=relevant,
                segments=retrieval.to_segments(relevant),
                notice="المساعد الذكي مش متاح دلوقتي — الفيديو والمقاطع شغالة عادي.",
            )

        # The model is the final judge of grounding: a question about another
        # topic can still be the "nearest" chunk in this lecture.
        if not reply.found:
            return TutorAnswer(
                answer=reply.answer or prompts.NOT_IN_LECTURE,
                grounded=False,
                passages=[],
                segments=[],
            )

        used = self._used_passages(relevant, reply.used_excerpts)

        return TutorAnswer(
            answer=reply.answer,
            grounded=True,
            passages=relevant,
            segments=retrieval.to_segments(used),
        )

    @staticmethod
    def _used_passages(passages, used_excerpts):
        """Excerpt numbers the model cited (1-based) -> passages.

        Playback should point at what the answer was actually built from, not
        at every candidate the search returned.
        """

        used = [
            passages[index - 1]
            for index in used_excerpts or []
            if 1 <= index <= len(passages)
        ]

        return used or passages
