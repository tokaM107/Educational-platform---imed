from fastapi import APIRouter, Depends
from app.api.deps import get_conn
from app.schemas.events import Event, EventResponse
from datetime import datetime

router = APIRouter(
    prefix="/api",
    tags=["Events"],
)


@router.post("/events", response_model=EventResponse)
def create_event(event: Event, conn=Depends(get_conn)):
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

        return EventResponse(
            id=row[0],
            student_id=row[1],
            lecture_id=row[2],
            event_type=row[3],
            video_ts=row[4],
            session_id=row[5],
            created_at=row[6],
        )