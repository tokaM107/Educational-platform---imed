"""The Bunny callback: who may queue a transcription, and how often."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps, webhooks
from app.config import get_settings
from tests.fake_db import FakeConn


SECRET = "webhook-secret-long-enough-to-be-a-credential"


def configure(monkeypatch, secret=SECRET, library_id="12345"):
    """Point the endpoint at a known secret and library."""

    settings = get_settings()
    monkeypatch.setattr(settings, "bunny_webhook_secret", secret)
    monkeypatch.setattr(settings, "bunny_library_id", library_id)


def make_client(conn):
    application = FastAPI()
    application.include_router(webhooks.router)
    application.dependency_overrides[deps.get_conn] = lambda: conn
    return TestClient(application)


def post(conn, body, secret=SECRET):
    return make_client(conn).post(
        "/api/webhooks/bunny", params={"secret": secret}, json=body
    )


def finished(guid="video-guid", library_id="12345"):
    return {"VideoLibraryId": library_id, "VideoGuid": guid, "Status": 4}


# -------------------------
# Authentication
# -------------------------


def test_a_callback_without_the_secret_is_refused(monkeypatch):
    """The URL is the credential — there is no signature to fall back on."""

    configure(monkeypatch)
    conn = FakeConn()

    response = make_client(conn).post("/api/webhooks/bunny", json=finished())

    assert response.status_code == 403
    assert conn.calls == []


def test_a_wrong_secret_is_refused(monkeypatch):
    configure(monkeypatch)
    conn = FakeConn()

    assert post(conn, finished(), secret="not-the-secret").status_code == 403
    assert conn.calls == []


def test_an_unconfigured_endpoint_refuses_rather_than_accepts(monkeypatch):
    """Forgetting the variable must not read as "no authentication required"."""

    configure(monkeypatch, secret="")

    assert post(FakeConn(), finished()).status_code == 503


# -------------------------
# Which callbacks queue work
# -------------------------


def test_a_finished_video_is_queued(monkeypatch):
    configure(monkeypatch)

    # The course_items lookup, then the INSERT ... RETURNING id.
    conn = FakeConn(lambda sql, params: [(11,)] if "course_items" in sql else [(1,)])

    response = post(conn, finished())

    assert response.status_code == 202
    assert response.json() == {
        "queued": True, "bunny_guid": "video-guid", "video_id": 11
    }


def test_an_unfinished_status_queues_nothing(monkeypatch):
    """Bunny sends a callback per transition; only encoded is actionable."""

    configure(monkeypatch)
    conn = FakeConn()

    response = post(conn, {"VideoLibraryId": "12345",
                           "VideoGuid": "g", "Status": 3})

    assert response.status_code == 202
    assert response.json()["queued"] is False
    assert conn.calls == []


def test_an_uninteresting_callback_is_accepted_not_errored(monkeypatch):
    """A 4xx would teach Bunny to retry an event we deliberately ignored."""

    configure(monkeypatch)

    assert post(FakeConn(), {"VideoLibraryId": "12345",
                             "VideoGuid": "g", "Status": 0}).status_code == 202


def test_a_callback_for_another_library_is_ignored(monkeypatch):
    """One deployment serves one library; a foreign guid must not be fetched."""

    configure(monkeypatch)
    conn = FakeConn()

    response = post(conn, finished(library_id="99999"))

    assert response.status_code == 202
    assert response.json() == {"queued": False, "reason": "different_library"}
    assert conn.calls == []


def test_a_payload_without_a_guid_is_rejected(monkeypatch):
    configure(monkeypatch)

    assert post(FakeConn(), {"VideoLibraryId": "12345",
                             "Status": 4}).status_code == 400


# -------------------------
# Once, and only once
# -------------------------


def test_a_repeated_callback_does_not_queue_a_second_job(monkeypatch):
    """Bunny retries deliveries. The second one must be a no-op, not a re-run.

    The INSERT returns no row because ON CONFLICT DO NOTHING matched the
    existing job — which is how the endpoint tells "already queued" from
    "queued just now" without a read-then-write race.
    """

    configure(monkeypatch)
    conn = FakeConn(lambda sql, params: [(11,)] if "course_items" in sql else [])

    response = post(conn, finished())

    assert response.status_code == 202
    assert response.json()["queued"] is False


def test_the_insert_is_left_to_resolve_the_conflict(monkeypatch):
    """The guarantee is the UNIQUE constraint, not a check in Python."""

    configure(monkeypatch)
    conn = FakeConn(lambda sql, params: [(11,)] if "course_items" in sql else [(1,)])

    post(conn, finished())

    inserts = [sql for sql, _ in conn.calls if "INSERT INTO transcription_jobs" in sql]

    assert len(inserts) == 1
    assert "ON CONFLICT (bunny_guid) DO NOTHING" in inserts[0]


# -------------------------
# The catalog race
# -------------------------


def test_a_video_bunny_finished_before_nest_catalogued_is_still_queued(
    monkeypatch,
):
    """Encoding can finish before `video_ref` is written.

    Dropping the callback because the catalog was a few seconds behind would
    mean the video is never transcribed at all — nothing sends it again. The
    job is queued with a null video_id and the worker resolves it later.
    """

    configure(monkeypatch)
    conn = FakeConn(lambda sql, params: [] if "course_items" in sql else [(1,)])

    response = post(conn, finished())

    assert response.status_code == 202
    assert response.json() == {
        "queued": True, "bunny_guid": "video-guid", "video_id": None
    }
