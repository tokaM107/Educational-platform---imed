from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.api.deps import get_conn, get_current_user
from app.schemas.events import Event, EventResponse, SessionAnalytics
from app.services import authz, engagement, triggers

router = APIRouter(
    prefix="/api",
    tags=["Events"],
)


@router.post("/events", response_model=EventResponse)
def create_event(
    event: Event,
    background: BackgroundTasks,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """Record one video event, against the authenticated student.

    Deliberately trivial: one insert, and the response goes back. Finishing the
    last lecture of a course also earns a report, but writing one takes a model
    call of half a minute, so it is handed to a background task — the player gets
    its answer immediately and the report arrives as a notification.

    The student comes from the token rather than the body. These rows are the
    only evidence behind watch time, coverage and every figure in the weekly
    report, so a body that could name its own student would let anybody
    manufacture somebody else's attendance — or their absence.
    """

    student_id = current_user["id"]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO video_events (
                student_id,
                lecture_id,
                event_type,
                video_ts,
                session_id
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING
                id,
                student_id,
                lecture_id,
                event_type,
                video_ts,
                session_id,
                created_at
            """,
            (
                student_id,
                event.lecture_id,
                event.event_type,
                event.video_ts,
                event.session_id,
            ),
        )

        row = cur.fetchone()
        conn.commit()

    # After the response, on its own connection. Only 'complete' can finish a
    # module, so nothing is queued for the hundreds of heartbeats.
    if event.event_type == "complete":
        background.add_task(
            triggers.after_lecture_completed, student_id, event.lecture_id
        )

    return EventResponse(
        id=row[0],
        student_id=row[1],
        lecture_id=row[2],
        event_type=row[3],
        video_ts=row[4],
        session_id=row[5],
        created_at=row[6],
    )


@router.get("/events/analytics", response_model=SessionAnalytics)
def event_analytics(
    lecture_id: int,
    student_id: int | None = None,
    session_id: str | None = None,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """Engagement for one lecture session, reconstructed from its events.

    Reads video_events only — nothing is precomputed or stored, so the numbers
    always reflect every event recorded so far.

    Without `session_id` the totals cover every session this student has had on
    the lecture, each replayed on its own and then added up.

    `student_id` defaults to the caller and is only accepted for someone else
    when the caller is the doctor teaching them — how long a named student spent
    watching, and where they stopped, is theirs.
    """

    target = current_user["id"] if student_id is None else student_id

    if not authz.may_view_student(conn, current_user, target):
        raise HTTPException(
            status_code=403,
            detail="Not allowed to read this student's engagement",
        )

    return SessionAnalytics(
        **engagement.summarise(conn, target, lecture_id, session_id)
    )
