"""Persistent chat sessions are private, ordered, and server-authored."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat, deps
from app.services.tutor import TutorAnswer
from tests.fake_db import FakeConn


STUDENT = {
    "id": 2,
    "name": "Ahmed",
    "email": "student@example.com",
    "role": "student",
    "auth_user_id": "11111111-1111-1111-1111-111111111111",
}
DOCTOR = {**STUDENT, "id": 1, "role": "doctor"}
SESSION_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
NOW = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


class FakeTutor:
    def __init__(self, result=None):
        self.result = result or TutorAnswer(answer="The answer", grounded=True)
        self.calls = []

    def ask(self, conn, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def api(monkeypatch):
    application = FastAPI()
    application.include_router(chat.router)

    # Access itself is covered by subscriptions tests; these tests keep the
    # chat route focused on identity, ownership, history and persistence.
    monkeypatch.setattr(
        chat.subscriptions,
        "can_watch",
        lambda conn, student_id, lecture_id: (True, 1, "Anatomy"),
    )

    return application


def client_for(api, conn, user=STUDENT, tutor=None):
    api.dependency_overrides[deps.get_conn] = lambda: conn
    api.dependency_overrides[deps.get_current_user] = lambda: user
    api.dependency_overrides[deps.get_tutor] = lambda: tutor or FakeTutor()
    return TestClient(api)


def test_student_creates_a_session_for_themselves(api):
    conn = FakeConn(
        lambda sql, params: [
            (SESSION_ID, STUDENT["id"], 7, "Revision", NOW, NOW)
        ]
        if "INSERT INTO chat_sessions" in sql
        else []
    )

    response = client_for(api, conn).post(
        "/api/chat/sessions",
        json={"lecture_id": 7, "title": "  Revision  "},
    )

    assert response.status_code == 201
    assert response.json()["id"] == str(SESSION_ID)
    assert response.json()["student_id"] == STUDENT["id"]
    assert conn.params_for("INSERT INTO chat_sessions") == (
        STUDENT["id"],
        7,
        "Revision",
    )
    assert conn.committed == 1


def test_doctor_cannot_create_a_student_chat_session(api):
    response = client_for(api, FakeConn(), user=DOCTOR).post(
        "/api/chat/sessions",
        json={"lecture_id": 7},
    )

    assert response.status_code == 403


def test_messages_are_scoped_to_the_session_owner(api):
    citations = [
        {
            "index": 1,
            "chunk_id": 9,
            "lecture_id": 7,
            "start_ts": 10,
            "end_ts": 20,
            "text": "source",
            "distance": 0.2,
        }
    ]

    def answer(sql, params):
        if "JOIN chat_messages" in sql:
            return [(11, SESSION_ID, "assistant", "Saved", None, citations, NOW)]
        return []

    conn = FakeConn(answer)
    response = client_for(api, conn).get(
        f"/api/chat/sessions/{SESSION_ID}/messages"
    )

    assert response.status_code == 200
    assert response.json()[0]["content"] == "Saved"
    assert conn.params_for("JOIN chat_messages") == (SESSION_ID, STUDENT["id"])


def test_an_unknown_or_other_students_session_is_not_exposed(api):
    response = client_for(api, FakeConn()).get(
        f"/api/chat/sessions/{SESSION_ID}/messages"
    )

    assert response.status_code == 404


def test_posting_a_message_generates_and_persists_both_turns(api):
    passage = SimpleNamespace(
        chunk_id=9,
        lecture_id=7,
        start_ts=10,
        end_ts=20,
        text="source",
        distance=0.23456,
    )
    segment = SimpleNamespace(
        lecture_id=7,
        lecture_title="Anatomy",
        start_ts=10,
        end_ts=20,
    )
    tutor = FakeTutor(
        TutorAnswer(
            answer="Radius",
            grounded=True,
            passages=[passage],
            segments=[segment],
        )
    )
    citation = {
        "index": 1,
        "chunk_id": 9,
        "lecture_id": 7,
        "start_ts": 10,
        "end_ts": 20,
        "text": "source",
        "distance": 0.2346,
    }

    def answer(sql, params):
        if "SELECT lecture_id" in sql:
            return [(7,)]
        if "SELECT role, content" in sql:
            return [("user", "Earlier"), ("assistant", "Earlier answer")]
        if "VALUES (%s, 'user'" in sql:
            return [(21, SESSION_ID, "user", "What is it?", "What is it?", None, NOW)]
        if "VALUES (%s, 'assistant'" in sql:
            return [(22, SESSION_ID, "assistant", "Radius", None, [citation], NOW)]
        return []

    conn = FakeConn(answer)
    response = client_for(api, conn, tutor=tutor).post(
        f"/api/chat/sessions/{SESSION_ID}/messages",
        json={"content": "  What is it?  "},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_message"]["role"] == "user"
    assert body["assistant_message"]["role"] == "assistant"
    assert body["assistant_message"]["citations"] == [citation]
    assert body["segments"][0]["video_url"] == "/api/lectures/7/video"
    assert tutor.calls[0] == {
        "question": "What is it?",
        "lecture_id": 7,
        "history": [("user", "Earlier"), ("assistant", "Earlier answer")],
    }
    assert conn.params_for("WHERE id = %s AND student_id = %s") == (
        SESSION_ID,
        STUDENT["id"],
    )
    assert conn.committed == 1


def test_blank_messages_are_rejected_before_the_tutor_runs(api):
    tutor = FakeTutor()
    response = client_for(api, FakeConn(), tutor=tutor).post(
        f"/api/chat/sessions/{SESSION_ID}/messages",
        json={"content": "   "},
    )

    assert response.status_code == 422
    assert tutor.calls == []
