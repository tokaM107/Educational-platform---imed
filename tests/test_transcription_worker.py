"""The worker: submit, settle, retry — and what must never happen twice.

Nothing here reaches RunPod, Bunny or a GPU. What is worth protecting is the
accounting: a lecture must not be submitted twice, a poll that failed to
complete must not be read as a transcription that failed, and this process must
never fetch the media itself.
"""

import pytest

from app.services import transcription_jobs
from rag import transcribe_runpod, worker
from tests.fake_db import FakeConn


def answers(**by_fragment):
    """Route a query to rows by a fragment of its SQL."""

    def answer(sql, params):
        for fragment, rows in by_fragment.items():
            if fragment in sql:
                return rows
        return []

    return answer


def a_job(**overrides):
    job = {
        "id": 1, "bunny_guid": "guid-1", "video_id": 11,
        "attempt_count": 1, "max_attempts": 3, "runpod_job_id": "rp-1",
        "submitted_seconds_ago": 30.0,
    }
    job.update(overrides)
    return job


@pytest.fixture
def no_network(monkeypatch):
    """Bunny and RunPod replaced; records what the worker tried to do."""

    calls = {"submitted": [], "ingested": [], "cancelled": []}

    monkeypatch.setattr(
        worker.bunny, "get_video",
        lambda guid: {"guid": guid, "status": 4, "length": 3600,
                      "availableResolutions": "240p", "hasMP4Fallback": True},
    )
    monkeypatch.setattr(
        worker.bunny, "audio_source_url",
        lambda video: "https://cdn.example/guid-1/play_240p.mp4",
    )
    monkeypatch.setattr(
        worker.get_settings(), "bunny_cdn_hostname", "cdn.example"
    )
    monkeypatch.setattr(
        transcribe_runpod, "submit",
        lambda url, video_id=None: calls["submitted"].append(url) or "rp-1",
    )
    monkeypatch.setattr(
        worker.ingest, "ingest_blocks",
        lambda conn, video_id, blocks: calls["ingested"].append(video_id) or 42,
    )
    monkeypatch.setattr(
        transcribe_runpod, "cancel",
        lambda job_id: calls["cancelled"].append(job_id) or True,
    )

    return calls


def record_marks(monkeypatch):
    """Capture the status transitions the worker asks for."""

    marks = []

    for name in ("mark_submitted", "mark_failed", "mark_completed",
                 "mark_processing", "touch"):
        monkeypatch.setattr(
            transcription_jobs, name,
            lambda conn, *args, _name=name: marks.append((_name, args)),
        )

    return marks


# -------------------------
# Submitting
# -------------------------


def test_a_claimed_job_is_submitted_and_its_runpod_id_recorded(
    monkeypatch, no_network
):
    """The id is persisted so a restart resumes rather than re-submits."""

    claims = iter([a_job(runpod_job_id=None)])
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: next(claims, None))
    marks = record_marks(monkeypatch)

    assert worker.submit_next(FakeConn()) is True
    assert no_network["submitted"] == ["https://cdn.example/guid-1/play_240p.mp4"]
    assert ("mark_submitted", (1, "rp-1")) in marks


def test_the_worker_never_fetches_the_media_itself(monkeypatch, no_network):
    """The VPS passes a URL. Bytes go Bunny -> RunPod, never through here."""

    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("the worker must not fetch media")

    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(requests, "post", forbidden)

    claims = iter([a_job(runpod_job_id=None)])
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: next(claims, None))
    record_marks(monkeypatch)

    assert worker.submit_next(FakeConn()) is True


def test_a_video_bunny_has_not_finished_is_not_submitted(monkeypatch, no_network):
    monkeypatch.setattr(worker.bunny, "get_video",
                        lambda guid: {"guid": guid, "status": 3})
    claims = iter([a_job(runpod_job_id=None)])
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: next(claims, None))
    marks = record_marks(monkeypatch)

    worker.submit_next(FakeConn())

    assert no_network["submitted"] == []
    assert [name for name, _ in marks] == ["mark_failed"]


def test_a_non_bunny_url_is_refused_before_a_gpu_is_started(
    monkeypatch, no_network
):
    """A URL that is not Bunny's means the catalog and config disagree."""

    monkeypatch.setattr(worker.bunny, "audio_source_url",
                        lambda video: "https://evil.test/x.mp4")
    claims = iter([a_job(runpod_job_id=None)])
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: next(claims, None))
    marks = record_marks(monkeypatch)

    worker.submit_next(FakeConn())

    assert no_network["submitted"] == []
    assert marks[0][0] == "mark_failed"


