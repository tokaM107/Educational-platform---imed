"""The transcription queue: one job per Bunny video, claimed by one worker.

Three processes share this module and want different halves of it. The API only
enqueues — `record_ready` is the whole of what the Bunny webhook does, and it is
deliberately cheap enough to run inside a request Bunny is timing. The worker
claims for submission, records the RunPod job id, and later settles the result.

The status lifecycle, and what moves a job along it:

    pending      queued by the webhook, nobody has submitted it
      │          worker claims it and calls RunPod /run
    submitted    RunPod has it; runpod_job_id is stored
      │          RunPod reports IN_PROGRESS
    processing   the GPU is on it
      │          RunPod reports COMPLETED and the chunks are stored
    completed    terminal, and never re-run by a webhook
      │
    failed       terminal once attempt_count reaches max_attempts;
                 claimable again below it

The once-only guarantee is not in this code. It is the UNIQUE constraint on
`bunny_guid` plus `ON CONFLICT DO NOTHING` below: a duplicate callback becomes
an insert that changes no rows, which is the same outcome as one that never
arrived. Enforcing it here instead — read, decide, insert — would leave a
window between the read and the insert where two concurrent callbacks both
decide to queue the video, which is exactly what webhook retries produce.
"""

import logging

from app.config import get_settings


logger = logging.getLogger(__name__)


PENDING = "pending"
SUBMITTED = "submitted"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"

# The two states where RunPod holds the job and this side is only waiting.
IN_FLIGHT = (SUBMITTED, PROCESSING)


# A job is claimable when nobody is working on it: never submitted, or failed
# with attempts left. `max_attempts` is read from the row, not from
# configuration, so raising the limit for one stubborn lecture does not raise it
# for every job in the table.
#
# This predicate is written to be *exactly* the predicate of
# idx_transcription_jobs_claimable, which is what lets the planner satisfy the
# ORDER BY from the index and stop at the first row.
#
# An earlier version folded stale recovery in as a third OR-branch
# (`OR (status IN ('submitted','processing') AND updated_at < …)`). Measured on
# Postgres 17, that still used the indexes — but as a BitmapOr that scanned the
# claimable index twice and then needed a Sort, because a bitmap scan returns no
# useful order. For an ORDER BY … LIMIT 1 that is the whole cost of the query.
# Splitting stale recovery into RECOVER_STALE_SQL below gives each statement a
# predicate its own index matches exactly: one ordered Index Scan, no sort.
#
# Claiming moves the row straight to 'submitted', before RunPod has actually
# been called. That is what makes the claim exclusive: leaving it 'pending'
# would let a second worker claim the same row a millisecond later and submit
# the same lecture twice. The window it opens — 'submitted' with a null
# runpod_job_id, if the worker dies between claiming and submitting — is
# closed from two directions: `in_flight` ignores rows with no RunPod id, and
# a failed submit calls `mark_failed`, which makes the row claimable again at
# once. Only an outright crash waits for `recover_stale`.
CLAIM_SQL = """
    UPDATE transcription_jobs
    SET status = %(submitted)s,
        attempt_count = attempt_count + 1,
        runpod_job_id = NULL,
        started_at = COALESCE(started_at, now()),
        updated_at = now()
    WHERE id = (
        SELECT id
        FROM transcription_jobs
        WHERE status = %(pending)s
           OR (status = %(failed)s AND attempt_count < max_attempts)
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, bunny_guid, video_id, attempt_count, max_attempts, runpod_job_id
"""


# Rows a worker claimed and then died holding. Sending them to 'failed' rather
# than straight back to 'pending' is what keeps retry accounting in one place:
# the attempt was already counted at claim time, so the claim query above
# decides whether this job has an attempt left, exactly as it does for a job
# that failed for any other reason. A crashed worker and a rejected submission
# are then the same case, and cannot drift apart.
RECOVER_STALE_SQL = """
    UPDATE transcription_jobs
    SET status = %(failed)s,
        last_error = 'abandoned: no worker reported on this job for '
                     || %(stale_minutes)s || ' minutes',
        updated_at = now()
    WHERE status IN %(in_flight)s
      AND updated_at < now() - make_interval(mins => %(stale_minutes)s)
    RETURNING id, bunny_guid, runpod_job_id, attempt_count, max_attempts
"""


# Jobs RunPod is holding, oldest first. Polled by the worker; no locking,
# because settling one is a single conditional UPDATE that is safe to race.
IN_FLIGHT_SQL = """
    SELECT id, bunny_guid, video_id, attempt_count, max_attempts, runpod_job_id,
           EXTRACT(EPOCH FROM (now() - submitted_at))
    FROM transcription_jobs
    WHERE status IN %(in_flight)s AND runpod_job_id IS NOT NULL
    ORDER BY submitted_at
    LIMIT %(limit)s
"""


