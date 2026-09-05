"""The transcription worker: submit queued Bunny videos, settle finished ones.

    python -m rag.worker                  # keep running: submit and poll
    python -m rag.worker --once           # one pass over both, then exit
    python -m rag.worker --video-id 11    # re-run one video deliberately
    python -m rag.worker --status         # what the queue is doing

This is the half of the pipeline the API cannot do. app/api/webhooks.py writes a
row when Bunny says a video finished encoding; this process turns that row into
transcript chunks a student's question can be answered from.

It does that in two passes rather than one, and never holds a connection open
while a GPU works:

  submit   claim a pending job, hand RunPod the Bunny URL, store the RunPod job
           id against the row, and move on. Seconds.
  settle   for each job RunPod is holding, ask what became of it. Completed
           ones are chunked, embedded and stored; failed ones are retried while
           attempts remain.

Persisting `runpod_job_id` between the two is what makes a worker restart free:
the next pass finds the row, asks RunPod, and collects a transcript that was
produced while nothing was watching. A design that waited in-process would
instead re-submit the lecture and pay for it twice.

The media never touches this machine. RunPod is given a Bunny URL and the GPU
worker fetches it, so this process needs no ffmpeg, no disk for hour-long
lectures, and no GPU. Only the transcript text comes back, and it goes from the
HTTP response into the chunker without becoming a file.

Nothing here is on the path of a student's question. app/services/retrieval.py
is a pgvector lookup over what this worker already stored: once a video has been
through here, every question about it forever after is a database query.
"""

import argparse
import logging
import time

import requests

from app.config import get_settings
from app.db import close_pool, connection, open_pool
from app.services import transcription_jobs
from rag import bunny, ingest, media_url, transcribe_runpod


logger = logging.getLogger(__name__)


# How long to wait when there is nothing to submit and nothing in flight. The
# webhook is what makes transcription prompt, so this only bounds how late a
# job held back by a missing catalog row starts.
IDLE_SECONDS = 30


class JobError(Exception):
    """This job cannot proceed. The message is recorded against the row."""


# -------------------------
# Working out what to send
# -------------------------


