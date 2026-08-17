from typing import Literal

from pydantic import BaseModel
from datetime import datetime

class Event(BaseModel):
    student_id: int
    lecture_id: int
    event_type: Literal["play", "pause", "seek", "complete", "skip", "rewatch_segment"]
    video_ts: float
    session_id: str


class EventResponse(Event):
    id: int
    created_at: datetime