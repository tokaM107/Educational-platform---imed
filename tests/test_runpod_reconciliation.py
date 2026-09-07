"""Collecting a finished RunPod job instead of running it again.

Job 10 (video 29) sat at status=submitted attempt=2/3 with a RunPod id whose
job had *completed successfully* — and the same video had already been
transcribed once before under a different RunPod id. The worker had thrown away
a finished transcription and paid for another.

The cause was ordering plus a guess. run_once recovered before it settled, and
recovery failed any in-flight row whose worker had been quiet for too long,
cancelled its RunPod job, and let the next claim submit a new one. Nothing had
asked RunPod what became of the first, and RunPod knew: it was done.

So the rule these tests hold in place is: a row carrying a runpod_job_id
belongs to RunPod, and only RunPod's answer moves it.
"""

import pytest

from app.services import transcription_jobs
from rag import worker
from tests.fake_db import FakeConn


def a_job(job_id=10, video_id=29, runpod_job_id="bd550646-…-e2",
          attempt_count=2, seconds_ago=10):
    return {
        "id": job_id, "bunny_guid": f"guid-{video_id}", "video_id": video_id,
        "attempt_count": attempt_count, "max_attempts": 3,
        "runpod_job_id": runpod_job_id, "submitted_seconds_ago": seconds_ago,
    }


COMPLETED = {
    "state": "COMPLETED",
    "blocks": [{"index": 0, "start_ts": 0, "end_ts": 37, "text": "مرحبا"}],
    "metrics": {"audio_duration_seconds": 37, "gpu_processing_seconds": 17.2},
}


@pytest.fixture
def runpod(monkeypatch):
    """RunPod, with what it was asked recorded."""

    state = {"status": COMPLETED, "submitted": [], "cancelled": [], "polled": []}

    def status(job_id):
        state["polled"].append(job_id)
        return state["status"]

    monkeypatch.setattr(worker.transcribe_runpod, "status", status)
    monkeypatch.setattr(
        worker.transcribe_runpod, "submit",
        lambda url, video_id=None, chunk_seconds=None:
            state["submitted"].append(video_id) or "rp-new",
    )
    monkeypatch.setattr(
        worker.transcribe_runpod, "cancel",
        lambda job_id: state["cancelled"].append(job_id) or True,
    )

    return state


@pytest.fixture
def marks(monkeypatch):
    recorded = []

    for name in ("mark_submitted", "mark_failed", "mark_completed",
                 "mark_processing", "touch", "defer"):
        monkeypatch.setattr(
            transcription_jobs, name,
            lambda conn, *args, _name=name, **kw: recorded.append((_name, args)),
        )

    return recorded


@pytest.fixture
def ingested(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker.ingest, "ingest_blocks",
        lambda conn, video_id, blocks: calls.append((video_id, blocks)) or len(blocks),
    )
    return calls


# ----------------------------------------
# A completed RunPod job is collected
# ----------------------------------------


def test_a_completed_runpod_job_is_ingested_and_marked_completed(
    runpod, marks, ingested
):
    assert worker.settle_one(FakeConn(), a_job()) == "completed"

    assert ingested and ingested[0][0] == 29
    assert [name for name, _ in marks] == ["mark_completed"]


def test_a_completed_job_is_never_resubmitted(runpod, marks, ingested):
    worker.settle_one(FakeConn(), a_job())

    assert runpod["submitted"] == []


def test_collecting_does_not_cancel_the_job_it_collected(runpod, marks, ingested):
    worker.settle_one(FakeConn(), a_job())

    assert runpod["cancelled"] == []


@pytest.mark.parametrize("state", ["IN_QUEUE", "IN_PROGRESS"])
def test_a_running_job_is_left_alone(runpod, marks, ingested, state):
    runpod["status"] = {"state": state}

    assert worker.settle_one(FakeConn(), a_job()) == "pending"

    assert runpod["submitted"] == []
    assert runpod["cancelled"] == []
    assert not ingested


# ----------------------------------------
# Staleness must not resubmit
# ----------------------------------------


def test_a_stale_submitted_job_is_settled_before_anything_resubmits(
    monkeypatch, runpod, ingested
):
    """The exact sequence that lost job 10's transcript."""

    order = []

    monkeypatch.setattr(
        transcription_jobs, "in_flight",
        lambda conn, limit=50: order.append("settle") or [a_job()],
    )
    monkeypatch.setattr(
        transcription_jobs, "recover_stale",
        lambda conn: order.append("recover") or [],
    )
    monkeypatch.setattr(
        transcription_jobs, "claim_for_submission",
        lambda conn: order.append("claim") or None,
    )
    for name in ("mark_completed", "mark_failed", "touch", "mark_processing"):
        monkeypatch.setattr(transcription_jobs, name, lambda *a, **k: None)

    worker.run_once(FakeConn())

    assert order[0] == "settle"
    assert order.index("settle") < order.index("recover") < order.index("claim")


def test_recovery_leaves_rows_that_runpod_is_holding(monkeypatch):
    """The SQL, not the Python: a row with a RunPod id is not selectable."""

    sql = " ".join(transcription_jobs.RECOVER_STALE_SQL.split())

    assert "runpod_job_id IS NULL" in sql