def _row_to_job(row):

    return {
        "id": row[0],
        "bunny_guid": row[1],
        "video_id": row[2],
        "attempt_count": row[3],
        "max_attempts": row[4],
        "runpod_job_id": row[5],
    }


def record_ready(conn, bunny_guid, video_id=None):
    """Queue this video for transcription, unless it already is.

    Returns True if this call is the one that queued it. False means an earlier
    callback already did — the common case for a redelivery, and not an error.

    A job that already completed is left alone. That is the point: "transcribe
    once" has to survive Bunny re-sending a callback for a video transcribed
    last month, which would otherwise re-run the GPU and re-embed the whole
    lecture. Re-running on purpose is `requeue`.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            INSERT INTO transcription_jobs (bunny_guid, video_id, max_attempts)
            VALUES (%s, %s, %s)
            ON CONFLICT (bunny_guid) DO NOTHING
            RETURNING id
            """,
            (bunny_guid, video_id, get_settings().transcription_max_attempts),
        )

        row = cur.fetchone()

    conn.commit()

    return row is not None


def attach_video_id(conn, bunny_guid, video_id):
    """Fill in the catalog id for a job queued before the catalog row existed.

    Only ever sets it, never changes it: two course items claiming one Bunny
    video is a catalog mistake, and silently re-pointing the job at whichever
    was seen last would hide it.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            UPDATE transcription_jobs
            SET video_id = %s, updated_at = now()
            WHERE bunny_guid = %s AND video_id IS NULL
            """,
            (video_id, bunny_guid),
        )

    conn.commit()


def claim_for_submission(conn):
    """Take the next job needing submission, or None. Safe to run in parallel.

    `FOR UPDATE SKIP LOCKED` is what makes two workers on one queue safe: the
    second steps over the row the first is holding instead of blocking on it,
    so adding a worker adds throughput rather than contention.

    The attempt is counted at claim time, not at failure. A worker that dies
    without recording anything has still used an attempt, which is what stops a
    job that reliably kills its worker from being retried forever.
    """

    settings = get_settings()

    with conn.cursor() as cur:

        cur.execute(
            CLAIM_SQL,
            {"pending": PENDING, "submitted": SUBMITTED, "failed": FAILED},
        )

        row = cur.fetchone()

    conn.commit()

    return _row_to_job(row) if row else None


def recover_stale(conn):
    """Release jobs a worker claimed and then died holding. Returns them.

    Without this, a worker killed between submitting and settling would hold
    its row in 'submitted' forever and the lecture would never be transcribed.
    The staleness window is a crash-recovery bound, not a limit on how long a
    lecture may take — the settle pass touches every job it polls, so one that
    is genuinely still running keeps its clock fresh and is never reclaimed.
    """

    settings = get_settings()

    with conn.cursor() as cur:

        cur.execute(
            RECOVER_STALE_SQL,
            {
                "failed": FAILED,
                "in_flight": IN_FLIGHT,
                "stale_minutes": settings.transcription_stale_minutes,
            },
        )
        rows = cur.fetchall()

    conn.commit()

    return [
        {
            "id": row[0],
            "bunny_guid": row[1],
            "runpod_job_id": row[2],
            "attempt_count": row[3],
            "max_attempts": row[4],
        }
        for row in rows
    ]


def mark_submitted(conn, job_id, runpod_job_id):
    """Record that RunPod has the job, and which of its ids it is under.

    Persisted rather than held in memory because it is what lets a worker
    restart resume an in-flight lecture instead of paying the GPU for it twice.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            UPDATE transcription_jobs
            SET status = %s,
                runpod_job_id = %s,
                submitted_at = now(),
                last_error = NULL,
                updated_at = now()
            WHERE id = %s
            """,
            (SUBMITTED, runpod_job_id, job_id),
        )

    conn.commit()


def mark_processing(conn, job_id):
    """RunPod says a GPU picked it up. Also refreshes the staleness clock."""

    with conn.cursor() as cur:

        cur.execute(
            """
            UPDATE transcription_jobs
            SET status = %s, updated_at = now()
            WHERE id = %s AND status <> %s
            """,
            (PROCESSING, job_id, PROCESSING),
        )

    conn.commit()


def touch(conn, job_id):
    """Say this job is still being watched, without changing its state.

    Called on every poll of an in-flight job. Without it a lecture that
    legitimately takes longer than TRANSCRIPTION_STALE_MINUTES would be
    reclaimed and submitted a second time while the first was still running.
    """

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE transcription_jobs SET updated_at = now() WHERE id = %s",
            (job_id,),
        )

    conn.commit()