def test_an_empty_queue_submits_nothing(monkeypatch):
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: None)

    assert worker.submit_next(FakeConn()) is False


def test_a_job_queued_before_the_catalog_row_waits(monkeypatch, no_network):
    """Bunny can finish encoding before Nest writes video_ref."""

    claims = iter([a_job(video_id=None, runpod_job_id=None)])
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: next(claims, None))
    marks = record_marks(monkeypatch)

    worker.submit_next(FakeConn())

    assert no_network["submitted"] == []
    assert marks[0][0] == "mark_failed"
    assert "waiting for the catalog row" in str(marks[0][1][1])


# -------------------------
# Settling: the money branch
# -------------------------


def test_an_unreachable_runpod_leaves_the_job_in_flight(monkeypatch, no_network):
    """A poll that could not complete is not proof the transcription failed.

    The GPU may be working right now. Marking it failed here would retry a
    running job and pay for the same lecture twice.
    """

    monkeypatch.setattr(
        transcribe_runpod, "status",
        lambda job_id: (_ for _ in ()).throw(
            transcribe_runpod.RunPodUnavailable("read timed out")
        ),
    )
    marks = record_marks(monkeypatch)

    assert worker.settle_one(FakeConn(), a_job()) == "pending"
    assert marks == []


def test_a_queued_job_is_touched_so_it_is_not_reclaimed(monkeypatch, no_network):
    """A cold start can outlast nothing; the staleness timer must not fire."""

    monkeypatch.setattr(transcribe_runpod, "status",
                        lambda job_id: {"state": "IN_QUEUE"})
    marks = record_marks(monkeypatch)

    assert worker.settle_one(FakeConn(), a_job()) == "pending"
    assert [name for name, _ in marks] == ["touch"]


def test_a_running_job_moves_to_processing(monkeypatch, no_network):
    monkeypatch.setattr(transcribe_runpod, "status",
                        lambda job_id: {"state": "IN_PROGRESS"})
    marks = record_marks(monkeypatch)

    assert worker.settle_one(FakeConn(), a_job()) == "pending"
    assert [name for name, _ in marks] == ["mark_processing"]


def test_a_completed_job_is_chunked_stored_and_marked(monkeypatch, no_network):
    monkeypatch.setattr(
        transcribe_runpod, "status",
        lambda job_id: {
            "state": "COMPLETED",
            "blocks": [{"index": 0, "start_ts": 0, "end_ts": 300, "text": "نص"}],
            "metrics": {"audio_duration_seconds": 300.0,
                        "gpu_processing_seconds": 2.55, "rtfx": 117.6},
        },
    )
    marks = record_marks(monkeypatch)

    assert worker.settle_one(FakeConn(), a_job()) == "completed"
    assert no_network["ingested"] == [11]

    name, args = marks[0]
    assert name == "mark_completed"
    assert args[1] == 42
    assert args[2]["gpu_processing_seconds"] == 2.55


def test_chunks_are_stored_against_the_video_id(monkeypatch, no_network):
    """Without it the chunks exist but course-video retrieval cannot see them."""

    monkeypatch.setattr(
        transcribe_runpod, "status",
        lambda job_id: {"state": "COMPLETED", "blocks": [
            {"index": 0, "start_ts": 0, "end_ts": 300, "text": "نص"}]},
    )
    record_marks(monkeypatch)

    worker.settle_one(FakeConn(), a_job(video_id=77))

    assert no_network["ingested"] == [77]


def test_a_completed_job_with_no_video_id_does_not_store_orphan_chunks(
    monkeypatch, no_network
):
    monkeypatch.setattr(
        transcribe_runpod, "status",
        lambda job_id: {"state": "COMPLETED", "blocks": [
            {"index": 0, "start_ts": 0, "end_ts": 300, "text": "نص"}]},
    )
    marks = record_marks(monkeypatch)

    assert worker.settle_one(FakeConn(), a_job(video_id=None)) == "failed"
    assert no_network["ingested"] == []
    assert marks[0][0] == "mark_failed"


