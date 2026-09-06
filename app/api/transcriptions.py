"""Start a transcription from a video_id, without waiting for Bunny's webhook.

The backend team uploads to Bunny and then tells this service which catalog
video is ready. That call is the trigger; the Bunny callback in
app/api/webhooks.py remains as a second, independent one. Neither depends on
the other, and both end in the same place — one row in `transcription_jobs` —
so a video announced by both is still transcribed exactly once.

What this endpoint does NOT do is transcribe. It resolves the video, checks the
lecture is fetchable, writes or finds the queue row, and returns. rag/worker.py
submits it to RunPod and stores the transcript minutes later. An endpoint that
waited for the GPU would hold an HTTP connection open across a cold start, a
queue wait and an hour of audio, and would give its caller no way to tell a
failed transcription from a successful one whose response was lost.

Authenticated with the application's own bearer token — no second credential,
since the caller reaches this having already logged the user in — and
restricted to doctors. Starting a transcription spends GPU time, which is not
something a student account should be able to do on the platform's bill; a
student's own path to a transcript is asking a question about a lecture that
has already been through the pipeline.

The queue bounds the spend independently of who asks: a video already
transcribed is never re-run, one already in flight returns its existing job,
and re-transcribing on purpose needs an explicit `force`.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import get_conn, require_doctor
from app.schemas.transcription import TranscriptionRequest, TranscriptionResponse
from app.services import transcription_jobs


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/transcriptions", tags=["Transcription"])


# Statuses where the lecture is already on its way and a second request must
# change nothing at all.
IN_PROGRESS = (
    transcription_jobs.PENDING,
    transcription_jobs.SUBMITTED,
    transcription_jobs.PROCESSING,
)


def _video(conn, video_id):
    """The catalog video and its Bunny identifier, or the 4xx that explains why not.

    Both failures are the caller's to fix and neither should reach the GPU: a
    video that is not in the catalog, and one whose `video_ref` Nest has not
    written yet. The second is the interesting one — it is a real state, not a
    corrupt row, and answering 409 rather than 404 says "ask again later"
    instead of "this will never work".
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT id, video_provider, video_ref
            FROM course_items
            WHERE id = %s AND type = 'video'
            """,
            (video_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "video_not_found", "video_id": video_id},
        )

    _, provider, video_ref = row

    # Same rule as the playback endpoint: null means the default, which is
    # Bunny. Anything else has no Bunny guid to transcribe from.
    if provider not in (None, "bunny"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "unsupported_video_provider",
                "video_id": video_id,
                "video_provider": provider,
            },
        )

    if not video_ref:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "video_not_ready",
                "message": "the video has no Bunny reference yet",
                "video_id": video_id,
            },
        )

    return video_ref


def _response(job, video_id, queued=False):

    return TranscriptionResponse(
        video_id=video_id,
        status=job["status"],
        job_id=job["id"],
        bunny_guid=job["bunny_guid"],
        attempt_count=job["attempt_count"],
        max_attempts=job["max_attempts"],
        runpod_job_id=job["runpod_job_id"],
        chunk_count=job["chunk_count"],
        last_error=job["last_error"],
        queued=queued,
        will_retry=transcription_jobs.retryable(job),
    )


@router.post("", status_code=202, response_model=TranscriptionResponse)
def start_transcription(
    body: TranscriptionRequest,
    response: Response,
    conn=Depends(get_conn),
    current_user=Depends(require_doctor),
):
    """Queue this video for transcription, or report the job already doing it.

    Answers 202 while there is work outstanding and 200 when there is nothing
    to do because the lecture is already transcribed — so a caller can tell
    "started" from "was already done" without parsing the status string.
    """

    video_id = body.video_id
    guid = _video(conn, video_id)

    existing = transcription_jobs.job_for_guid(conn, guid)

    if existing is not None:

        # Queued by the webhook before Nest wrote video_ref: the job is real,
        # it just did not know which catalog row it belonged to. Only ever
        # filled in, never repointed.
        if existing["video_id"] is None:
            transcription_jobs.attach_video_id(conn, guid, video_id)
            existing["video_id"] = video_id

        if body.force:
            transcription_jobs.requeue(conn, guid)
            logger.info(
                "Re-queued transcription for video_id=%s guid=%s (force)",
                video_id, guid,
            )
            return _response(
                transcription_jobs.job_for_guid(conn, guid), video_id, queued=True
            )

        if existing["status"] == transcription_jobs.COMPLETED:
            # The once-only rule. Re-running costs a GPU and replaces every
            # chunk of a lecture that is already answering questions.
            response.status_code = 200
            return _response(existing, video_id)

        if existing["status"] in IN_PROGRESS:
            # The ordinary duplicate: the work is already on its way.
            logger.info(
                "Transcription for video_id=%s already %s (job=%s)",
                video_id, existing["status"], existing["id"],
            )
            return _response(existing, video_id)

        # Failed. With attempts left the worker reclaims it unprompted, so this
        # is still not a new job; out of attempts it needs `force`, which is
        # what `will_retry: false` in the response tells the caller.
        if not transcription_jobs.retryable(existing):
            response.status_code = 200

        return _response(existing, video_id)

    queued = transcription_jobs.record_ready(conn, guid, video_id)

    job = transcription_jobs.job_for_guid(conn, guid)

    if job is None:
        # record_ready inserts, so this means the row vanished between the two
        # statements. Nothing sensible to return, and a 5xx is honest.
        raise HTTPException(status_code=500, detail="could not queue the job")

    logger.info(
        "Queued transcription for video_id=%s guid=%s (job=%s, by user=%s)",
        video_id, guid, job["id"], current_user["id"],
    )

    return _response(job, video_id, queued=queued)
