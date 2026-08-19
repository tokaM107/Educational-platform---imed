"""Response models for in-site notifications."""

from datetime import datetime

from pydantic import BaseModel, Field


class Notification(BaseModel):
    id: int
    kind: str
    title: str
    body: str | None

    # The report to open. None for a notification that is not about one.
    report_id: int | None

    # Who the report is about — the recipient for a student, one of their
    # students for a doctor.
    student_id: int | None

    read_at: datetime | None
    created_at: datetime


class Inbox(BaseModel):
    unread: int
    items: list[Notification] = Field(default_factory=list)