def test_a_completed_job_with_no_blocks_fails(monkeypatch, no_network):
    """Storing nothing would mark the video transcribed and unsearchable."""

    monkeypatch.setattr(transcribe_runpod, "status",
                        lambda job_id: {"state": "COMPLETED", "blocks": []})
    marks = record_marks(monkeypatch)

    assert worker.settle_one(FakeConn(), a_job()) == "failed"
    assert marks[0][0] == "mark_failed"


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "TIMED_OUT"])
def test_a_runpod_failure_is_recorded_for_retry(monkeypatch, no_network, state):
    monkeypatch.setattr(transcribe_runpod, "status",
                        lambda job_id: {"state": state, "error": "boom"})
    marks = record_marks(monkeypatch)

    assert worker.settle_one(FakeConn(), a_job()) == "failed"
    assert marks[0][0] == "mark_failed"
    assert state in str(marks[0][1][1])


def test_a_job_running_past_the_timeout_is_cancelled_and_failed(
    monkeypatch, no_network
):
    """Ours to give up on. Cancelling stops it consuming GPU seconds."""

    monkeypatch.setattr(transcribe_runpod, "status",
                        lambda job_id: {"state": "IN_PROGRESS"})
    monkeypatch.setattr(worker.get_settings(), "runpod_job_timeout_seconds", 60)
    marks = record_marks(monkeypatch)

    assert worker.settle_one(
        FakeConn(), a_job(submitted_seconds_ago=99999.0)
    ) == "failed"
    assert no_network["cancelled"] == ["rp-1"]
    assert marks[-1][0] == "mark_failed"


def test_an_unknown_runpod_state_fails_rather_than_hanging(
    monkeypatch, no_network
):
    monkeypatch.setattr(transcribe_runpod, "status",
                        lambda job_id: {"state": "WHO_KNOWS"})
    marks = record_marks(monkeypatch)

    assert worker.settle_one(FakeConn(), a_job()) == "failed"
    assert marks[0][0] == "mark_failed"


# -------------------------
# The claim contract
# -------------------------


def test_claiming_skips_rows_another_worker_holds():
    """Two workers on one queue must add throughput, not contention."""

    assert "FOR UPDATE SKIP LOCKED" in transcription_jobs.CLAIM_SQL


def test_claiming_takes_the_row_out_of_the_pending_pool_at_once():
    """Leaving it 'pending' would let a second worker submit the same lecture."""

    assert "SET status = %(submitted)s" in transcription_jobs.CLAIM_SQL


def test_an_attempt_is_counted_when_claimed_not_when_it_fails():
    """A worker killed mid-job has still used an attempt.

    Counting at failure would let a job that reliably crashes its worker be
    retried forever, because it never reaches the code that records anything.
    """

    assert "attempt_count = attempt_count + 1" in transcription_jobs.CLAIM_SQL


def test_retries_are_bounded_by_the_rows_own_limit():
    assert "attempt_count < max_attempts" in transcription_jobs.CLAIM_SQL


def test_a_completed_job_is_never_reclaimed():
    """'completed' appears in no branch of the claim predicate."""

    predicate = transcription_jobs.CLAIM_SQL.split("ORDER BY")[0]

    assert "completed" not in predicate


def test_a_terminally_failed_job_leaves_the_claimable_index():
    """The claim predicate must match idx_transcription_jobs_claimable exactly.

    If they drift, the planner stops satisfying the ORDER BY from the index and
    falls back to sorting — and terminally failed rows start accumulating in an
    index whose whole point is to exclude them.
    """

    predicate = transcription_jobs.CLAIM_SQL
    normalised = " ".join(predicate.split())

    assert (
        "WHERE status = %(pending)s "
        "OR (status = %(failed)s AND attempt_count < max_attempts)"
    ) in normalised


def test_the_claim_predicate_does_not_also_do_stale_recovery():
    """Stale recovery is its own statement so each matches one index.

    Folding it back in as a third OR-branch turns one ordered index scan into a
    BitmapOr plus a Sort. Only the row-selecting predicate is checked — the SET
    clause sets `updated_at` and is supposed to.
    """

    predicate = transcription_jobs.CLAIM_SQL.split("SELECT id", 1)[1]

    assert "updated_at" not in predicate


# -------------------------
# Stale recovery
# -------------------------


def test_stale_recovery_only_touches_jobs_nobody_is_watching():
    sql = " ".join(transcription_jobs.RECOVER_STALE_SQL.split())

    # ANY() and not IN: psycopg3 binds server-side, so a list in an IN is one
    # placeholder PostgreSQL has no grammar for. See tests/test_sql_placeholders.py.
    assert "status = ANY(%(in_flight)s)" in sql
    assert "updated_at < now() - make_interval" in sql