def test_recovery_cancels_nothing(monkeypatch, runpod):
    monkeypatch.setattr(
        transcription_jobs, "recover_stale",
        lambda conn: [{"id": 5, "bunny_guid": "g", "runpod_job_id": None,
                       "attempt_count": 1, "max_attempts": 3}],
    )

    worker.recover_stale(FakeConn())

    assert runpod["cancelled"] == []


# ----------------------------------------
# Attempts move only for real attempts
# ----------------------------------------


def test_polling_a_running_job_does_not_touch_the_attempt_count(
    runpod, marks, ingested
):
    runpod["status"] = {"state": "IN_PROGRESS"}

    worker.settle_one(FakeConn(), a_job())

    assert [name for name, _ in marks] == ["mark_processing"]


def test_collecting_a_result_does_not_touch_the_attempt_count(
    runpod, marks, ingested
):
    worker.settle_one(FakeConn(), a_job())

    assert "mark_failed" not in [name for name, _ in marks]


def test_one_pass_handles_a_job_once_even_when_it_fails(monkeypatch, runpod):
    """A failed job is claimable again at once; a pass must not take it back.

    Otherwise a single tick spends all three attempts before RunPod has been
    polled even once — which is how a job reaches attempt 3 in a second.
    """

    claimed = []

    def claim(conn):
        claimed.append(len(claimed))
        return {"id": 7, "bunny_guid": "g", "video_id": 1,
                "attempt_count": len(claimed), "max_attempts": 3,
                "runpod_job_id": None}

    monkeypatch.setattr(transcription_jobs, "claim_for_submission", claim)
    monkeypatch.setattr(transcription_jobs, "in_flight", lambda conn, limit=50: [])
    monkeypatch.setattr(transcription_jobs, "recover_stale", lambda conn: [])
    monkeypatch.setattr(transcription_jobs, "mark_failed", lambda *a, **k: None)
    monkeypatch.setattr(
        worker.bunny, "get_video",
        lambda guid: (_ for _ in ()).throw(worker.JobError("boom")),
    )

    worker.run_once(FakeConn())

    assert len(claimed) == 2  # the job, then the repeat that stops the loop


# ----------------------------------------
# Ingest happens once
# ----------------------------------------


def test_ingest_replaces_this_videos_chunks_rather_than_adding_to_them():
    """What makes collecting a result twice harmless."""

    import inspect

    from rag import ingest

    source = inspect.getsource(ingest.replace_chunks)

    assert "DELETE FROM transcript_chunks WHERE video_id" in source


def test_settling_the_same_completed_job_twice_stores_one_set(
    runpod, marks, ingested
):
    job = a_job()

    worker.settle_one(FakeConn(), job)
    worker.settle_one(FakeConn(), job)

    # Both passes ingest, and both ingest the same one block for the same
    # video — replace_chunks makes the second a no-op in effect.
    assert [video_id for video_id, _ in ingested] == [29, 29]
    assert all(len(blocks) == 1 for _, blocks in ingested)


def test_a_completed_row_is_not_polled_again(jobs_db):
    """in_flight only returns submitted/processing, so a collected job leaves."""

    with jobs_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO transcription_jobs
                (id, bunny_guid, video_id, status, attempt_count, max_attempts,
                 runpod_job_id, submitted_at)
            VALUES (10, 'guid-29', 29, 'submitted', 2, 3, 'rp-1', now())
            """
        )
    jobs_db.commit()

    assert [job["id"] for job in transcription_jobs.in_flight(jobs_db)] == [10]

    transcription_jobs.mark_completed(jobs_db, 10, 1)

    assert transcription_jobs.in_flight(jobs_db) == []


def test_a_job_runpod_is_holding_is_never_reclaimed_as_stale(jobs_db):
    """The database half of the fix, against the real predicate."""

    with jobs_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO transcription_jobs
                (id, bunny_guid, video_id, status, attempt_count, max_attempts,
                 runpod_job_id, updated_at)
            VALUES
                (10, 'guid-29', 29, 'submitted', 2, 3, 'rp-1',
                 now() - interval '10 days'),
                (11, 'guid-30', 30, 'submitted', 1, 3, NULL,
                 now() - interval '10 days')
            """
        )
    jobs_db.commit()

    released = [job["id"] for job in transcription_jobs.recover_stale(jobs_db)]

    # Only the one that never reached RunPod.
    assert released == [11]


def test_deferring_gives_the_attempt_back(jobs_db):
    """Waiting for Bunny or the catalog must not spend a retry."""

    with jobs_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO transcription_jobs
                (id, bunny_guid, video_id, status, attempt_count, max_attempts)
            VALUES (12, 'guid-31', 31, 'submitted', 1, 3)
            """
        )
    jobs_db.commit()

    assert transcription_jobs.defer(jobs_db, 12, "still transcoding") == 0

    claimed = transcription_jobs.claim_for_submission(jobs_db)

    assert claimed["id"] == 12
    assert claimed["attempt_count"] == 1
