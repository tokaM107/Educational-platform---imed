from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CheckpointQuestion(BaseModel):
    id: int
    lecture_id: int | None
    video_id: int | None
    topic: str | None
    prompt: str
    options: list[str]
    checkpoint_timestamp_seconds: float
    allowed_time_seconds: float
    position: int


class CheckpointAttemptCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    shown_at: datetime
    answered_at: datetime | None = None
    response_time_ms: int | None = Field(default=None, ge=0)
    selected_answer: str | None = None
    outcome: Literal["answered", "skipped", "timed_out"]
    attempt_number: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def outcome_is_complete(self):
        answered = self.outcome == "answered"
        if answered and (
            self.answered_at is None
            or self.response_time_ms is None
            or not (self.selected_answer or "").strip()
        ):
            raise ValueError("answered checkpoints require answer, time and timestamp")
        if not answered and any(
            value is not None
            for value in (self.answered_at, self.response_time_ms, self.selected_answer)
        ):
            raise ValueError("skipped/timed-out checkpoints cannot carry an answer")
        return self


class CheckpointAttemptResult(BaseModel):
    id: int
    checkpoint_question_id: int
    is_correct: bool | None
    outcome: Literal["answered", "skipped", "timed_out"]
    attempt_number: int
    created_at: datetime
