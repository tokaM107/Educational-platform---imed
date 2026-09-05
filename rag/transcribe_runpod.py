"""RunPod Serverless: submit a lecture, ask later whether it is done.

The client half of gpu/handler.py. It never waits for a transcription — it
submits and returns a RunPod job id, and a separate call asks what became of
that id. The worker persists the id in `transcription_jobs.runpod_job_id`
between the two, so a worker restart resumes an in-flight lecture instead of
paying for it again.

Blocking until the GPU finished would be wrong twice over. An hour of lecture
is minutes of wall clock, so the connection would be held open across a cold
start, a queue wait and a CDN read; and when such a connection breaks there is
no way to tell a failed transcription from a successful one whose result was
lost — the difference between retrying and double-billing.

Nothing about the media passes through this process. RunPod is handed the Bunny
URL and the GPU worker fetches it directly, which is why the application server
needs no ffmpeg, no bandwidth and no GPU.

The REST shape is RunPod's documented queue-based endpoint API:

    POST {base}/{endpoint_id}/run          -> {"id": ..., "status": "IN_QUEUE"}
    GET  {base}/{endpoint_id}/status/{id}  -> {"status": ..., "output"|"error"}
    POST {base}/{endpoint_id}/cancel/{id}

with `Authorization: Bearer $RUNPOD_API_KEY`.
"""

import logging

import requests

from app.config import get_settings
from rag import media_url


logger = logging.getLogger(__name__)


# RunPod's job states, split by what this side should do about them.
PENDING_STATES = {"IN_QUEUE", "IN_PROGRESS"}
DONE_STATE = "COMPLETED"
FAILED_STATES = {"FAILED", "CANCELLED", "TIMED_OUT"}


class RunPodError(Exception):
    """RunPod refused, or returned something the pipeline cannot use."""


class RunPodUnavailable(RunPodError):
    """RunPod could not be reached. Says nothing about the transcription.

    Separate from RunPodError on purpose, and the distinction is the one that
    keeps the bill down: a poll that could not complete is not evidence that
    the job failed. The GPU may well be transcribing right now. The worker
    leaves the job alone and asks again rather than retrying it.
    """


def _endpoint():

    return get_settings().require_runpod()


def _headers(api_key):

    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _request(method, path, api_key, base, **kwargs):
    """One call to RunPod, with the two failure kinds kept apart."""

    timeout = get_settings().runpod_request_timeout_seconds

    try:
        response = requests.request(
            method, f"{base}/{path}", headers=_headers(api_key),
            timeout=timeout, **kwargs
        )
    except requests.RequestException as error:
        # Connection refused, DNS, read timeout. Unreachable, not failed.
        raise RunPodUnavailable(f"could not reach RunPod: {error}") from error

    if response.status_code in (429, 500, 502, 503, 504):
        # RunPod's own trouble, not this job's. Same reasoning as above.
        raise RunPodUnavailable(
            f"RunPod returned HTTP {response.status_code} for {path}"
        )

    if response.status_code >= 400:
        raise RunPodError(
            f"RunPod {path} failed: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )

    try:
        return response.json()
    except ValueError as error:
        raise RunPodError(f"RunPod {path} returned a body that is not JSON") from error


def submit(bunny_url, video_id=None, chunk_seconds=None):
    """Queue one lecture on the serverless endpoint. Returns the RunPod job id.

    The URL is checked here as well as on the worker. This side refuses early
    so a bad catalog row cannot start a GPU at all; the worker refuses again
    because it cannot assume its caller was this code.
    """

    settings = get_settings()
    base, api_key = _endpoint()

    media_url.check(bunny_url, settings.bunny_media_hosts())

    payload = {"bunny_url": bunny_url}

    if video_id is not None:
        payload["video_id"] = video_id

    if chunk_seconds:
        payload["chunk_seconds"] = chunk_seconds

    body = _request("POST", "run", api_key, base, json={"input": payload})

    job_id = body.get("id")

    if not job_id:
        raise RunPodError(f"RunPod /run returned no job id: {str(body)[:200]}")

    logger.info(
        "RunPod: submitted video_id=%s as job %s (status=%s)",
        video_id, job_id, body.get("status"),
    )

    return job_id


def status(runpod_job_id):
    """Ask RunPod what became of a submitted job.

    Returns a dict with `state` (RunPod's own string), plus `blocks` when it
    completed and `error` when it did not.
    """

    base, api_key = _endpoint()

    body = _request("GET", f"status/{runpod_job_id}", api_key, base)

    state = body.get("status")

    result = {
        "state": state,
        "delay_ms": body.get("delayTime"),
        "execution_ms": body.get("executionTime"),
    }

    if state == DONE_STATE:

        output = body.get("output") or {}

        if isinstance(output, list):
            # A generator handler aggregates into a list; ours does not, but
            # reading the first element is cheaper than a confusing failure.
            output = output[0] if output else {}

        result["blocks"] = output.get("blocks") or []
        result["metrics"] = {
            key: output.get(key)
            for key in ("audio_duration_seconds", "gpu_processing_seconds", "rtfx")
            if output.get(key) is not None
        }

    if state in FAILED_STATES:
        result["error"] = str(body.get("error") or state)[:2000]

    return result


def cancel(runpod_job_id):
    """Stop a job we have given up on, so it stops costing GPU seconds."""

    base, api_key = _endpoint()

    try:
        _request("POST", f"cancel/{runpod_job_id}", api_key, base)
    except RunPodError as error:
        # Best effort: the job may already have finished, and failing to
        # cancel must not turn into a failure of whatever asked us to.
        logger.warning("RunPod: could not cancel %s: %s", runpod_job_id, error)
        return False

    return True


def to_block_tuples(blocks):
    """The handler's JSON blocks -> (index, start_ts, end_ts, text) tuples.

    Validated rather than trusted: a block missing its timestamps would be
    written as a header of zeros, which does not fail — it silently points
    every citation in that stretch of lecture at the start of the video.
    """

    out = []

    for block in blocks:

        try:
            index = int(block["index"])
            start_ts = int(block["start_ts"])
            end_ts = int(block["end_ts"])
        except (KeyError, TypeError, ValueError) as error:
            raise RunPodError(
                f"malformed block from the GPU worker: {str(block)[:200]}"
            ) from error

        # chunk_block divides by the block's duration.
        out.append(
            (index, start_ts, max(end_ts, start_ts + 1), block.get("text", ""))
        )

    return sorted(out)
