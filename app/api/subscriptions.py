"""Paid access to a teacher's material.

A subscription is per teacher: it unlocks every course and lecture that teacher
publishes. Enrolment is a separate act, and is refused for a course whose
teacher the student does not pay for.

Every route is authenticated, and the student a subscription belongs to comes
from the token. `ENFORCE_SUBSCRIPTIONS` now gates a real boundary rather than an
honour system, because the identity the check runs against can no longer be
chosen by the caller.

Still true and still outside this file: subscribing records an entitlement, not
a payment. Nothing here takes money or checks that any was taken.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_conn, get_current_user, require_doctor
from app.config import get_settings
from app.schemas.subscriptions import (
    AccessCheck,
    Subscriber,
    SubscribeRequest,
    Subscription,
    TeacherSubscription,
)
from app.services import authz, subscriptions


router = APIRouter(
    prefix="/api/subscriptions",
    tags=["Subscriptions"],
)


@router.post("", response_model=Subscription, status_code=201)
def create_subscription(
    request: SubscribeRequest,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """Subscribe the authenticated student to a teacher.

    Idempotent: subscribing again returns the existing row rather than resetting
    the date the student has been paying since.

    This records the entitlement, not the payment — there is no amount or
    processor reference here, and no expiry. Taking the money happens upstream,
    which is the open question this endpoint leaves: it will hand out access to
    anyone logged in who asks for it.
    """

    row, _created = subscriptions.subscribe(
        conn, current_user["id"], request.doctor_id
    )

    if row is None:
        raise HTTPException(
            status_code=400,
            detail="Needs an existing student and an existing doctor",
        )

    return Subscription(**row)


@router.delete("/{doctor_id}")
def cancel_subscription(
    doctor_id: int,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """Give up the caller's own subscription to a teacher.

    Enrolments, events and reports are left untouched: cancelling should stop
    someone watching, not erase that they studied.

    The student half of the old two-part path is gone. It could only ever mean
    "me", and while it was in the URL it also meant "or anyone I care to name".
    """

    removed = subscriptions.cancel(conn, current_user["id"], doctor_id)

    if not removed:
        raise HTTPException(status_code=404, detail="No such subscription")

    return {"cancelled": removed}


@router.get("/student/{student_id}", response_model=list[TeacherSubscription])
def student_subscriptions(
    student_id: int,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """The teachers this student pays for.

    Kept as a path parameter because a doctor may legitimately ask it of a
    student they teach — but it is checked now, so a student asking it of
    another student is refused rather than answered.
    """

    if not authz.may_view_student(conn, current_user, student_id):
        raise HTTPException(
            status_code=403,
            detail="Not allowed to read this student's subscriptions",
        )

    return [
        TeacherSubscription(**row)
        for row in subscriptions.for_student(conn, student_id)
    ]


@router.get("/doctor/{doctor_id}", response_model=list[Subscriber])
def doctor_subscribers(
    doctor_id: int,
    conn=Depends(get_conn),
    current_user=Depends(require_doctor),
):
    """Who is paying this teacher.

    A teacher's subscriber list is their commercial position — who their
    students are, and how many. Doctors only, and only their own: one teacher
    reading another's roll is exactly the request this used to answer.
    """

    if doctor_id != current_user["id"]:
        raise HTTPException(
            status_code=403,
            detail="Not allowed to read another teacher's subscribers",
        )

    return [
        Subscriber(**row) for row in subscriptions.for_doctor(conn, doctor_id)
    ]


@router.get("/access", response_model=AccessCheck)
def check_access(
    lecture_id: int | None = None,
    course_id: int | None = None,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """Whether the caller may watch a lecture, or enrol on a course.

    Lets the UI show a locked state before the student clicks, instead of
    letting them press play and meet a 402.
    """

    if lecture_id is None and course_id is None:
        raise HTTPException(
            status_code=400, detail="Give a lecture_id or a course_id"
        )

    student_id = current_user["id"]
    enforced = get_settings().enforce_subscriptions

    if lecture_id is not None:
        allowed, doctor_id, title = subscriptions.can_watch(
            conn, student_id, lecture_id
        )
    else:
        allowed, doctor_id, title = subscriptions.can_enrol(
            conn, student_id, course_id
        )

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
