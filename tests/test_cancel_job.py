"""Cancelling one transcription job, without losing the row or the others.

    python -m rag.worker --cancel-job 3

Cancellation is a terminal `failed` rather than a status of its own, because
the table's CHECK constraint admits five statuses and it lives in the database
repository — see the note on CANCEL_SQL. What makes it terminal is the same
rule that makes an exhausted retry terminal: `attempt_count = max_attempts`,
which is exactly what the claim query refuses to pick up. That is the property
worth testing, because getting it wrong does not look like a bug: the job goes
to `failed`, looks cancelled, and is then quietly claimed and sent to a GPU
again on the worker's next pass.

The FakeConn tests below assert on what was asked of the database. The ones
taking `jobs_db` run the same operations against a real PostgreSQL, which is
what can actually prove a cancel touched one row and not five; they skip
unless TEST_DATABASE_URL is set.
"""

import pytest

from app.services import transcription_jobs
from rag import worker
from tests.fake_db import FakeConn


def job_row(
    job_id=3,
    status=transcription_jobs.PENDING,
    attempt_count=0,
    max_attempts=3,
    runpod_job_id=None,
    last_error=None,
):
    return (
        job_id, f"guid-{job_id}", 17, status, attempt_count, max_attempts,
        runpod_job_id, last_error,
    )


def responder(before, after=None):
    """Answer the SELECT ... FOR UPDATE, then the UPDATE ... RETURNING."""

    def answer(sql, params):
        text = " ".join(sql.split())

        if "FOR UPDATE" in text:
            return [before] if before else []

        if "UPDATE transcription_jobs" in text:
            row = after if after is not None else before
            # What the UPDATE sets, reflected back the way RETURNING would.
            return [(
                row[0], row[1], row[2], transcription_jobs.FAILED,
                row[5], row[5], row[6],
                transcription_jobs.CANCEL_NOTE,
            )]

        return []

    return answer


# -------------------------
# What may be cancelled
# -------------------------


def test_a_pending_job_is_cancelled():
    conn = FakeConn(responder(job_row(status="pending")))

    result = transcription_jobs.cancel(conn, 3)

    assert result["outcome"] == "cancelled"
    assert result["job"]["status"] == "failed"


@pytest.mark.parametrize("status", ["submitted", "processing"])
def test_an_in_flight_job_is_cancelled(status):
    conn = FakeConn(
        responder(job_row(status=status, runpod_job_id="runpod-9"))
    )

    result = transcription_jobs.cancel(conn, 3)

    assert result["outcome"] == "cancelled"
    assert result["was_active"] is True
    assert result["runpod_job_id"] == "runpod-9"


def test_a_failed_job_is_cancelled_so_it_stops_being_retried():
    """Its remaining attempts are what cancelling has to take away."""

    conn = FakeConn(
        responder(job_row(status="failed", attempt_count=1, last_error="boom"))
    )

    result = transcription_jobs.cancel(conn, 3)

    assert result["outcome"] == "cancelled"
    assert result["was_active"] is False


def test_a_completed_job_is_refused():
    """Its chunks are already answering questions; there is nothing to stop."""

    conn = FakeConn(responder(job_row(status="completed")))

    result = transcription_jobs.cancel(conn, 3)

    assert result["outcome"] == "already_completed"

    assert not any(
        "UPDATE transcription_jobs" in sql for sql, _ in conn.calls
    )

    # Nothing written, and the lock the read took is released.
    assert conn.committed == 0 and conn.rolled_back == 1


def test_an_unknown_job_id_is_reported_not_invented():
    conn = FakeConn(responder(None))

    result = transcription_jobs.cancel(conn, 999)

    assert result["outcome"] == "not_found"

    assert not any(
        "UPDATE transcription_jobs" in sql for sql, _ in conn.calls
    )

    assert conn.committed == 0 and conn.rolled_back == 1


# -------------------------
# What it writes
# -------------------------


def test_the_row_is_never_deleted():
    conn = FakeConn(responder(job_row()))

    transcription_jobs.cancel(conn, 3)

    assert not any("DELETE" in sql.upper() for sql, _ in conn.calls)


