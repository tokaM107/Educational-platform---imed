from typing import Literal

from pydantic import BaseModel
from datetime import datetime


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
    student_id: int
    lecture_id: int
    event_type: EventType
    video_ts: float
    session_id: str


class EventResponse(Event):
    id: int
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
    lecture_id: int

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
    rewatch_count: int
    completed: bool
