from typing import Literal

from pydantic import BaseModel, Field, model_validator
from datetime import datetime
from uuid import UUID


# What the player sends. `skip` and `rewatch_segment` are not captured by the
# browser; they stay in the list so the endpoint keeps accepting every type the
# video_events CHECK constraint allows.
#
# `tab_hidden` / `tab_visible` say the lecture page lost and regained
# visibility, and nothing more — a switched tab, a locked screen and a
# minimised window are the same event here.
EventType = Literal[
    "play",
    "pause",
    "seek",
    "complete",
    "skip",
    "rewatch_segment",
    "heartbeat",
    "tab_hidden",
    "tab_visible",
]


class Event(BaseModel):
    """What the player sends.

    No `student_id`: the event is recorded against whoever the request is
    authenticated as. A player that could name the student would let anyone post
    watch time onto anyone's record, which is the one thing the weekly report and
    the engagement figures take entirely on trust.
    """

    lecture_id: int | None = None
    video_id: int | None = None
    event_type: EventType
    video_ts: float
    session_id: str = Field(min_length=1, max_length=64)
    playback_rate: float | None = Field(default=None, ge=0.25, le=4.0)
    client_event_id: UUID | None = None

    @model_validator(mode="after")
    def one_content_source(self):
        if (self.lecture_id is None) == (self.video_id is None):
            raise ValueError("provide exactly one of lecture_id or video_id")
        return self


class EventResponse(Event):
    id: int

    # On the way out, not on the way in: the server says who it recorded this
    # for, having decided that from the token.
    student_id: int

    created_at: datetime


class SessionAnalytics(BaseModel):
    """Engagement for one lecture session — three separate clocks.

    `watch_time_seconds` is time the video was playing, reconstructed from the
    events. `session_duration_seconds` is wall-clock time from the first event
    to the last, which includes every pause and every absence.
    `time_away_seconds` is how much of that the page spent hidden. A three-hour
    session with 68 minutes of watching and 42 minutes away is all three at
    once; they are never interchangeable.
    """

    student_id: int
    lecture_id: int | None
    video_id: int | None = None

    # None when the numbers cover every session on the lecture, not just one.
    session_id: str | None

    # Seconds, from the last transcript timestamp — see engagement.py.
    lecture_duration: float

    watch_time_seconds: float

    # None when the lecture length is unknown. Can exceed 100 when a student
    # rewatches: it is watched seconds over lecture seconds, not coverage.
    watch_percentage: float | None

    time_away_seconds: float
    session_duration_seconds: float

    pause_count: int
    seek_count: int
    backward_seek_count: int
    forward_seek_count: int
    rewatch_count: int
    completed: bool