def mark_completed(conn, job_id, chunk_count, metrics=None):
    """Terminal success. `metrics` is what the GPU reported about the run."""

    metrics = metrics or {}

    with conn.cursor() as cur:

        cur.execute(
            """
            UPDATE transcription_jobs
            SET status = %s,
                chunk_count = %s,
                audio_duration_seconds = %s,
                gpu_processing_seconds = %s,
                last_error = NULL,
                completed_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (
                COMPLETED,
                chunk_count,
                metrics.get("audio_duration_seconds"),
                metrics.get("gpu_processing_seconds"),
                job_id,
            ),
        )

    conn.commit()


def mark_failed(conn, job_id, error):
    """Record why. Whether this is terminal is decided by attempt_count.

    The row goes to 'failed' either way; the claim query is what distinguishes
    "failed with attempts left" (claimable again) from "out of attempts"
    (terminal). Keeping that decision in one place means the two cannot
    disagree about when to stop.

    The message is truncated rather than stored whole: a provider error can
    arrive with a response body attached, and the useful part is at the front.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            UPDATE transcription_jobs
            SET status = %s,
                last_error = %s,
                completed_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (FAILED, str(error)[:2000], job_id),
        )

    conn.commit()


def in_flight(conn, limit=50):
    """Jobs RunPod is holding, with how long ago each was submitted."""

    with conn.cursor() as cur:

        cur.execute(IN_FLIGHT_SQL, {"in_flight": IN_FLIGHT, "limit": limit})
        rows = cur.fetchall()

    jobs = []

    for row in rows:
        job = _row_to_job(row)
        job["submitted_seconds_ago"] = float(row[6] or 0)
        jobs.append(job)

    return jobs


def requeue(conn, bunny_guid):
    """Deliberately transcribe a video again — a re-run, not a retry.

    The one way past the once-only rule, and it is manual on purpose: it
    spends GPU time and replaces the video's chunks. Wanted after a change to
    chunking or a switch of ASR, not after an ordinary failure, which the
    worker already retries by itself.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            UPDATE transcription_jobs
            SET status = %s,
                attempt_count = 0,
                max_attempts = %s,
                runpod_job_id = NULL,
                last_error = NULL,
                started_at = NULL,
                submitted_at = NULL,
                completed_at = NULL,
                updated_at = now()
            WHERE bunny_guid = %s
            """,
            (PENDING, get_settings().transcription_max_attempts, bunny_guid),
        )
        changed = cur.rowcount

    conn.commit()

    return changed > 0


def job_for_guid(conn, bunny_guid):
    """The queue row for one Bunny video, or None.

    Keyed on the guid rather than the catalog id because that is the table's
    own key, and it is the lookup that answers "is this video already queued".
    `status_for_video` asks the same question the other way round and can miss:
    a job queued by the webhook before Nest wrote `video_ref` still has a null
    video_id, and a trigger that searched by video_id would not find it and
    would try to queue the lecture a second time.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT id, bunny_guid, video_id, status, attempt_count, max_attempts,
                   runpod_job_id, last_error, chunk_count
            FROM transcription_jobs
            WHERE bunny_guid = %s
            """,
            (bunny_guid,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "bunny_guid": row[1],
        "video_id": row[2],
        "status": row[3],
        "attempt_count": row[4],
        "max_attempts": row[5],
        "runpod_job_id": row[6],
        "last_error": row[7],
        "chunk_count": row[8],
    }


def retryable(job):
    """Whether the worker will pick this failed job up again on its own.

    The same condition as the claim query's `attempt_count < max_attempts`,
    expressed once here so the API can tell a caller "it failed but is being
    retried" without duplicating the rule or guessing at it.
    """

    return (
        job["status"] == FAILED
        and job["attempt_count"] < job["max_attempts"]
    )


def status_for_video(conn, video_id):
    """What the queue knows about one catalog video, or None."""

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT bunny_guid, status, attempt_count, max_attempts, chunk_count,
                   runpod_job_id, last_error, audio_duration_seconds,
                   gpu_processing_seconds, created_at, completed_at
            FROM transcription_jobs
            WHERE video_id = %s
            """,
            (video_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "bunny_guid": row[0],
        "status": row[1],
        "attempt_count": row[2],
        "max_attempts": row[3],
        "chunk_count": row[4],
        "runpod_job_id": row[5],
        "last_error": row[6],
        "audio_duration_seconds": row[7],
        "gpu_processing_seconds": row[8],
        "created_at": row[9],
        "completed_at": row[10],
    }
