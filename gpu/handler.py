"""RunPod Serverless handler: one Bunny lecture in, one transcript out.

This is the only process in the system that loads the ASR model, so it is the
only one that needs a GPU, torch, transformers or ffmpeg. It runs on RunPod
Serverless with a minimum of zero workers: the GPU starts when a job arrives
and stops when the queue empties, so nothing is billed between lectures.

    runpod.serverless.start({"handler": handler})

The model is loaded at the worker's start, not inside the handler. RunPod keeps
a worker warm for a while after a job, and a warm worker must not pay the
several-GB load again — that cost belongs to the worker's start, where RunPod's
own cold start already accounts for it.

The weights come from a RunPod Cached Model on /runpod-volume, not from this
image and not from Hugging Face at runtime: the image carries code and
dependencies only, holds no Hugging Face token, and runs with HF_HUB_OFFLINE=1
so a missing file fails loudly instead of turning into a silent download from a
worker that has no credentials to do it with.

Media lifecycle, which is the part worth being strict about:

    /tmp/transcription_jobs/<job_id>/     created per job
        audio/chunk_000.wav               written, transcribed, deleted
    ...                                   removed in a finally, always

Nothing survives a job. The lecture stays in Bunny, which is the permanent
copy, so a failed job has nothing worth preserving locally — a retry fetches
from Bunny again. RunPod's container filesystem is ephemeral anyway, but a
warm worker handling lecture after lecture would fill it within a shift if
each job left its audio behind.
"""

import logging
import os
import shutil
import time
from pathlib import Path

from rag import media_url
from rag.audio import CHUNK_SECONDS, AudioError, iter_audio_chunks


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


# Everything this worker writes lives under here, one directory per job.
WORK_ROOT = Path(os.getenv("TRANSCRIPTION_WORK_ROOT", "/tmp/transcription_jobs"))

# Which hosts a job may point at. Comma-separated so the endpoint can serve
# more than one pull zone without a rebuild. The worker enforces this itself
# rather than trusting its caller: the endpoint id is not a secret, and anyone
# who can submit a job could otherwise make this GPU fetch an internal address.
ALLOWED_MEDIA_HOSTS = {
    host.strip().lower()
    for host in os.getenv("BUNNY_MEDIA_HOSTS", "video.bunnycdn.com").split(",")
    if host.strip()
}


class JobInputError(Exception):
    """The job input is unusable. Retrying it unchanged will not help."""


def preload_model():
    """Warm the model into VRAM before the first job is accepted.

    Called from the worker's entry point below, not at import. At import it
    would fire in anything that merely reads this module — a test, a linter —
    and spend minutes pulling several gigabytes of weights to do it.

    `load_model` is `lru_cache`d, so this is the only load a warm worker ever
    does: RunPod keeps the container alive between jobs, and the second lecture
    finds the model already resident.

    A missing cached model is fatal, and nothing else is. That split is the
    point: ModelNotCached means the endpoint is misconfigured -- the Cached
    Model was never attached, or the token cannot reach the gated repo -- and
    no number of restarts makes weights appear, so the worker says exactly what
    is wrong and stops instead of accepting lectures it can only fail. Every
    other failure (a transient OOM, a half-written mount) may well succeed on
    the next start, so those still let the worker come up and fail its jobs
    with a readable message rather than crash-loop while RunPod bills for it.
    """

    from rag.transcribe_cohere import ModelNotCached, load_model, resolve_checkpoint

    try:
        checkpoint = resolve_checkpoint()

    except ModelNotCached as error:
        logger.critical("Cached model unavailable: %s", error)
        raise

    logger.info("Loading %s…", checkpoint)

    try:
        started = time.monotonic()
        load_model()

        logger.info("Model loaded in %.1fs", time.monotonic() - started)
        return True

    except Exception as error:
        logger.error("Model failed to load at start: %s: %s",
                     type(error).__name__, error)
        return False


