"""Request and response shapes for the video_id transcription trigger."""

from pydantic import BaseModel, Field


class TranscriptionRequest(BaseModel):
    """What the backend sends when a video is ready to be transcribed."""

    video_id: int = Field(
        ...,
        gt=0,
        description="course_items.id of the video to transcribe",
    )

    # The one documented way past "transcribe once". It maps onto the queue's
    # existing `requeue`, which is deliberately manual: re-running spends GPU
    # time and replaces the video's chunks, so it is never inferred from a
    # repeated request.
    force: bool = Field(
        default=False,
        description="re-transcribe a video that already completed or exhausted "
                    "its retries",
    )


class TranscriptionResponse(BaseModel):
    """What the queue knows about this video after the call."""

    video_id: int
    status: str
    job_id: int

    bunny_guid: str
    attempt_count: int
    max_attempts: int

    # Present once the worker has handed the lecture to RunPod.
    runpod_job_id: str | None = None

    # Chunks stored, once it has completed.
    chunk_count: int | None = None

    # Why the last attempt failed. Null unless the job is in `failed`.
    last_error: str | None = None

    # True when this call is what put the job on the queue, false when it found
    # one already there. The idempotency answer, in one field.
    queued: bool = False

    # True when the job failed but the worker will claim it again by itself.
    will_retry: bool = False
