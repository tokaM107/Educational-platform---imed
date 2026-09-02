"""Course-item video playback uses the modern catalog identifier."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps, videos
from tests.fake_db import FakeConn


def make_client(conn, monkeypatch):
    application = FastAPI()
    application.include_router(videos.router)
    application.dependency_overrides[deps.get_conn] = lambda: conn
    application.dependency_overrides[deps.get_current_user_streaming] = lambda: {
        "id": 2,
        "role": "student",
    }
    monkeypatch.setattr(
        videos.subscriptions,
        "can_watch_video",
        lambda conn, student_id, video_id: (True, 7, "Anatomy"),
    )
    return TestClient(application)


def test_bunny_course_item_video_redirects_to_playback(monkeypatch):
    conn = FakeConn(lambda sql, params: [("bunny", "video-guid")])
    monkeypatch.setattr(videos.bunny, "get_video", lambda guid: {
        "guid": guid,
        "status": 4,
        "availableResolutions": "720p",
    })
    monkeypatch.setattr(
        videos.bunny,
        "rendition_url",
        lambda video, prefer: "https://cdn.example/video.mp4",
    )

    response = make_client(conn, monkeypatch).get(
        "/api/videos/11/video", follow_redirects=False
    )

    assert response.status_code == 307
    assert response.headers["location"] == "https://cdn.example/video.mp4"
    assert conn.params_for("FROM course_items") == (11,)


def test_unknown_course_item_video_returns_404(monkeypatch):
    application = FastAPI()
    application.include_router(videos.router)
    application.dependency_overrides[deps.get_conn] = lambda: FakeConn()
    application.dependency_overrides[deps.get_current_user_streaming] = lambda: {
        "id": 2,
        "role": "student",
    }
    monkeypatch.setattr(
        videos.subscriptions,
        "can_watch_video",
        lambda *args: (False, None, None),
    )

    response = TestClient(application).get("/api/videos/999/video")

    assert response.status_code == 404
