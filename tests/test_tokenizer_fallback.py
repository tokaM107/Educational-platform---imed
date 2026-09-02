"""Live regression: chat counting must never enter the Hugging Face stack."""

import builtins
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat, deps
from app.services import prompts
from app.services.llm import GeneratedReply
from app.services.retrieval import Passage
from app.services.token_budget import ProviderTokenCounter
from app.services.tutor import TutorService
from tests.fake_db import FakeConn


class Model:
    def __init__(self):
        self.calls = []
        self.replies = [
            GeneratedReply(
                parsed=prompts.StandaloneQueryReply(
                    standalone_query="What are pneumatic bones?"
                ),
                model_name="gemini-test",
                input_tokens=12,
                output_tokens=5,
                total_tokens=17,
            ),
            GeneratedReply(
                parsed=prompts.TutorReply(
                    found=True,
                    answer="They are bones containing air spaces [1].",
                    used_excerpts=[1],
                ),
                model_name="gemini-test",
                input_tokens=80,
                output_tokens=14,
                total_tokens=94,
            ),
        ]

    def generate_with_metadata(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


def test_api_chat_never_imports_or_contacts_huggingface_for_counting(monkeypatch):
    official_counts = []
    imports = []
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"transformers", "torchvision"}:
            imports.append(name)
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    class OfficialModels:
        def count_tokens(self, **kwargs):
            official_counts.append(kwargs)
            return SimpleNamespace(total_tokens=220)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(
        chat.subscriptions, "can_watch_video", lambda *args: (True, 7, "Anatomy")
    )
    monkeypatch.setattr(
        chat.subscriptions, "accessible_course_video_ids", lambda *args, **kwargs: [1]
    )
    passage = Passage(
        chunk_id=9,
        video_id=1,
        video_title="Anatomy",
        text="Pneumatic bones contain air spaces.",
        start_ts=60,
        end_ts=75,
        distance=.2,
    )
    monkeypatch.setattr(
        "app.services.tutor.query_cache.embed_query",
        lambda conn, embedder, question, commit=False: [0.0, 0.0, 0.0],
    )
    monkeypatch.setattr(
        "app.services.tutor.retrieval.search",
        lambda conn, embedding, top_k, video_id: [passage],
    )
    monkeypatch.setattr(
        "app.services.tutor.retrieval.by_chunk_ids",
        lambda conn, ids, video_id: [],
    )
    settings = SimpleNamespace(
        chat_model="gemini-test",
        chat_rewrite_history_tokens=2000,
        chat_rewrite_max_output_tokens=160,
        chat_retrieval_candidate_limit=30,
        chat_answer_history_tokens=2500,
        chat_max_output_tokens=1200,
        chat_max_input_tokens=12000,
        llm_context_window=20000,
        chat_safety_margin_tokens=500,
        chat_prompt_resize_max_attempts=8,
        embed_model="embed-test",
        embed_dim=3,
        max_distance=.45,
        segment_merge_gap=20,
        segment_lead_in=3,
    )
    counter = ProviderTokenCounter(
        settings.chat_model,
        client=SimpleNamespace(models=OfficialModels()),
    )
    tutor = TutorService(
        embedder=SimpleNamespace(settings=settings),
        chat_model=Model(),
        settings=settings,
        token_counter=counter,
    )
    application = FastAPI()
    application.include_router(chat.router)
    application.dependency_overrides[deps.get_conn] = lambda: FakeConn()
    application.dependency_overrides[deps.get_current_user] = lambda: {
        "id": 2, "role": "student"
    }
    application.dependency_overrides[deps.get_tutor] = lambda: tutor
    application.dependency_overrides[deps.chat_llm_quota] = lambda: None

    response = TestClient(application).post(
        "/api/chat",
        json={"message": "What are pneumatic bones?", "video_id": 1},
    )

    assert response.status_code == 200
    assert response.json()["grounded"] is True
    assert response.json()["citations"][0]["chunk_id"] == 9
    assert imports == []
    assert len(official_counts) == 1
    assert official_counts[0]["model"] == "gemini-test"
    assert tutor.chat_model.calls[-1]["allow_fallback"] is False
    assert all(
        "huggingface.co" not in part.text
        for content in official_counts[0]["contents"]
        for part in content.parts
    )


def test_exact_count_failure_returns_controlled_503(monkeypatch):
    class BrokenOfficialModels:
        def count_tokens(self, **kwargs):
            raise OSError("count endpoint unavailable")

    monkeypatch.setattr(
        chat.subscriptions, "can_watch_video", lambda *args: (True, 7, "Anatomy")
    )
    monkeypatch.setattr(
        chat.subscriptions, "accessible_course_video_ids", lambda *args, **kwargs: [1]
    )

    passage = Passage(
        chunk_id=9, video_id=1, video_title="Anatomy",
        text="Pneumatic bones contain air spaces.", start_ts=60, end_ts=75,
        distance=.2,
    )
    monkeypatch.setattr(
        "app.services.tutor.query_cache.embed_query", lambda *args, **kwargs: [0, 0, 0]
    )
    monkeypatch.setattr(
        "app.services.tutor.retrieval.search", lambda *args, **kwargs: [passage]
    )
    monkeypatch.setattr(
        "app.services.tutor.retrieval.by_chunk_ids", lambda *args, **kwargs: []
    )
    settings = SimpleNamespace(
        chat_model="gemini-test", chat_rewrite_history_tokens=2000,
        chat_rewrite_max_output_tokens=160, chat_retrieval_candidate_limit=30,
        chat_answer_history_tokens=2500, chat_max_output_tokens=1200,
        chat_max_input_tokens=12000, llm_context_window=20000,
        chat_safety_margin_tokens=500, chat_prompt_resize_max_attempts=8,
        embed_model="embed-test", embed_dim=3, max_distance=.45,
        segment_merge_gap=20, segment_lead_in=3,
    )
    model = Model()
    counter = ProviderTokenCounter(
        settings.chat_model,
        client=SimpleNamespace(models=BrokenOfficialModels()),
    )
    tutor = TutorService(SimpleNamespace(settings=settings), model, settings, counter)
    application = FastAPI()
    application.include_router(chat.router)
    application.dependency_overrides[deps.get_conn] = lambda: FakeConn()
    application.dependency_overrides[deps.get_current_user] = lambda: {
        "id": 2, "role": "student"
    }
    application.dependency_overrides[deps.get_tutor] = lambda: tutor
    application.dependency_overrides[deps.chat_llm_quota] = lambda: None

    response = TestClient(application, raise_server_exceptions=False).post(
        "/api/chat", json={"message": "What are pneumatic bones?", "video_id": 1}
    )

    assert response.status_code == 503
    assert response.json()["detail"].startswith("تعذر التحقق")
    assert len(model.replies) == 1  # answer generation was never attempted
