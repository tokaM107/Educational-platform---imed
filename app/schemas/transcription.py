"""Request and response shapes for the video_id transcription trigger."""

from pydantic import BaseModel, Field, model_validator


class TranscriptionRequest(BaseModel):
    """What the backend sends when a video is ready to be transcribed.

    The canonical field is `video_id`, but the course-item payload the frontend
    already passes around carries the same number as `id`, either at the top
    level or inside the `data` envelope:

        {"video_id": 1842}
        {"id": 1842, "type": "video", "videoStatus": "ready", ...}
        {"success": true, "data": {"id": 1842, ...}}

    All three are accepted, so the caller can forward the object it already has
    instead of reshaping it. Everything else in that payload is ignored:
    `videoStatus` and `videoAttached` describe the catalog's own view of the
    upload, and this service checks Bunny itself rather than trusting a field
    it did not write.
    """

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

    @model_validator(mode="before")
    @classmethod
    def _accept_course_item_shapes(cls, data):

        if not isinstance(data, dict):
            return data

        # Unwrap the envelope first, keeping any `force` the caller put beside
        # it rather than inside it.
        inner = data.get("data")

        if isinstance(inner, dict):
            data = {**inner, "force": data.get("force", inner.get("force", False))}

        if "video_id" not in data and "id" in data:
            data = {**data, "video_id": data["id"]}

        return data


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