def _validate(job_input):
    """Pull the inputs out of the job, refusing anything we will not fetch."""

    if not isinstance(job_input, dict):
        raise JobInputError("job input must be an object")

    bunny_url = job_input.get("bunny_url")

    try:
        bunny_url = media_url.check(bunny_url, ALLOWED_MEDIA_HOSTS)
    except media_url.UntrustedMediaURL as error:
        raise JobInputError(str(error)) from error

    # Absent means "use the default"; present means it is checked. `or` would
    # conflate the two and silently turn an explicit 0 into 300.
    chunk_seconds = job_input.get("chunk_seconds")

    if chunk_seconds is None:
        chunk_seconds = CHUNK_SECONDS
    else:
        try:
            chunk_seconds = int(chunk_seconds)
        except (TypeError, ValueError) as error:
            raise JobInputError("chunk_seconds must be an integer") from error

        if chunk_seconds <= 0:
            raise JobInputError("chunk_seconds must be positive")

    return bunny_url, chunk_seconds, job_input.get("video_id")


def _transcribe(bunny_url, chunk_seconds, workspace):
    """Stream the lecture through ffmpeg and the ASR. Returns (blocks, seconds).

    `workspace` is this job's own directory. iter_audio_chunks writes each
    chunk there and deletes it once this loop has moved on, so at most one
    chunk of audio exists at a time — and the caller removes the directory
    whatever happens, so nothing survives even if that fails.
    """

    from rag.transcribe_cohere import transcribe_chunk

    audio_dir = workspace / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    blocks = []
    audio_seconds = 0.0
    gpu_seconds = 0.0

    for chunk in iter_audio_chunks(
        bunny_url, chunk_seconds=chunk_seconds, workspace=str(audio_dir)
    ):

        started = time.monotonic()
        text, _ = transcribe_chunk(chunk.path)
        gpu_seconds += time.monotonic() - started

        audio_seconds += chunk.duration_seconds

        blocks.append(
            {
                "index": chunk.index,
                "start_ts": int(chunk.start_seconds),
                # The chunk's real length, so a final short chunk reports what
                # it actually covers rather than a full chunk_seconds.
                "end_ts": int(chunk.end_seconds),
                "text": text,
            }
        )

        logger.info(
            "chunk %d transcribed (%.0fs audio)", chunk.index, chunk.duration_seconds
        )

    if not blocks:
        raise AudioError("the source produced no audio — nothing to transcribe")

    return blocks, audio_seconds, gpu_seconds


def handler(job):
    """One RunPod job. Returns the transcript blocks, or an error dict.

    Errors are returned rather than raised so RunPod records a FAILED job with
    a message the application server can store in `last_error` and a human can
    read, instead of an opaque worker traceback.
    """

    job_id = job.get("id") or "local"
    workspace = WORK_ROOT / str(job_id)

    started = time.monotonic()

    try:
        bunny_url, chunk_seconds, video_id = _validate(job.get("input") or {})

    except JobInputError as error:
        logger.warning("Job %s rejected: %s", job_id, error)
        return {"error": str(error)}

    # Never logged with the URL's query string: a signed Bunny link carries a
    # token, and job logs are not the place for one.
    logger.info(
        "Job %s: video_id=%s host=%s", job_id, video_id,
        bunny_url.split("/")[2] if "//" in bunny_url else "?",
    )

    try:
        workspace.mkdir(parents=True, exist_ok=True)

        blocks, audio_seconds, gpu_seconds = _transcribe(
            bunny_url, chunk_seconds, workspace
        )

    except Exception as error:
        logger.warning("Job %s failed: %s: %s", job_id, type(error).__name__, error)
        return {"error": f"{type(error).__name__}: {error}"[:2000]}

    finally:
        # The one guarantee this worker makes about disk. Runs on success, on
        # an ffmpeg failure, on an ASR failure, and on the way out of a
        # cancelled job — anything that leaves the try block at all.
        shutil.rmtree(workspace, ignore_errors=True)

    wall_seconds = time.monotonic() - started

    result = {
        "blocks": blocks,
        "video_id": video_id,
        "audio_duration_seconds": round(audio_seconds, 1),
        "gpu_processing_seconds": round(gpu_seconds, 2),
        "wall_seconds": round(wall_seconds, 2),
        # The number to compare GPUs on later. Guarded because a zero-length
        # GPU time would be a division by zero rather than an infinite speedup.
        "rtfx": round(audio_seconds / gpu_seconds, 1) if gpu_seconds else None,
    }

    logger.info(
        "Job %s done: %d blocks, %.0fs audio in %.1fs GPU (RTFx %s)",
        job_id, len(blocks), audio_seconds, gpu_seconds, result["rtfx"],
    )

    return result


if __name__ == "__main__":

    import runpod

    # Before accepting work, so the first lecture is not also the one that
    # waits for the weights.
    preload_model()

    runpod.serverless.start({"handler": handler})
