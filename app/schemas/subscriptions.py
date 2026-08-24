"""Request/response models for paid access to a teacher."""

from datetime import datetime

from pydantic import BaseModel


class SubscribeRequest(BaseModel):
    """Which teacher to subscribe to.

    No `student_id`: a student subscribes themselves, and the request body is
    not where that is decided.
    """

    doctor_id: int


class Subscription(BaseModel):
    id: int
    student_id: int
    doctor_id: int
    subscribed_at: datetime


class TeacherSubscription(BaseModel):
    """A teacher a student pays for, and what that unlocks."""

    id: int
    doctor_id: int
    doctor_name: str
    subscribed_at: datetime
    courses: int
    lectures: int


class Subscriber(BaseModel):
    """A student paying a given teacher."""

    id: int
    student_id: int
    student_name: str
    student_email: str
    subscribed_at: datetime


class AccessCheck(BaseModel):
    """Whether a student may watch a lecture, and why not if they may not."""

    student_id: int
    lecture_id: int | None = None
    course_id: int | None = None
    doctor_id: int | None = None
    title: str | None = None
    allowed: bool

    # False when the whole check is switched off, in which case `allowed` is
    # true for everyone and means nothing.
    enforced: bool