def resolve_video_id(conn, guid):
    """The course_items video for this guid, or None if Nest has not written it."""

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT id FROM course_items
            WHERE video_ref = %s AND type = 'video'
            """,
            (guid,),
        )
        row = cur.fetchone()

    return row[0] if row else None


def media_url_for(guid):
    """The Bunny URL the GPU worker should read, checked before it is sent.

    The smallest rendition: every one carries the same soundtrack, and the
    picture is discarded the moment ffmpeg has read it. This process does not
    fetch it — the URL is passed to RunPod and the GPU worker reads it there.
    """

    try:
        video = bunny.get_video(guid)
    except (bunny.BunnyError, requests.RequestException) as error:
        raise JobError(f"could not read Bunny video {guid}: {error}") from error

    if not bunny.is_finished(video):
        raise JobError(f"Bunny {guid} is {bunny.status_name(video)}, not Finished")

    source_url = bunny.audio_source_url(video)

    try:
        media_url.check(source_url, get_settings().bunny_media_hosts())
    except media_url.UntrustedMediaURL as error:
        # Refused here rather than at the GPU: a URL that is not Bunny's means
        # BUNNY_STREAM_CDN_HOSTNAME and the catalog disagree, and starting a
        # GPU to find that out costs money.
        raise JobError(f"refusing to transcribe {guid}: {error}") from error

    return source_url, video.get("length") or 0


# -------------------------
# Pass one: submit
# -------------------------


def submit_next(conn):
    """Claim one job and hand it to RunPod. True if there was one."""

    job = transcription_jobs.claim_for_submission(conn)

    if job is None:
        return False

    video_id = job["video_id"]
    guid = job["bunny_guid"]

    try:
        # Queued before the catalog row existed, which is normal: Bunny can
        # finish encoding before Nest writes video_ref. Resolving here is what
        # lets that ordering work itself out.
        if video_id is None:

            video_id = resolve_video_id(conn, guid)

            if video_id is None:
                raise JobError(
                    f"no course_items video has video_ref = {guid} yet — "
                    "waiting for the catalog row"
                )

            transcription_jobs.attach_video_id(conn, guid, video_id)

        source_url, length = media_url_for(guid)

        runpod_job_id = transcribe_runpod.submit(source_url, video_id=video_id)

    except Exception as error:
        logger.warning(
            "job=%s guid=%s attempt=%s/%s submit failed: %s",
            job["id"], guid, job["attempt_count"], job["max_attempts"], error,
        )
        transcription_jobs.mark_failed(conn, job["id"], error)
        return True

    transcription_jobs.mark_submitted(conn, job["id"], runpod_job_id)

    logger.info(
        "job=%s guid=%s video_id=%s attempt=%s/%s runpod_job=%s length=%ss submitted",
        job["id"], guid, video_id, job["attempt_count"], job["max_attempts"],
        runpod_job_id, length,
    )

    return True


# -------------------------
# Pass two: settle
# -------------------------


def settle_one(conn, job):
    """Ask RunPod about one in-flight job and act on the answer.

    Returns "pending", "completed" or "failed" — what this poll concluded, not
    what the job will eventually be.
    """

    settings = get_settings()
    runpod_job_id = job["runpod_job_id"]

    try:
        result = transcribe_runpod.status(runpod_job_id)

    except transcribe_runpod.RunPodUnavailable as error:
        # The single most important branch in this file. A poll that could not
        # complete says nothing about the transcription — the GPU may be
        # working on it right now. Treating this as a failure would retry a
        # running job and pay for the same lecture twice.
        logger.info(
            "job=%s runpod_job=%s poll unavailable, leaving in flight: %s",
            job["id"], runpod_job_id, error,
        )
        return "pending"

    except transcribe_runpod.RunPodError as error:
        transcription_jobs.mark_failed(conn, job["id"], error)
        return "failed"

    state = result.get("state")

    if state in transcribe_runpod.PENDING_STATES:

        if state == "IN_PROGRESS":
            transcription_jobs.mark_processing(conn, job["id"])
        else:
            # Still queued. Touching it keeps the staleness timer from
            # reclaiming a lecture that is only waiting for a cold start.
            transcription_jobs.touch(conn, job["id"])

        if job["submitted_seconds_ago"] > settings.runpod_job_timeout_seconds:
            # Ours to give up on, not RunPod's. Cancel first so an abandoned
            # job stops consuming GPU seconds we will not use the result of.
            transcribe_runpod.cancel(runpod_job_id)
            transcription_jobs.mark_failed(
                conn, job["id"],
                f"gave up after {int(job['submitted_seconds_ago'])}s "
                f"(RunPod still {state})",
            )
            return "failed"

        return "pending"

    if state in transcribe_runpod.FAILED_STATES:
        logger.warning(
            "job=%s runpod_job=%s attempt=%s/%s RunPod %s: %s",
            job["id"], runpod_job_id, job["attempt_count"], job["max_attempts"],
            state, result.get("error"),
        )
        transcription_jobs.mark_failed(
            conn, job["id"], f"RunPod {state}: {result.get('error')}"
        )
        return "failed"

    if state != transcribe_runpod.DONE_STATE:
        transcription_jobs.mark_failed(
            conn, job["id"], f"unknown RunPod status {state!r}"
        )
        return "failed"

    # Completed. Everything from here is this machine's own work on text.
    try:
        blocks = transcribe_runpod.to_block_tuples(result.get("blocks") or [])

        if not blocks:
            raise JobError("RunPod completed but returned no transcript blocks")

        if job["video_id"] is None:
            raise JobError("cannot store chunks: the job has no video_id")

        started = time.monotonic()
        count = ingest.ingest_blocks(conn, job["video_id"], blocks)

    except Exception as error:
        logger.warning("job=%s ingest failed: %s", job["id"], error)
        transcription_jobs.mark_failed(conn, job["id"], error)
        return "failed"

    metrics = result.get("metrics") or {}

    transcription_jobs.mark_completed(conn, job["id"], count, metrics)

    logger.info(
        "job=%s video_id=%s runpod_job=%s completed: %d chunks, "
        "audio=%ss gpu=%ss rtfx=%s ingest=%.1fs",
        job["id"], job["video_id"], runpod_job_id, count,
        metrics.get("audio_duration_seconds"),
        metrics.get("gpu_processing_seconds"),
        metrics.get("rtfx"),
        time.monotonic() - started,
    )

    return "completed"


def settle_in_flight(conn):
    """Poll every job RunPod is holding. Returns how many were still pending."""

    jobs = transcription_jobs.in_flight(conn)

    pending = 0

    for job in jobs:
        if settle_one(conn, job) == "pending":
            pending += 1

    return pending


# -------------------------
# Loops
# -------------------------


def recover_stale(conn):
    """Release jobs whose worker died, and stop their GPU work if any."""

    released = transcription_jobs.recover_stale(conn)

    for job in released:
        logger.warning(
            "job=%s guid=%s runpod_job=%s attempt=%s/%s reclaimed: "
            "no worker reported on it",
            job["id"], job["bunny_guid"], job["runpod_job_id"],
            job["attempt_count"], job["max_attempts"],
        )

        # The worker that owned this is gone, but RunPod may still be running
        # the job it submitted. Cancelling stops GPU seconds nobody will
        # collect the result of; the retry submits a fresh one.
        if job["runpod_job_id"]:
            transcribe_runpod.cancel(job["runpod_job_id"])

    return released


def run_once(conn):
    """One recovery, submit and settle pass. True if anything happened."""

    # First, so a job abandoned by a dead worker is claimable in this same
    # pass rather than waiting for the next one.
    released = recover_stale(conn)

    worked = bool(released)

    # Submitting before settling: a cold RunPod worker starts sooner if the
    # job is queued before we spend time polling the ones already running.
    while submit_next(conn):
        worked = True

    return bool(settle_in_flight(conn)) or worked


def run_forever(idle_seconds=IDLE_SECONDS):

    settings = get_settings()

    logger.info(
        "Transcription worker started (backend=%s endpoint=%s poll=%ss)",
        settings.asr_backend,
        settings.runpod_endpoint_id or "unset",
        settings.runpod_poll_interval_seconds,
    )

    while True:

        with connection() as conn:
            busy = run_once(conn)

        # Poll briskly while RunPod holds something, idle slowly otherwise.
        time.sleep(
            settings.runpod_poll_interval_seconds if busy else idle_seconds
        )


def drain(timeout_seconds=None):
    """Submit everything and wait for it to finish. Returns settled job count.

    For CI and for `--video-id`: the polling loop, but with an end.
    """

    settings = get_settings()
    deadline = time.monotonic() + (
        timeout_seconds or settings.runpod_job_timeout_seconds
    )

    while True:

        with connection() as conn:
            run_once(conn)
            remaining = len(transcription_jobs.in_flight(conn))

        if not remaining:
            return 0

        if time.monotonic() > deadline:
            logger.warning("%d job(s) still in flight at the deadline", remaining)
            return remaining

        time.sleep(settings.runpod_poll_interval_seconds)


def run_one_video(video_id):
    """Transcribe one video again, whatever the queue says.

    The manual path, for a video whose webhook never arrived or one being
    re-done deliberately. It bypasses the once-only rule, which is why it is a
    flag somebody types rather than something the worker decides.
    """

    with connection() as conn:

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT video_ref FROM course_items
                WHERE id = %s AND type = 'video'
                """,
                (video_id,),
            )
            row = cur.fetchone()

        if row is None:
            raise SystemExit(f"no course-item video with id {video_id}")

        if not row[0]:
            raise SystemExit(f"video {video_id} has no video_ref")

        transcription_jobs.record_ready(conn, row[0], video_id)
        transcription_jobs.requeue(conn, row[0])

    remaining = drain()

    with connection() as conn:
        state = transcription_jobs.status_for_video(conn, video_id)

    if state and state["status"] == transcription_jobs.COMPLETED:
        print(f"Stored {state['chunk_count']} chunks for video {video_id}")
        return

    raise SystemExit(
        f"video {video_id} did not complete: "
        f"{(state or {}).get('status')} {(state or {}).get('last_error') or ''}"
    )


