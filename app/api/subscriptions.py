"""Paid access to a teacher's material.

A subscription is per teacher: it unlocks every course and lecture that teacher
publishes. Enrolment is a separate act, and is refused for a course whose
teacher the student does not pay for.

**No authentication yet.** These endpoints take `student_id` as a parameter, so
they identify rather than authenticate — anyone can pass any id. That is true of
the whole API today, and it is why `ENFORCE_SUBSCRIPTIONS` gates a real paywall
rather than pretending to be one. The check belongs here; the identity it acts on
has to come from a session before this is security.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_conn
from app.config import get_settings
from app.schemas.subscriptions import (
    AccessCheck,
    Subscriber,
    SubscribeRequest,
    Subscription,
    TeacherSubscription,
)
from app.services import subscriptions


router = APIRouter(
    prefix="/api/subscriptions",
    tags=["Subscriptions"],
)


@router.post("", response_model=Subscription, status_code=201)
def create_subscription(request: SubscribeRequest, conn=Depends(get_conn)):
    """Subscribe a student to a teacher.

    Idempotent: subscribing again returns the existing row rather than resetting
    the date the student has been paying since.

    This records the entitlement, not the payment — there is no amount or
    processor reference here, and no expiry. Taking the money happens upstream.
    """

    row, _created = subscriptions.subscribe(
        conn, request.student_id, request.doctor_id
    )

    if row is None:
        raise HTTPException(
            status_code=400,
            detail="Needs an existing student and an existing doctor",
        )

    return Subscription(**row)


@router.delete("/{student_id}/{doctor_id}")
def cancel_subscription(student_id: int, doctor_id: int, conn=Depends(get_conn)):
    """Revoke access.

    Enrolments, events and reports are left untouched: cancelling should stop
    someone watching, not erase that they studied.
    """

    removed = subscriptions.cancel(conn, student_id, doctor_id)

    if not removed:
        raise HTTPException(status_code=404, detail="No such subscription")

    return {"cancelled": removed}


@router.get("/student/{student_id}", response_model=list[TeacherSubscription])
def student_subscriptions(student_id: int, conn=Depends(get_conn)):
    """The teachers this student pays for."""

    return [TeacherSubscription(**row) for row in subscriptions.for_student(conn, student_id)]


@router.get("/doctor/{doctor_id}", response_model=list[Subscriber])
def doctor_subscribers(doctor_id: int, conn=Depends(get_conn)):
    """Who is paying this teacher."""

    return [Subscriber(**row) for row in subscriptions.for_doctor(conn, doctor_id)]


@router.get("/access", response_model=AccessCheck)
def check_access(
    student_id: int,
    lecture_id: int | None = None,
    course_id: int | None = None,
    conn=Depends(get_conn),
):
    """Whether a student may watch a lecture, or enrol on a course.

    Lets the UI show a locked state before the student clicks, instead of
    letting them press play and meet a 402.
    """

    if lecture_id is None and course_id is None:
        raise HTTPException(
            status_code=400, detail="Give a lecture_id or a course_id"
        )

    enforced = get_settings().enforce_subscriptions

    if lecture_id is not None:
        allowed, doctor_id, title = subscriptions.can_watch(conn, student_id, lecture_id)
    else:
        allowed, doctor_id, title = subscriptions.can_enrol(conn, student_id, course_id)

    return AccessCheck(
        student_id=student_id,
        lecture_id=lecture_id,
        course_id=course_id,
        doctor_id=doctor_id,
        title=title,
        # With the paywall off everyone is allowed, and the flag says so rather
        # than letting the caller read it as a granted subscription.
        allowed=allowed or not enforced,
        enforced=enforced,
    )
