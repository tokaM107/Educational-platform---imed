from fastapi import APIRouter, BackgroundTasks, Depends
from app.api.deps import get_conn
from app.schemas.events import Event, EventResponse, SessionAnalytics
from app.services import engagement, triggers
from datetime import datetime

router = APIRouter(
    prefix="/api",
    tags=["Events"],
)


@router.post("/events", response_model=EventResponse)
def create_event(event: Event, background: BackgroundTasks, conn=Depends(get_conn)):
    """Record one video event.

    Deliberately trivial: one insert, and the response goes back. Finishing the
    last lecture of a course also earns a report, but writing one takes a model
    call of half a minute, so it is handed to a background task — the player gets
    its answer immediately and the report arrives as a notification.
    """

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
                event.student_id,
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
            triggers.after_lecture_completed, event.student_id, event.lecture_id
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
    student_id: int,
    lecture_id: int,
    session_id: str | None = None,
    conn=Depends(get_conn),
):
    """Engagement for one lecture session, reconstructed from its events.

    Reads video_events only — nothing is precomputed or stored, so the numbers
    always reflect every event recorded so far.

    Without `session_id` the totals cover every session this student has had on
    the lecture, each replayed on its own and then added up.
    """

    return SessionAnalytics(
        **engagement.summarise(conn, student_id, lecture_id, session_id)
    )