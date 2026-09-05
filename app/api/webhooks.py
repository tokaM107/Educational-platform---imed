"""Bunny Stream callbacks: a finished encode becomes a queued transcription.

This endpoint does as little as it possibly can. It authenticates the caller,
decides whether the callback is interesting, writes one row, and returns — no
Bunny API call, no ffmpeg, no ASR. Everything expensive belongs to the worker
in rag/worker.py.

That division is not tidiness. Bunny gives a webhook a few seconds before it
treats the delivery as failed and retries, and the transcription of an hour of
lecture is measured in minutes; any design where the callback waits for the
work produces a retry storm and several transcriptions of one video. The queue
row is the handoff, and the UNIQUE constraint behind it is what makes the
retries harmless.

On authentication. Bunny Stream signs nothing — there is no HMAC header to
verify, so a handler that trusted the body would transcribe whatever anybody
who found the path told it to, on our bill. The secret travels in the query
string, which is why it must be a long random value and why the URL is a
credential: anyone holding it can queue jobs.
"""

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_conn
from app.config import get_settings
from app.services import transcription_jobs


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


# Bunny's own value for "encoded and playable". Duplicated from rag.bunny
# rather than imported so this module keeps no dependency on the Bunny client,
# which the API image ships but does not otherwise need here.
FINISHED = 4


def _authenticate(secret):
    """Constant-time comparison against the configured secret.

    `compare_digest` rather than `==` because a plain comparison returns as
    soon as two bytes differ, and the time it took is a measurement of how much
    of the secret was right.
    """

    expected = get_settings().bunny_webhook_secret

    if not expected:
        # Refusing is the safe reading of "unconfigured". The alternative —
        # accepting everything when no secret is set — turns forgetting an
        # environment variable into an open endpoint.
        logger.error("Bunny webhook called but BUNNY_WEBHOOK_SECRET is not set")
        raise HTTPException(status_code=503, detail="webhook not configured")

    if not secret or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=403, detail="bad webhook secret")


def _resolve_video_id(conn, guid):
    """The course_items video pointing at this Bunny guid, if there is one yet.

    Returning None is expected rather than exceptional. Nest creates the Bunny
    video and the catalog row on its own schedule, and encoding can finish
    before `video_ref` is written. The job is queued regardless and the worker
    resolves it later — dropping the callback because the catalog was a few
    seconds behind would mean the video is never transcribed at all.
    """

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


@router.post("/bunny", status_code=202)
async def bunny_video_status(
    request: Request,
    secret: str = Query(default="", description="shared webhook secret"),
    conn=Depends(get_conn),
):
    """Queue a transcription when Bunny says a video finished encoding.

    Answers 202 for everything it understood, including callbacks it chose to
    ignore. A webhook that returns an error for an uninteresting event teaches
    the sender to retry it, and Bunny sends one for every status transition —
    most of which are "still transcoding".
    """

    _authenticate(secret)

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="body is not JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body is not a JSON object")

    guid = payload.get("VideoGuid") or payload.get("videoGuid")
    status = payload.get("Status", payload.get("status"))
    library_id = payload.get("VideoLibraryId", payload.get("videoLibraryId"))

    if not guid:
        raise HTTPException(status_code=400, detail="no VideoGuid in payload")

    # One deployment reads one library. A callback for a different one is
    # either a misconfigured Bunny library pointing here or someone holding the
    # secret trying to make us fetch an unrelated video; neither should queue.
    configured_library = get_settings().bunny_library_id

    if configured_library and str(library_id) != str(configured_library):
        logger.warning(
            "Bunny webhook for foreign library %s (we serve %s), ignoring",
            library_id,
            configured_library,
        )
        return {"queued": False, "reason": "different_library"}

    if status != FINISHED:
        # Created/Uploaded/Processing/Transcoding all arrive here. There is
        # nothing to read audio from until encoding finishes.
        return {"queued": False, "reason": "not_finished", "bunny_status": status}

    video_id = _resolve_video_id(conn, guid)

    queued = transcription_jobs.record_ready(conn, guid, video_id)

    if queued:
        logger.info(
            "Queued transcription for Bunny %s (video_id=%s)", guid, video_id
        )
    else:
        # The ordinary outcome of a retried or repeated callback.
        logger.info("Bunny %s already queued or transcribed, ignoring", guid)
        if video_id is not None:
            transcription_jobs.attach_video_id(conn, guid, video_id)

    return {"queued": queued, "bunny_guid": guid, "video_id": video_id}
