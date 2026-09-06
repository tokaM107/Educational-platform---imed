"""POST /api/transcriptions: a video_id starts exactly one transcription.

The trigger the backend calls after uploading to Bunny, replacing the webhook
as the thing that has to happen for a lecture to be transcribed. Two properties
are worth protecting here and both cost real money when they break: a repeated
call must not start a second GPU run of the same lecture, and a video that
cannot be fetched must be refused before anything is queued at all.

No database and no RunPod: FakeConn answers the two queries this endpoint makes,
and the assertions are about which rows it decided to write.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps, transcriptions
from app.services import transcription_jobs
from tests.fake_db import FakeConn


GUID = "bunny-guid-17"

USER = {"id": 7, "name": "Toqa", "email": "t@example.com", "role": "student"}


def make_client(conn, user=USER):
    """The endpoint behind the same bearer dependency the chat endpoints use."""

    application = FastAPI()
    application.include_router(transcriptions.router)
    application.dependency_overrides[deps.get_conn] = lambda: conn

    if user is not None:
        application.dependency_overrides[deps.get_current_user] = lambda: user

    return TestClient(application)


def post(conn, body=None, user=USER):
    return make_client(conn, user).post(
        "/api/transcriptions",
        json={"video_id": 17} if body is None else body,
    )


def video_row(video_ref=GUID, provider=None):
    return [(17, provider, video_ref)]


def job_row(
    status=transcription_jobs.PENDING,
    job_id=123,
    video_id=17,
    attempt_count=0,
    max_attempts=3,
    runpod_job_id=None,
    last_error=None,
    chunk_count=None,
):
    return [(
        job_id, GUID, video_id, status, attempt_count, max_attempts,
        runpod_job_id, last_error, chunk_count,
    )]


def responder(video=None, job=None, inserted=True, jobs=None):
    """Answer the endpoint's queries: the catalog lookup, then the queue.

    `jobs` takes a list to return in order, for the case where the row does not
    exist on the first read and does after the insert.
    """

    video = video_row() if video is None else video
    remaining = list(jobs) if jobs is not None else None

    def answer(sql, params):
        text = " ".join(sql.split())

        if "FROM course_items" in text:
            return video

        if "FROM transcription_jobs" in text:
            if remaining is not None:
                return remaining.pop(0) if remaining else []
            return job or []

        if "INSERT INTO transcription_jobs" in text:
            return [(123,)] if inserted else []

        return []

    return answer


# -------------------------
# Authentication
# -------------------------


def test_an_unauthenticated_request_is_refused():
    """The endpoint starts GPU work; it is not open to an anonymous caller."""

    conn = FakeConn(responder())

    # No get_current_user override, so the real bearer dependency runs and
    # finds no Authorization header.
    response = make_client(conn, user=None).post(
        "/api/transcriptions", json={"video_id": 17}
    )

    assert response.status_code == 401


# -------------------------
# Validation
# -------------------------


def test_a_missing_video_id_is_rejected():
    assert post(FakeConn(responder()), body={}).status_code == 422


@pytest.mark.parametrize("value", ["seventeen", None, 1.5, [17], {"id": 17}])
def test_a_video_id_of_the_wrong_type_is_rejected(value):
    conn = FakeConn(responder())

    assert post(conn, body={"video_id": value}).status_code == 422


@pytest.mark.parametrize("value", [0, -1])
def test_a_video_id_that_is_not_a_real_id_is_rejected(value):
    conn = FakeConn(responder())

    assert post(conn, body={"video_id": value}).status_code == 422


def test_a_video_that_is_not_in_the_catalog_is_a_404():
    conn = FakeConn(responder(video=[]))

    response = post(conn)

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "video_not_found"


def test_a_video_with_no_bunny_reference_is_refused_before_queueing():
    """Nest has not written video_ref yet: a real state, and nothing to fetch."""

    conn = FakeConn(responder(video=video_row(video_ref=None)))

    response = post(conn)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "video_not_ready"

    assert not any(
        "INSERT INTO transcription_jobs" in sql for sql, _ in conn.calls
    )


def test_a_video_on_another_provider_is_refused():
    conn = FakeConn(responder(video=video_row(provider="youtube")))

    response = post(conn)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "unsupported_video_provider"


# -------------------------
# Queueing
# -------------------------


def test_a_valid_video_id_queues_a_job():
    conn = FakeConn(responder(jobs=[[], job_row()]))

    response = post(conn)

    assert response.status_code == 202

    body = response.json()

    assert body["video_id"] == 17
    assert body["status"] == "pending"
    assert body["job_id"] == 123
    assert body["queued"] is True


def test_queueing_writes_the_guid_and_the_video_id():
    """The worker needs both: the guid to fetch, the video_id to store chunks."""

    conn = FakeConn(responder(jobs=[[], job_row()]))

    post(conn)

    inserts = [
        params for sql, params in conn.calls
        if "INSERT INTO transcription_jobs" in sql
    ]

    assert inserts and inserts[0][0] == GUID and inserts[0][1] == 17


def test_the_request_does_not_transcribe_anything_itself():
    """The endpoint returns a queue row; the GPU work belongs to the worker."""

    conn = FakeConn(responder(jobs=[[], job_row()]))

    post(conn)

    # Reading the catalog and writing the queue row, and nothing else. No
    # status transition belongs to this request: `pending -> submitted` is the
    # worker's claim, and making it here would mark a lecture as submitted that
    # nobody has handed to RunPod.
    assert not any("SET status" in sql for sql, _ in conn.calls)


# -------------------------
# Idempotency
# -------------------------


@pytest.mark.parametrize("status", ["pending", "submitted", "processing"])
def test_a_duplicate_request_returns_the_existing_job(status):
    """The lecture is already on its way; a second call must change nothing."""

    conn = FakeConn(responder(job=job_row(status=status)))

    response = post(conn)

    assert response.status_code == 202

    body = response.json()

    assert body["job_id"] == 123
    assert body["status"] == status
    assert body["queued"] is False

    assert not any(
        "INSERT INTO transcription_jobs" in sql for sql, _ in conn.calls
    )


def test_an_already_completed_video_is_not_transcribed_again():
    """Re-running spends a GPU and replaces chunks that already answer questions."""

    conn = FakeConn(
        responder(job=job_row(status="completed", chunk_count=42))
    )

    response = post(conn)

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "completed"
    assert body["chunk_count"] == 42
    assert body["queued"] is False

    assert not any(
        "INSERT INTO transcription_jobs" in sql or "SET status" in sql
        for sql, _ in conn.calls
    )


def test_a_job_queued_before_the_catalog_row_gets_its_video_id_filled_in():
    """The webhook can queue a guid Nest has not yet linked to a course item."""

    conn = FakeConn(responder(job=job_row(video_id=None)))

    response = post(conn)

    assert response.status_code == 202
    assert response.json()["video_id"] == 17

    assert any(
        "SET video_id" in sql and "video_id IS NULL" in sql
        for sql, _ in conn.calls
    )


# -------------------------
# Failure and retry
# -------------------------


def test_a_failed_job_with_attempts_left_is_left_for_the_worker():
    """It is already claimable; queueing a second row would double-submit it."""

    conn = FakeConn(
        responder(job=job_row(status="failed", attempt_count=1, last_error="boom"))
    )

    response = post(conn)

    assert response.status_code == 202

    body = response.json()

    assert body["status"] == "failed"
    assert body["will_retry"] is True
    assert body["last_error"] == "boom"

    assert not any(
        "INSERT INTO transcription_jobs" in sql for sql, _ in conn.calls
    )


def test_a_job_out_of_attempts_reports_that_it_will_not_retry():
    conn = FakeConn(
        responder(job=job_row(status="failed", attempt_count=3, max_attempts=3))
    )

    response = post(conn)

    assert response.status_code == 200
    assert response.json()["will_retry"] is False


def test_force_re_queues_a_completed_video():
    """The documented way past the once-only rule, and it is explicit."""

    conn = FakeConn(
        responder(jobs=[job_row(status="completed"), job_row(status="pending")])
    )

    response = post(conn, body={"video_id": 17, "force": True})

    assert response.status_code == 202

    body = response.json()

    assert body["status"] == "pending"
    assert body["queued"] is True

    assert any("attempt_count = 0" in sql for sql, _ in conn.calls)


def test_force_re_queues_a_job_that_ran_out_of_attempts():
    conn = FakeConn(
        responder(
            jobs=[
                job_row(status="failed", attempt_count=3, max_attempts=3),
                job_row(status="pending"),
            ]
        )
    )

    response = post(conn, body={"video_id": 17, "force": True})

    assert response.status_code == 202
    assert response.json()["status"] == "pending"


def test_without_force_a_completed_video_is_never_requeued():
    conn = FakeConn(responder(job=job_row(status="completed")))

    post(conn)

    assert not any("attempt_count = 0" in sql for sql, _ in conn.calls)


# -------------------------
# Accepted request shapes
# -------------------------


def test_the_course_item_object_is_accepted_as_it_comes():
    """The frontend forwards the item it already holds, `id` and all."""

    conn = FakeConn(responder(jobs=[[], job_row()]))

    response = post(conn, body={
        "id": 17,
        "moduleId": 91,
        "type": "video",
        "title": "Lecture 3 — Cardiac cycle",
        "videoAttached": True,
        "videoStatus": "ready",
    })

    assert response.status_code == 202
    assert response.json()["video_id"] == 17


def test_the_success_envelope_is_unwrapped():
    conn = FakeConn(responder(jobs=[[], job_row()]))

    response = post(conn, body={
        "success": True,
        "code": None,
        "message": "Video status loaded",
        "data": {"id": 17, "type": "video", "videoStatus": "ready"},
        "errors": None,
    })

    assert response.status_code == 202
    assert response.json()["video_id"] == 17


def test_force_survives_the_envelope():
    """Sent beside `data`, where a caller forwarding a response would put it."""

    conn = FakeConn(
        responder(jobs=[job_row(status="completed"), job_row(status="pending")])
    )

    response = post(conn, body={"data": {"id": 17}, "force": True})

    assert response.status_code == 202
    assert any("attempt_count = 0" in sql for sql, _ in conn.calls)


def test_video_id_still_wins_when_both_are_present():
    conn = FakeConn(responder(jobs=[[], job_row()]))

    response = post(conn, body={"video_id": 17, "id": 999})

    assert response.status_code == 202
    assert response.json()["video_id"] == 17
