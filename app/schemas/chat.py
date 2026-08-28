from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints


NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChatMessage(BaseModel):
    role: Literal["student", "tutor"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    student_id: int | None = None

    # Optional: keep the search inside one lecture
    lecture_id: int | None = None

    # Previous turns, oldest first — the demo UI sends the last few
    history: list[ChatMessage] = Field(default_factory=list)


class VideoSegment(BaseModel):
    """Where to jump to, and where to put the flag.

    The player seeks to `start_ts` and marks `end_ts`; it never stops there —
    the student can keep watching straight past the flag.
    """

    lecture_id: int
    lecture_title: str
    video_url: str
    start_ts: int
    end_ts: int
    start_label: str
    end_label: str


class Citation(BaseModel):
    """The exact transcript text an answer was allowed to use."""

    index: int
    chunk_id: int
    lecture_id: int
    start_ts: int
    end_ts: int
    text: str
    distance: float


class ChatSessionCreate(BaseModel):
    """Start a thread for one lecture; the token supplies the student."""

    lecture_id: int
    title: str | None = Field(default=None, max_length=255)


class ChatSession(BaseModel):
    id: UUID
    student_id: int
    lecture_id: int
    title: str | None
    created_at: datetime
    updated_at: datetime


class ChatMessageCreate(BaseModel):
    """A student's next turn. Assistant messages are server-generated."""

    content: NonBlankText


class StoredChatMessage(BaseModel):
    id: int
    session_id: UUID
    role: Literal["user", "assistant"]
    content: str
    standalone_query: str | None = None
    citations: list[Citation] | None = None
    created_at: datetime


class ChatTurnResponse(BaseModel):
    """The two persisted messages plus the video navigation for this answer."""

    user_message: StoredChatMessage
    assistant_message: StoredChatMessage
    grounded: bool
    segments: list[VideoSegment] = Field(default_factory=list)
    notice: str | None = None


class ChatResponse(BaseModel):
    answer: str

    # False when nothing in the transcript was close enough to the question,
    # in which case the tutor says so and returns no segments.
    grounded: bool

    segments: list[VideoSegment] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)

    # Set when the answer is degraded, e.g. the LLM was unreachable and only
    # the retrieved video segments are available.
    notice: str | None = None
