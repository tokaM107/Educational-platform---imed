from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api import checkpoints
from app.schemas.checkpoints import CheckpointAttemptCreate
from tests.fake_db import FakeConn


STUDENT = {"id": 7, "role": "student"}
NOW = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)


def test_checkpoint_answer_is_scoped_to_authenticated_student(monkeypatch):
    def answer(sql, params):
        if "SELECT q.course_id" in sql:
            return [(3, 11, None, "B", 20, 2)]
        if "INSERT INTO checkpoint_attempts" in sql:
            return [(91, 41, True, False, False, 1, NOW)]
        return []

    conn = FakeConn(answer)
    monkeypatch.setattr(
        checkpoints.subscriptions, "entitled_to_course", lambda *args: True
    )
    result = checkpoints.create_checkpoint_attempt(
        41,
        CheckpointAttemptCreate(
            session_id="session-a", shown_at=NOW,
            answered_at=NOW + timedelta(seconds=8), response_time_ms=8000,
            selected_answer="B", outcome="answered",
        ),
        conn=conn,
        current_user=STUDENT,
    )
    params = conn.params_for("INSERT INTO checkpoint_attempts")
    assert params[0] == STUDENT["id"]
    assert result.is_correct is True
    assert conn.committed == 1


def test_checkpoint_rejects_fabricated_response_latency(monkeypatch):
    conn = FakeConn(lambda sql, params: [
        (3, 11, None, "B", 20, 2)
    ] if "SELECT q.course_id" in sql else [])
    monkeypatch.setattr(
        checkpoints.subscriptions, "entitled_to_course", lambda *args: True
    )
    with pytest.raises(HTTPException) as error:
        checkpoints.create_checkpoint_attempt(
            41,
            CheckpointAttemptCreate(
                session_id="session-a", shown_at=NOW,
                answered_at=NOW + timedelta(seconds=8), response_time_ms=100,
                selected_answer="B", outcome="answered",
            ),
            conn=conn,
            current_user=STUDENT,
        )
    assert error.value.status_code == 422
    assert conn.committed == 0


def test_checkpoint_access_is_denied_before_attempt_write(monkeypatch):
    conn = FakeConn(lambda sql, params: [
        (3, 11, None, "B", 20, 2)
    ] if "SELECT q.course_id" in sql else [])
    monkeypatch.setattr(
        checkpoints.subscriptions, "entitled_to_course", lambda *args: False
    )
    with pytest.raises(HTTPException) as error:
        checkpoints.create_checkpoint_attempt(
            41,
            CheckpointAttemptCreate(
                session_id="session-a", shown_at=NOW,
                answered_at=NOW + timedelta(seconds=8), response_time_ms=8000,
                selected_answer="B", outcome="answered",
            ),
            conn=conn,
            current_user=STUDENT,
        )
    assert error.value.status_code == 403
    assert not any("INSERT INTO checkpoint_attempts" in sql for sql, _ in conn.calls)


def test_student_checkpoint_payload_never_contains_correct_answer(monkeypatch):
    def answer(sql, params):
        if "SELECT l.course_id" in sql:
            return [(3, 2)]
        if "FROM checkpoint_questions AS q" in sql:
            return [(41, 11, None, "Spine", "Question?", ["A", "B"], 120, 20, 1)]
        return []

    conn = FakeConn(answer)
    monkeypatch.setattr(
        checkpoints.subscriptions, "entitled_to_course", lambda *args: True
    )
    rows = checkpoints.list_lecture_checkpoints(11, conn=conn, current_user=STUDENT)
    assert rows[0].model_dump().get("correct_answer") is None


def test_modern_video_checkpoint_payload_is_access_scoped(monkeypatch):
    def answer(sql, params):
        if "FROM checkpoint_questions AS q" in sql:
            return [(42, None, 17, "Spine", "Question?", ["A", "B"], 90, 15, 1)]
        return []

    conn = FakeConn(answer)
    monkeypatch.setattr(
        checkpoints.subscriptions, "can_watch_video", lambda *args: (True, 2, "Video")
    )
    rows = checkpoints.list_video_checkpoints(17, conn=conn, current_user=STUDENT)
    assert rows[0].video_id == 17
    assert conn.params_for("q.video_id = %s") == (17,)
    assert rows[0].model_dump().get("correct_answer") is None