def test_a_reclaimed_job_goes_to_failed_so_retry_accounting_stays_in_one_place():
    """The attempt was already counted at claim time.

    Sending it back to 'pending' instead would let a job whose worker keeps
    dying be retried past max_attempts, because only the claim query checks it.
    """

    assert "SET status = %(failed)s" in transcription_jobs.RECOVER_STALE_SQL


def test_recovering_cancels_the_runpod_job_it_abandoned(monkeypatch, no_network):
    """The old worker is gone but RunPod may still be burning GPU seconds."""

    monkeypatch.setattr(
        transcription_jobs, "recover_stale",
        lambda conn: [{"id": 5, "bunny_guid": "g", "runpod_job_id": "rp-9",
                       "attempt_count": 2, "max_attempts": 3}],
    )

    assert worker.recover_stale(FakeConn())
    assert no_network["cancelled"] == ["rp-9"]


def test_recovering_a_job_that_never_reached_runpod_cancels_nothing(
    monkeypatch, no_network
):
    """Claimed, then the worker died before submitting: there is no job to stop."""

    monkeypatch.setattr(
        transcription_jobs, "recover_stale",
        lambda conn: [{"id": 5, "bunny_guid": "g", "runpod_job_id": None,
                       "attempt_count": 1, "max_attempts": 3}],
    )

    worker.recover_stale(FakeConn())

    assert no_network["cancelled"] == []


# -------------------------
# updated_at
# -------------------------


def test_every_update_sets_updated_at():
    """Stale recovery reads this column and nothing else.

    A write that forgets it freezes the row's clock, so recovery reclaims a job
    that is still running on the GPU and the lecture is billed twice. There is
    a BEFORE UPDATE trigger in the migration as a backstop; this keeps the
    statements honest on their own.
    """

    import re
    from pathlib import Path

    source = Path(transcription_jobs.__file__).read_text()

    statements = re.findall(
        r"UPDATE transcription_jobs\b.*?(?=WHERE|RETURNING)", source, re.S
    )

    assert statements, "no UPDATE statements found — did the module move?"

    for statement in statements:
        assert "updated_at = now()" in statement, (
            f"an UPDATE does not set updated_at:\n{statement}"
        )


# -------------------------
# The local (cohere) backend
# -------------------------


def test_the_cohere_backend_never_calls_runpod(monkeypatch, no_network):
    """ASR_BACKEND=cohere transcribes in-process; there is no remote job."""

    monkeypatch.setattr(worker.get_settings(), "asr_backend", "cohere")
    claims = iter([a_job(runpod_job_id=None)])
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: next(claims, None))
    monkeypatch.setattr(
        worker, "transcribe_locally",
        lambda url, chunk_seconds=None: (
            [(0, 0, 300, "نص")],
            {"audio_duration_seconds": 300.0, "gpu_processing_seconds": 2.5,
             "rtfx": 120.0},
        ),
    )
    marks = record_marks(monkeypatch)

    assert worker.submit_next(FakeConn()) is True
    assert no_network["submitted"] == []
    assert no_network["ingested"] == [11]

    name, args = marks[0]
    assert name == "mark_completed"
    assert args[2]["rtfx"] == 120.0


def test_an_unknown_backend_fails_before_spending_anything(monkeypatch, no_network):
    monkeypatch.setattr(worker.get_settings(), "asr_backend", "whisper")
    claims = iter([a_job(runpod_job_id=None)])
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: next(claims, None))
    marks = record_marks(monkeypatch)

    worker.submit_next(FakeConn())

    assert no_network["submitted"] == []
    assert marks[0][0] == "mark_failed"
    assert "unknown ASR_BACKEND" in str(marks[0][1][1])


def test_local_transcription_always_removes_its_temp_directory(monkeypatch):
    """Same discipline as the GPU worker: one directory, removed in a finally."""

    import rag.audio

    seen = {}

    def exploding_chunks(source, chunk_seconds, workspace):
        seen["workspace"] = workspace
        raise RuntimeError("ffmpeg died")
        yield  # pragma: no cover

    monkeypatch.setattr(rag.audio, "iter_audio_chunks", exploding_chunks)

    with pytest.raises(RuntimeError, match="ffmpeg died"):
        worker.transcribe_locally("https://cdn.example/x.mp4")

    import os
    assert not os.path.exists(seen["workspace"])