def test_only_the_named_job_is_touched():
    conn = FakeConn(responder(job_row()))

    transcription_jobs.cancel(conn, 3)

    updates = [
        (sql, params) for sql, params in conn.calls
        if "UPDATE transcription_jobs" in sql
    ]

    assert len(updates) == 1

    sql, params = updates[0]

    assert "WHERE id = %(job_id)s" in sql
    assert params["job_id"] == 3


def test_cancelling_exhausts_the_attempts_so_the_worker_cannot_reclaim_it():
    """`attempt_count = max_attempts` is the whole of what makes it terminal."""

    conn = FakeConn(responder(job_row()))

    transcription_jobs.cancel(conn, 3)

    sql = next(
        sql for sql, _ in conn.calls if "UPDATE transcription_jobs" in sql
    )

    assert "attempt_count = max_attempts" in sql


def test_a_completed_job_cannot_be_cancelled_even_by_a_race():
    """The guard is in the UPDATE too, not only in the read above it."""

    conn = FakeConn(responder(job_row()))

    transcription_jobs.cancel(conn, 3)

    sql = next(
        sql for sql, _ in conn.calls if "UPDATE transcription_jobs" in sql
    )

    assert "status <> %(completed)s" in sql


def test_the_row_is_read_for_update_so_a_claim_cannot_race_it():
    conn = FakeConn(responder(job_row()))

    transcription_jobs.cancel(conn, 3)

    assert any("FOR UPDATE" in sql for sql, _ in conn.calls)


def test_the_previous_error_is_kept_beside_the_cancellation():
    conn = FakeConn(
        responder(job_row(status="failed", last_error="RunPod TIMED_OUT"))
    )

    transcription_jobs.cancel(conn, 3)

    params = next(
        params for sql, params in conn.calls
        if "UPDATE transcription_jobs" in sql
    )

    assert "cancelled by operator" in params["note"]
    assert "RunPod TIMED_OUT" in params["note"]


def test_the_identifiers_are_not_cleared():
    """bunny_guid, video_id and runpod_job_id are what an audit reads later."""

    conn = FakeConn(responder(job_row(runpod_job_id="runpod-9")))

    sql = None
    transcription_jobs.cancel(conn, 3)

    sql = next(
        sql for sql, _ in conn.calls if "UPDATE transcription_jobs" in sql
    )

    for column in ("bunny_guid", "video_id", "runpod_job_id"):
        assert f"{column} =" not in sql.replace("WHERE id =", "")


# -------------------------
# The RunPod side
# -------------------------


def runpod_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker.transcribe_runpod, "cancel",
        lambda job_id: calls.append(job_id) or True,
    )
    return calls


def use_conn(monkeypatch, conn):
    """Give the worker this connection instead of a pool."""

    import contextlib

    @contextlib.contextmanager
    def connection():
        yield conn

    monkeypatch.setattr(worker, "connection", connection)


@pytest.mark.parametrize("status", ["submitted", "processing"])
def test_runpod_is_cancelled_for_a_job_it_is_still_holding(monkeypatch, status):
    conn = FakeConn(responder(job_row(status=status, runpod_job_id="runpod-9")))
    use_conn(monkeypatch, conn)
    calls = runpod_spy(monkeypatch)

    worker.cancel_job(3)

    assert calls == ["runpod-9"]


def test_runpod_is_not_called_for_a_job_that_never_reached_it(monkeypatch):
    """A pending job has no RunPod id; calling would be a wasted API round trip."""

    conn = FakeConn(responder(job_row(status="pending")))
    use_conn(monkeypatch, conn)
    calls = runpod_spy(monkeypatch)

    worker.cancel_job(3)

    assert calls == []


def test_runpod_is_not_called_for_a_job_already_finished_there(monkeypatch):
    """Failed means RunPod is done with it, whatever id the row still carries."""

    conn = FakeConn(
        responder(job_row(status="failed", attempt_count=1,
                          runpod_job_id="runpod-9"))
    )
    use_conn(monkeypatch, conn)
    calls = runpod_spy(monkeypatch)

    worker.cancel_job(3)

    assert calls == []


