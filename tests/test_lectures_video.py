"""Bunny-backed lectures play through the authenticated video endpoint."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import deps, lectures
from rag import bunny
from tests.fake_db import FakeConn


STUDENT = {"id": 2, "role": "student"}


@pytest.fixture
def api(monkeypatch):
    application = FastAPI()
    application.include_router(lectures.router)
    monkeypatch.setattr(
        lectures.subscriptions,
        "can_watch",
        lambda conn, student_id, lecture_id: (True, 1, "Anatomy"),
    )
    return application


def client_for(api, conn):
    api.dependency_overrides[deps.get_conn] = lambda: conn
    api.dependency_overrides[deps.get_current_user] = lambda: STUDENT
    api.dependency_overrides[deps.get_current_user_streaming] = lambda: STUDENT
    return TestClient(api)


def test_bunny_lecture_is_reported_as_having_video(api):
    conn = FakeConn(lambda sql, params: [
        (1, "Anatomy", 1, None, "video-guid", 12, 900)
    ] if "FROM lectures AS l" in sql else [])
    response = client_for(api, conn).get("/api/lectures")
    assert response.status_code == 200
    assert response.json()[0]["has_video"] is True
    assert response.json()[0]["video_url"] == "/api/lectures/1/video"


def test_video_endpoint_redirects_to_highest_bunny_rendition(api, monkeypatch):
    conn = FakeConn(lambda sql, params: [
        (None, "video-guid")
    ] if "SELECT video_url, bunny_video_id" in sql else [])
    metadata = {
        "guid": "video-guid", "status": bunny.FINISHED,
        "hasMP4Fallback": True, "availableResolutions": "240p,720p",
    }
    monkeypatch.setattr(lectures.bunny, "get_video", lambda guid: metadata)
    monkeypatch.setattr(
        lectures.bunny,
        "rendition_url",
        lambda video, prefer: "https://vz-test.b-cdn.net/video-guid/play_720p.mp4",
    )
    response = client_for(api, conn).get(
        "/api/lectures/1/video", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/play_720p.mp4")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_video_endpoint_reports_bunny_encoding_in_progress(api, monkeypatch):
    conn = FakeConn(lambda sql, params: [
        (None, "video-guid")
    ] if "SELECT video_url, bunny_video_id" in sql else [])
    monkeypatch.setattr(
        lectures.bunny, "get_video",
        lambda guid: {"guid": guid, "status": 3},
    )
    response = client_for(api, conn).get("/api/lectures/1/video")
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "video_not_ready"


def test_bunny_errors_are_safe_and_do_not_expose_provider_details(api, monkeypatch):
    conn = FakeConn(lambda sql, params: [
        (None, "video-guid")
    ] if "SELECT video_url, bunny_video_id" in sql else [])
    monkeypatch.setattr(
        lectures.bunny, "get_video",
        lambda guid: (_ for _ in ()).throw(bunny.BunnyError("secret response")),
    )
    response = client_for(api, conn).get("/api/lectures/1/video")
    assert response.status_code == 502
    assert "secret response" not in response.text
