"""Shared LLM quotas are atomic, authenticated, and fail before provider work."""

from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

from app.api import chat, deps, grading_demo, reports, search
from app.services import llm_quota
from tests.fake_db import FakeConn


def _dependency_calls(router, path):
    route = next(
        item
        for item in router.routes
        if item.path == path and "POST" in item.methods
    )
    calls = set()

    def collect(dependant):
        for child in dependant.dependencies:
            calls.add(child.call)
            collect(child)

    collect(route.dependant)
    return calls


@pytest.mark.parametrize(
    "router,path,quota_dependency",
    [
        (chat.router, "/api/chat", deps.chat_llm_quota),
        (
            chat.router,
            "/api/chat/sessions/{session_id}/messages",
            deps.chat_llm_quota,
        ),
        (
            grading_demo.router,
            "/api/grading-demo/generate-criteria",
            deps.grading_llm_quota,
        ),
        (
            grading_demo.router,
            "/api/grading-demo/evaluate-answer",
            deps.grading_llm_quota,
        ),
        (
            grading_demo.router,
            "/api/grading-demo/grade",
            deps.grading_llm_quota,
        ),
        (
            grading_demo.router,
            "/api/grading-demo/evaluate-dataset",
            deps.grading_dataset_llm_quota,
        ),
    ],
)
def test_every_llm_route_requires_shared_auth_and_daily_quota(
    router, path, quota_dependency
):
    calls = _dependency_calls(router, path)

    assert deps.get_current_user in calls
    assert quota_dependency in calls


def test_public_search_has_no_authentication_or_quota_dependency():
    calls = _dependency_calls(search.router, "/api/search")

    assert deps.get_current_user not in calls
    assert deps.search_llm_quota not in calls


@pytest.mark.parametrize("narrative,expected_calls", [(True, 1), (False, 0)])
def test_weekly_report_only_charges_when_ai_narrative_is_requested(
    monkeypatch, narrative, expected_calls
):
    charged = []
    monkeypatch.setattr(reports.authz, "may_view_student", lambda *args: True)
    monkeypatch.setattr(reports.report, "build", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        reports,
        "consume_llm_quota",
        lambda *args, **kwargs: charged.append((args, kwargs)),
    )

    with pytest.raises(HTTPException) as caught:
        reports.weekly_report(
            response=Response(),
            student_id=None,
            course_id=None,
            week_start=None,
            narrative=narrative,
            refresh=False,
            conn=FakeConn(),
            current_user={"id": 42, "role": "student"},
        )

    assert caught.value.status_code == 404
    assert len(charged) == expected_calls


def test_quota_reservation_uses_user_feature_and_shared_limit():
    conn = FakeConn(
        lambda sql, params: [(7, 3600)] if "INSERT INTO llm_daily_usage" in sql else []
    )

    usage = llm_quota.consume(conn, 42, "chat", limit=10)

    assert usage == llm_quota.QuotaUsage(limit=10, used=7, retry_after=3600)
    assert usage.remaining == 3
    assert conn.params_for("INSERT INTO llm_daily_usage") == (
        42, 1, "chat", 1, "chat", "chat", 1, 10,
    )
    assert conn.committed == 1


def test_quota_feature_key_is_explicitly_typed_for_postgres():
    """jsonb_build_object's variadic key does not infer a bound string type."""

    conn = FakeConn(
        lambda sql, params: [(1, 3600)] if "INSERT INTO llm_daily_usage" in sql else []
    )

    llm_quota.consume(conn, 42, "search", limit=10)

    sql = next(sql for sql, _ in conn.calls if "INSERT INTO llm_daily_usage" in sql)
    assert "jsonb_build_object(%s::text, %s)" in sql


def test_quota_rejects_when_atomic_upsert_cannot_reserve_more():
    def answer(sql, params):
        if "INSERT INTO llm_daily_usage" in sql:
            return []
        if "FROM llm_daily_usage" in sql:
            return [(10, 120)]
        return []

    conn = FakeConn(answer)

    with pytest.raises(llm_quota.QuotaExceeded) as caught:
        llm_quota.consume(conn, 42, "search", limit=10)

    assert caught.value.usage.used == 10
    assert caught.value.usage.remaining == 0
    assert caught.value.usage.retry_after == 120
    assert conn.committed == 1


def test_http_quota_returns_429_and_does_not_run_endpoint(monkeypatch):
    application = FastAPI()
    called = []

    @application.post("/llm")
    def endpoint(_quota=Depends(deps.chat_llm_quota)):
        called.append(True)
        return {"ok": True}

    application.dependency_overrides[deps.get_conn] = lambda: FakeConn()
    application.dependency_overrides[deps.get_current_user] = lambda: {
        "id": 42, "role": "student"
    }
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(llm_daily_query_limit=10),
    )
    monkeypatch.setattr(
        deps.llm_quota,
        "consume",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            llm_quota.QuotaExceeded(llm_quota.QuotaUsage(10, 10, 90))
        ),
    )

    response = TestClient(application).post("/llm")

    assert response.status_code == 429
    assert response.json()["detail"]["error"] == "daily_llm_limit_reached"
    assert response.headers["Retry-After"] == "90"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert called == []


def test_http_quota_exposes_remaining_budget(monkeypatch):
    application = FastAPI()

    @application.post("/llm")
    def endpoint(_quota=Depends(deps.chat_llm_quota)):
        return {"ok": True}

    application.dependency_overrides[deps.get_conn] = lambda: FakeConn()
    application.dependency_overrides[deps.get_current_user] = lambda: {
        "id": 42, "role": "student"
    }
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(llm_daily_query_limit=10),
    )
    monkeypatch.setattr(
        deps.llm_quota,
        "consume",
        lambda *args, **kwargs: llm_quota.QuotaUsage(10, 4, 3600),
    )

    response = TestClient(application).post("/llm")

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "10"
    assert response.headers["X-RateLimit-Remaining"] == "6"
