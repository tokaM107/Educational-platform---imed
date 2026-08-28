from types import SimpleNamespace

import pytest

from app.services import prompts
from app.services.llm import GeneratedReply
from app.services.retrieval import Passage
from app.services.token_budget import CountedTokens
from app.services.tutor import TutorService


class Counter:
    tokenizer_name = "exact:test"
    def count_text(self, text):
        return CountedTokens(len(text.split()) or 1, self.tokenizer_name)
    def count_complete_prompt(self, system, messages):
        total = self.count_text(system).count
        total += sum(self.count_text(text).count + 4 for _, text in messages)
        return CountedTokens(total, self.tokenizer_name)


class Model:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
    def generate_with_metadata(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def settings(**overrides):
    values = dict(
        chat_model="gemini-test", chat_rewrite_history_tokens=100,
        chat_rewrite_max_output_tokens=160, chat_retrieval_candidate_limit=30,
        chat_transcript_tokens=200, chat_answer_history_tokens=100,
        chat_max_output_tokens=1200, chat_max_input_tokens=12000,
        llm_context_window=20000, chat_safety_margin_tokens=500,
        embed_model="embed-test", embed_dim=3,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def generated(parsed, model="gemini-test", input_tokens=10, output_tokens=3):
    return GeneratedReply(parsed=parsed, model_name=model,
                          input_tokens=input_tokens, output_tokens=output_tokens)


@pytest.fixture
def rag(monkeypatch):
    passage = Passage(9, 7, "Anatomy", "Pneumatic bones include the maxilla.",
                      60, 75, .2)
    calls = {}
    monkeypatch.setattr("app.services.tutor.query_cache.embed_query",
                        lambda conn, embedder, question, commit=False:
                        calls.setdefault("query", question) or [0, 0, 0])
    def search(conn, embedding, top_k, lecture_id):
        calls["lecture_id"] = lecture_id
        calls["top_k"] = top_k
        return [passage]
    monkeypatch.setattr("app.services.tutor.retrieval.search", search)
    monkeypatch.setattr("app.services.tutor.retrieval.by_chunk_ids",
                        lambda conn, ids, lecture_id: [])
    return passage, calls


def test_followup_is_rewritten_and_retrieval_stays_in_session_lecture(rag):
    model = Model([
        generated(prompts.StandaloneQueryReply(
            standalone_query="What example of pneumatic bones did the doctor mention?")),
        generated(prompts.TutorReply(found=True, answer="The maxilla [1]",
                                     used_excerpts=[1])),
    ])
    tutor = TutorService(embedder=SimpleNamespace(), chat_model=model,
                         settings=settings(), token_counter=Counter())
    result = tutor.ask(None, "What example did the doctor mention?", lecture_id=7,
                       history=[("user", "What are pneumatic bones?"),
                                ("assistant", "They contain air spaces.")])
    assert result.standalone_query.startswith("What example of pneumatic bones")
    assert rag[1]["query"] == result.standalone_query
    assert rag[1]["lecture_id"] == 7
    assert result.segments[0].start_ts == 57


def test_already_standalone_question_is_left_unchanged(rag):
    question = "What are pneumatic bones?"
    model = Model([
        generated(prompts.StandaloneQueryReply(standalone_query=question)),
        generated(prompts.TutorReply(found=True, answer="Answer [1]",
                                     used_excerpts=[1])),
    ])
    result = TutorService(SimpleNamespace(), model, settings(), Counter()).ask(
        None, question, lecture_id=7)
    assert result.standalone_query == question


def test_contextualizer_failure_falls_back_to_original_query(rag):
    question = "Why?"
    model = Model([
        RuntimeError("rewrite unavailable"),
        generated(prompts.TutorReply(found=True, answer="Because [1]",
                                     used_excerpts=[1])),
    ])
    result = TutorService(SimpleNamespace(), model, settings(), Counter()).ask(
        None, question, lecture_id=7)
    assert result.standalone_query == question
    assert rag[1]["query"] == question


def test_missing_evidence_returns_safe_answer_without_answer_generation(rag, monkeypatch):
    monkeypatch.setattr("app.services.tutor.retrieval.search", lambda *args, **kwargs: [])
    model = Model([generated(prompts.StandaloneQueryReply(standalone_query="Unknown"))])
    result = TutorService(SimpleNamespace(), model, settings(), Counter()).ask(
        None, "Unknown", lecture_id=7)
    assert result.grounded is False
    assert result.answer == prompts.NOT_IN_LECTURE
    assert len(model.calls) == 1


def test_summary_is_context_only_and_never_transcript_evidence(rag):
    model = Model([
        generated(prompts.StandaloneQueryReply(standalone_query="Why X?")),
        generated(prompts.TutorReply(found=True, answer="Transcript answer [1]",
                                     used_excerpts=[1])),
    ])
    TutorService(SimpleNamespace(), model, settings(), Counter()).ask(
        None, "Why?", lecture_id=7,
        summary="The previous assistant claimed an unsupported medical fact.")
    final_prompt = model.calls[1]["user_prompt"]
    assert "NEVER evidence" in final_prompt
    assert "ONLY medical evidence" in final_prompt
    assert "Pneumatic bones include the maxilla" in final_prompt


def test_provider_usage_is_kept_separate_for_rewrite_and_answer(rag):
    model = Model([
        generated(prompts.StandaloneQueryReply(standalone_query="Why X?"),
                  input_tokens=7, output_tokens=2),
        generated(prompts.TutorReply(found=True, answer="Because [1]",
                                     used_excerpts=[1]),
                  input_tokens=30, output_tokens=5),
    ])
    result = TutorService(SimpleNamespace(), model, settings(), Counter()).ask(
        None, "Why?", lecture_id=7)
    assert (result.rewrite_input_tokens, result.rewrite_output_tokens) == (7, 2)
    assert (result.input_tokens, result.output_tokens) == (30, 5)


def test_four_turn_pneumatic_bones_scenario_stays_grounded(monkeypatch):
    questions = [
        "What are pneumatic bones?",
        "What example did the doctor mention?",
        "Why are they classified differently?",
        "Can you show me where this was said in the video?",
    ]
    standalone = [
        questions[0],
        "What example of pneumatic bones did the doctor mention?",
        "Why are pneumatic bones classified differently?",
        "Where did the doctor discuss pneumatic bones in the lecture?",
    ]
    replies = []
    for query in standalone:
        replies.extend([
            generated(prompts.StandaloneQueryReply(standalone_query=query)),
            generated(prompts.TutorReply(
                found=True, answer="Grounded pneumatic-bones answer [1]",
                used_excerpts=[1])),
        ])
    model = Model(replies)
    passage = Passage(44, 7, "Anatomy", "The maxilla is a pneumatic bone example.",
                      125, 142, .18)
    retrieval_calls = []
    monkeypatch.setattr("app.services.tutor.query_cache.embed_query",
                        lambda conn, embedder, query, commit=False:
                        retrieval_calls.append((query, commit)) or [0, 0, 0])
    monkeypatch.setattr("app.services.tutor.retrieval.search",
                        lambda conn, embedding, top_k, lecture_id: (
                            retrieval_calls.append(("lecture", lecture_id)) or [passage]
                        ))
    monkeypatch.setattr("app.services.tutor.retrieval.by_chunk_ids",
                        lambda conn, ids, lecture_id: [])
    tutor = TutorService(SimpleNamespace(), model, settings(), Counter())
    history = []
    results = []
    for question in questions:
        result = tutor.ask(None, question, lecture_id=7, history=history)
        results.append(result)
        history.extend([("user", question), ("assistant", result.answer)])

    assert [result.standalone_query for result in results] == standalone
    assert all(result.grounded for result in results)
    assert all(result.segments[0].start_ts == 122 for result in results)
    assert all(result.prompt_token_count < 12000 for result in results)
    assert [item for item in retrieval_calls if item[0] == "lecture"] == [
        ("lecture", 7)
    ] * 4
