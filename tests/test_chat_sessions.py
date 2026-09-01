"""Session APIs derive identity from JWT and persist ordered, idempotent turns."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat, deps
from app.services.token_budget import CountedTokens
from app.services.llm import GeneratedReply
from app.services import prompts
from app.services.tutor import TutorAnswer
from tests.fake_db import FakeConn


STUDENT = {"id": 2, "name": "Ahmed", "email": "student@example.com",
           "role": "student", "auth_user_id": "11111111-1111-1111-1111-111111111111"}
OTHER = {**STUDENT, "id": 99}
DOCTOR = {**STUDENT, "id": 1, "role": "doctor"}
SESSION_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
NOW = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


class FakeCounter:
    tokenizer_name = "fake:model-v1"
    def count_text(self, text):
        return CountedTokens(len((text or "").split()) or 1, self.tokenizer_name)


class FakeTutor:
    def __init__(self, result=None):
        self.result = result or TutorAnswer(answer="The answer", grounded=True,
                                            standalone_query="Standalone")
        self.calls = []
        self.token_counter = FakeCounter()
    def ask(self, conn, **kwargs):
        self.calls.append(kwargs)
        return self.result


def message_row(message_id, order, role, content, *, standalone=None, citations=None,
                status="completed", reply_grounded=None, total_tokens=None):
    return (message_id, SESSION_ID, order, role, content, standalone, citations or [],
            3, "fake:model-v1", "gemini-test" if role == "assistant" else None,
            "lecture-answer-v2" if role == "assistant" else None,
            20 if role == "assistant" else None, 4 if role == "assistant" else None,
            total_tokens, status, None, reply_grounded, NOW)


@pytest.fixture
def api(monkeypatch):
    application = FastAPI()
    application.include_router(chat.router)
    monkeypatch.setattr(chat.subscriptions, "can_watch",
                        lambda conn, student_id, lecture_id: (True, 1, "Anatomy"))
    return application


def client_for(api, conn, user=STUDENT, tutor=None):
    api.dependency_overrides[deps.get_conn] = lambda: conn
    api.dependency_overrides[deps.get_current_user] = lambda: user
    api.dependency_overrides[deps.get_tutor] = lambda: tutor or FakeTutor()
    api.dependency_overrides[deps.chat_llm_quota] = lambda: None
    return TestClient(api)


def test_student_creates_session_from_jwt_identity(api):
    conn = FakeConn(lambda sql, params: [
        (SESSION_ID, STUDENT["id"], 7, "Revision", NOW, NOW, 0)
    ] if "INSERT INTO chat_sessions" in sql else [])
    response = client_for(api, conn).post(
        "/api/chat/sessions", json={"lecture_id": 7, "title": "  Revision  "})
    assert response.status_code == 201
    assert response.json()["student_id"] == STUDENT["id"]
    assert conn.params_for("INSERT INTO chat_sessions") == (2, 7, "Revision")
    assert conn.committed == 1


def test_session_body_rejects_caller_supplied_user_id(api):
    response = client_for(api, FakeConn()).post(
        "/api/chat/sessions", json={"user_id": 99, "lecture_id": 7})
    assert response.status_code == 422


def test_doctor_cannot_create_student_session(api):
    response = client_for(api, FakeConn(), user=DOCTOR).post(
        "/api/chat/sessions", json={"lecture_id": 7})
    assert response.status_code == 403


def test_inaccessible_lecture_is_rejected(api, monkeypatch):
    monkeypatch.setattr(chat.subscriptions, "can_watch",
                        lambda *args: (False, 1, "Private"))
    response = client_for(api, FakeConn()).post(
        "/api/chat/sessions", json={"lecture_id": 7})
    assert response.status_code == 402


def test_sessions_are_paginated_and_scoped_to_student_and_lecture(api):
    conn = FakeConn(lambda sql, params: [
        (SESSION_ID, 2, 7, None, NOW, NOW, 0)
    ] if "FROM chat_sessions" in sql else [])
    response = client_for(api, conn).get(
        "/api/chat/sessions?lecture_id=7&limit=10&offset=20")
    assert response.status_code == 200
    assert conn.params_for("ORDER BY updated_at") == (2, 7, 7, 10, 20)


def test_messages_use_stable_order_and_pagination(api):
    def answer(sql, params):
        if "SELECT lecture_id FROM chat_sessions" in sql:
            return [(7,)]
        if "FROM chat_messages" in sql:
            return [message_row(11, 1, "user", "First"),
                    message_row(12, 2, "assistant", "Second", reply_grounded=True)]
        return []
    conn = FakeConn(answer)
    response = client_for(api, conn).get(
        f"/api/chat/sessions/{SESSION_ID}/messages?limit=25&offset=5")
    assert response.status_code == 200
    assert [item["message_order"] for item in response.json()] == [1, 2]
    assert conn.params_for("ORDER BY message_order") == (SESSION_ID, 25, 5)


def test_other_students_session_is_not_exposed(api):
    response = client_for(api, FakeConn()).get(
        f"/api/chat/sessions/{SESSION_ID}/messages")
    assert response.status_code == 404


def test_post_message_persists_standalone_query_citations_and_usage(api):
    passage = SimpleNamespace(chunk_id=9, lecture_id=7, start_ts=10, end_ts=20,
                              text="source", distance=0.23456)
    segment = SimpleNamespace(lecture_id=7, lecture_title="Anatomy",
                              start_ts=10, end_ts=20)
    tutor = FakeTutor(TutorAnswer(
        answer="Radius", grounded=True,
        standalone_query="What did the lecturer call it?", passages=[passage],
        segments=[segment], model_name="gemini-test", input_tokens=31,
        output_tokens=5, total_tokens=36, prompt_token_count=27,
        prompt_tokenizer_name="fake:model-v1",
        rewrite_model_name="gemini-test", rewrite_input_tokens=8,
        rewrite_output_tokens=3, rewrite_total_tokens=11,
    ))
    user_pending = message_row(21, 1, "user", "What is it?", status="pending")
    user_done = message_row(21, 1, "user", "What is it?",
                            standalone="What did the lecturer call it?",
                            total_tokens=11)
    citation = {"index": 1, "chunk_id": 9, "lecture_id": 7, "start_ts": 10,
                "end_ts": 20, "text": "source", "distance": 0.2346}
    assistant = message_row(22, 2, "assistant", "Radius", citations=[citation],
                            reply_grounded=True, total_tokens=36)

    def answer(sql, params):
        if "FROM chat_sessions WHERE" in sql and "FOR UPDATE" in sql:
            return [(7, "", 0, 1)]
        if "role = 'user' AND idempotency_key" in sql:
            return []
        if "status = 'completed'" in sql and "ORDER BY message_order DESC" in sql:
            return []
        if "VALUES (%s, %s, 'user'" in sql:
            return [user_pending]
        if "UPDATE chat_messages" in sql and "standalone_query" in sql:
            return [user_done]
        if "VALUES (%s, %s, 'assistant'" in sql:
            return [assistant]
        return []

    conn = FakeConn(answer)
    response = client_for(api, conn, tutor=tutor).post(
        f"/api/chat/sessions/{SESSION_ID}/messages",
        headers={"Idempotency-Key": "request-0001"},
        json={"content": "What is it?"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["standalone_query"] == "What did the lecturer call it?"
    assert body["citations"][0]["start_ts"] == 10
    assert body["token_usage"]["input_tokens"] == 20  # provider value stored in row
    assert body["token_usage"]["total_tokens"] == 47
    rewrite_params = conn.params_for("standalone_query = %s")
    assert rewrite_params[5] == 11
    answer_params = conn.params_for("VALUES (%s, %s, 'assistant'")
    assert answer_params[10] == 36
    assert tutor.calls[0]["lecture_id"] == 7
    assert conn.params_for("next_message_order = next_message_order + 2") == (SESSION_ID,)
    assert conn.params_for("role = 'user' AND idempotency_key") == (
        SESSION_ID, "request-0001")


def test_repeated_idempotency_key_returns_existing_pair(api):
    user = message_row(21, 1, "user", "Why?", standalone="Why X?")
    assistant = message_row(22, 2, "assistant", "Because", reply_grounded=True)
    def answer(sql, params):
        if "FROM chat_sessions WHERE" in sql and "FOR UPDATE" in sql:
            return [(7, "", 0, 3)]
        if "role = 'user' AND idempotency_key" in sql:
            return [(21, "Why?")]
        if "WHERE id = %s" in sql and "FROM chat_messages" in sql:
            return [user]
        if "reply_to_message_id" in sql:
            return [assistant]
        return []
    tutor = FakeTutor()
    response = client_for(api, FakeConn(answer), tutor=tutor).post(
        f"/api/chat/sessions/{SESSION_ID}/messages",
        headers={"Idempotency-Key": "same-key"}, json={"content": "Why?"})
    assert response.status_code == 201
    assert response.json()["assistant_message"]["id"] == 22
    assert tutor.calls == []


def test_idempotency_key_cannot_be_reused_for_different_content(api):
    def answer(sql, params):
        if "FROM chat_sessions WHERE" in sql and "FOR UPDATE" in sql:
            return [(7, "", 0, 3)]
        if "role = 'user' AND idempotency_key" in sql:
            return [(21, "Original question")]
        return []
    response = client_for(api, FakeConn(answer)).post(
        f"/api/chat/sessions/{SESSION_ID}/messages",
        headers={"Idempotency-Key": "same-key"},
        json={"content": "Different question"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"].startswith("idempotency_key_reused")


def test_post_requires_idempotency_key(api):
    response = client_for(api, FakeConn()).post(
        f"/api/chat/sessions/{SESSION_ID}/messages", json={"content": "Why?"})
    assert response.status_code == 422


def test_oversized_message_is_rejected_before_database_or_tutor(api, monkeypatch):
    settings = chat.get_settings()
    monkeypatch.setattr(settings, "chat_max_student_message_tokens", 2)
    tutor = FakeTutor()
    conn = FakeConn()
    response = client_for(api, conn, tutor=tutor).post(
        f"/api/chat/sessions/{SESSION_ID}/messages",
        headers={"Idempotency-Key": "large-msg"}, json={"content": "one two three"})
    assert response.status_code == 422
    assert tutor.calls == []
    assert conn.calls == []


def test_post_serializes_concurrent_session_requests(api):
    # The lock plus FOR UPDATE is the concurrency contract; the migration adds
    # unique session/order and idempotency indexes as the final database guard.
    def answer(sql, params):
        if "FOR UPDATE" in sql:
            return None
        return []
    conn = FakeConn(answer)
    response = client_for(api, conn).post(
        f"/api/chat/sessions/{SESSION_ID}/messages",
        headers={"Idempotency-Key": "concurrent"}, json={"content": "Why?"})
    assert response.status_code == 404
    assert conn.params_for("pg_advisory_xact_lock") == (str(SESSION_ID),)


def test_rolling_summary_updates_checkpoint_atomically():
    rows = [
        (index, index, "user" if index % 2 else "assistant",
         f"message {index}", 2, "fake:model-v1", [])
        for index in range(1, 7)
    ]
    conn = FakeConn(lambda sql, params: rows if "FROM chat_messages" in sql else [])

    class SummaryTutor:
        def __init__(self):
            self.calls = []
        def _generate(self, **kwargs):
            self.calls.append(kwargs)
            return GeneratedReply(
                parsed=prompts.ConversationSummaryReply(
                    summary="Discussed pneumatic bones; pronoun it refers to maxilla."
                ), model_name="gemini-test", input_tokens=20,
                output_tokens=5, total_tokens=25)

    tutor = SummaryTutor()
    settings = SimpleNamespace(
        chat_summary_update_threshold=5, chat_history_load_limit=100,
        chat_summary_max_output_tokens=100, chat_summary_tokens=50,
        chat_max_input_tokens=12000, llm_context_window=20000,
        chat_max_output_tokens=1200, chat_safety_margin_tokens=500,
    )
    chat._update_summary(conn, SESSION_ID, "old summary", 0,
                         tutor, FakeCounter(), settings)
    params = conn.params_for("summarized_until_message_order = %s")
    assert params[0].startswith("Discussed pneumatic bones")
    assert params[3] == 2  # four newest messages remain verbatim
    assert params[10] == 0  # compare-and-set prevents duplicate summarization
    assert params[6:9] == (20, 5, 25)
    assert "old summary" in tutor.calls[0]["user_prompt"]