def test_cancelling_a_completed_job_exits_without_touching_runpod(monkeypatch):
    conn = FakeConn(responder(job_row(status="completed")))
    use_conn(monkeypatch, conn)
    calls = runpod_spy(monkeypatch)

    with pytest.raises(SystemExit) as caught:
        worker.cancel_job(3)

    assert "already completed" in str(caught.value)
    assert calls == []


def test_an_unknown_job_exits_with_a_message(monkeypatch):
    conn = FakeConn(responder(None))
    use_conn(monkeypatch, conn)
    runpod_spy(monkeypatch)

    with pytest.raises(SystemExit) as caught:
        worker.cancel_job(999)

    assert "no transcription job with id 999" in str(caught.value)


def test_the_cli_routes_cancel_job(monkeypatch):
    seen = []
    monkeypatch.setattr(worker, "cancel_job", lambda job_id: seen.append(job_id))
    monkeypatch.setattr(worker, "open_pool", lambda: None)
    monkeypatch.setattr(worker, "close_pool", lambda: None)

    worker.main(["--cancel-job", "7"])

    assert seen == [7]


# ----------------------------------------
# Against a real PostgreSQL
# ----------------------------------------


def insert_job(conn, job_id, status, attempt_count=0, runpod_job_id=None):

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO transcription_jobs
                (id, bunny_guid, video_id, status, attempt_count, max_attempts,
                 runpod_job_id)
            VALUES (%s, %s, %s, %s, %s, 3, %s)
            """,
            (job_id, f"guid-{job_id}", job_id, status, attempt_count,
             runpod_job_id),
        )

    conn.commit()


def read_job(conn, job_id):

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, attempt_count, max_attempts, bunny_guid, video_id,
                   runpod_job_id, last_error
            FROM transcription_jobs WHERE id = %s
            """,
            (job_id,),
        )
        return cur.fetchone()


def test_cancelling_leaves_every_other_job_alone(jobs_db):
    """The property FakeConn cannot check: how many rows the statement hit."""

    for job_id in (3, 5, 6, 7, 8):
        insert_job(jobs_db, job_id, transcription_jobs.PENDING)

    transcription_jobs.cancel(jobs_db, 5)

    assert read_job(jobs_db, 5)[0] == transcription_jobs.FAILED

    for job_id in (3, 6, 7, 8):
        assert read_job(jobs_db, job_id)[0] == transcription_jobs.PENDING


def test_a_cancelled_job_is_never_claimed_again(jobs_db):
    """The reason cancelling must exhaust the attempts rather than only fail."""

    insert_job(jobs_db, 3, transcription_jobs.PENDING)

    transcription_jobs.cancel(jobs_db, 3)

    assert transcription_jobs.claim_for_submission(jobs_db) is None


def test_a_cancelled_job_is_not_reclaimed_as_stale(jobs_db):
    insert_job(jobs_db, 3, transcription_jobs.SUBMITTED,
               runpod_job_id="runpod-9")

    transcription_jobs.cancel(jobs_db, 3)

    assert transcription_jobs.recover_stale(jobs_db) == []


def test_a_cancelled_job_is_no_longer_polled(jobs_db):
    insert_job(jobs_db, 3, transcription_jobs.SUBMITTED,
               runpod_job_id="runpod-9")

    transcription_jobs.cancel(jobs_db, 3)

    assert transcription_jobs.in_flight(jobs_db) == []


def test_the_row_and_its_identifiers_survive(jobs_db):
    insert_job(jobs_db, 3, transcription_jobs.SUBMITTED,
               runpod_job_id="runpod-9")

    transcription_jobs.cancel(jobs_db, 3)

    status, _, _, guid, video_id, runpod_job_id, last_error = read_job(jobs_db, 3)

    assert status == transcription_jobs.FAILED
    assert guid == "guid-3"
    assert video_id == 3
    assert runpod_job_id == "runpod-9"
    assert "cancelled by operator" in last_error


def test_a_completed_job_is_untouched_in_the_database(jobs_db):
    insert_job(jobs_db, 3, transcription_jobs.COMPLETED)

    result = transcription_jobs.cancel(jobs_db, 3)

    assert result["outcome"] == "already_completed"
    assert read_job(jobs_db, 3)[0] == transcription_jobs.COMPLETED