def print_status():
    """What the queue is doing, for an operator with a stuck video."""

    with connection() as conn:

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, count(*)
                FROM transcription_jobs GROUP BY status ORDER BY status
                """
            )
            for status, count in cur.fetchall():
                print(f"{status:<12} {count}")

            cur.execute(
                """
                SELECT id, bunny_guid, video_id, status, attempt_count,
                       max_attempts, runpod_job_id, left(last_error, 120)
                FROM transcription_jobs
                WHERE status <> 'completed'
                ORDER BY created_at
                LIMIT 20
                """
            )
            rows = cur.fetchall()

    if rows:
        print("\nunfinished:")
        for row in rows:
            print(
                f"  job={row[0]} guid={row[1]} video_id={row[2]} {row[3]} "
                f"attempt={row[4]}/{row[5]} runpod={row[6]}\n"
                f"    {row[7] or ''}"
            )


def parse_args(argv=None):

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--once", action="store_true",
        help="submit and settle once, then exit, instead of looping",
    )
    parser.add_argument(
        "--video-id", type=int,
        help="re-transcribe this video now, ignoring the once-only rule",
    )
    parser.add_argument(
        "--status", action="store_true", help="print the queue and exit"
    )
    parser.add_argument(
        "--idle-seconds", type=int, default=IDLE_SECONDS,
        help="how long to sleep when there is nothing to do",
    )

    return parser.parse_args(argv)


def main(argv=None):

    args = parse_args(argv)

    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    open_pool()

    try:
        if args.status:
            print_status()

        elif args.video_id:
            run_one_video(args.video_id)

        elif args.once:
            with connection() as conn:
                run_once(conn)

        else:
            run_forever(args.idle_seconds)

    except KeyboardInterrupt:
        logger.info("Stopping.")

    finally:
        close_pool()


if __name__ == "__main__":
    main()
