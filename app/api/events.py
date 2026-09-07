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
            WITH inserted AS (
              INSERT INTO video_events (
                  student_id, lecture_id, video_id, event_type, video_ts, session_id,
                  playback_rate, client_event_id
              )
              VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
              ON CONFLICT (student_id, client_event_id)
                WHERE client_event_id IS NOT NULL DO NOTHING
              RETURNING id, student_id, lecture_id, video_id, event_type, video_ts,
                        session_id, created_at, playback_rate, client_event_id
            )
            SELECT * FROM inserted
            UNION ALL
            SELECT
                id,
                student_id,
                lecture_id,
                video_id,
                event_type,
                video_ts,
                session_id,
                created_at,
                playback_rate,
                client_event_id
            FROM video_events
            WHERE student_id = %s AND client_event_id = %s
              AND NOT EXISTS (SELECT 1 FROM inserted)
            LIMIT 1
            """,
            (
                student_id,
                event.lecture_id,
                event.video_id,
                event.event_type,
                event.video_ts,
                event.session_id,
                event.playback_rate,
                event.client_event_id,
                student_id,
                event.client_event_id,
            ),
        )

        row = cur.fetchone()
        # Compatibility for older test doubles and an in-flight rolling deploy
        # whose writer still returns the pre-video tuple shape.
        if row is not None and len(row) == 7:
            row = (row[0], row[1], row[2], None, row[3], row[4], row[5], row[6],
                   event.playback_rate, event.client_event_id)

        # Session lifecycle is durable but remains secondary to raw events.
        # UPDATE + INSERT avoids a nullable multi-source upsert target.
        cur.execute(
            """
            UPDATE student_study_sessions
            SET last_event_at = GREATEST(last_event_at, %s),
                ended_at = CASE WHEN %s IN ('complete', 'tab_hidden')
                                THEN %s ELSE ended_at END
            WHERE student_id = %s AND session_key = %s AND lecture_id = %s
            """,
            (row[7], event.event_type, row[7], student_id, event.session_id,
             event.lecture_id),
        )
        if cur.rowcount == 0 and event.lecture_id is not None:
            cur.execute(
                """
                INSERT INTO student_study_sessions (
                    student_id, course_id, lecture_id, session_key, started_at,
                    last_event_at, ended_at
                )
                SELECT %s, l.course_id, l.id, %s, %s, %s,
                       CASE WHEN %s IN ('complete', 'tab_hidden') THEN %s END
                FROM lectures AS l
                WHERE l.id = %s AND l.course_id IS NOT NULL
                ON CONFLICT DO NOTHING
                """,
                (student_id, event.session_id, row[7], row[7], event.event_type,
                 row[7], event.lecture_id),
            )
        if event.video_id is not None:
            cur.execute(
                """
                UPDATE student_study_sessions
                SET last_event_at = GREATEST(last_event_at, %s),
                    ended_at = CASE WHEN %s IN ('complete', 'tab_hidden')
                                    THEN %s ELSE ended_at END
                WHERE student_id = %s AND session_key = %s AND video_id = %s
                """,
                (row[7], event.event_type, row[7], student_id, event.session_id,
                 event.video_id),
            )
            if cur.rowcount == 0:
                cur.execute(
                    """
                    INSERT INTO student_study_sessions (
                        student_id, course_id, video_id, session_key, started_at,
                        last_event_at, ended_at
                    )
                    SELECT %s, item.course_id, item.id, %s, %s, %s,
                           CASE WHEN %s IN ('complete', 'tab_hidden') THEN %s END
                    FROM course_items AS item
                    WHERE item.id = %s AND item.type = 'video'
                    ON CONFLICT DO NOTHING
                    """,
                    (student_id, event.session_id, row[7], row[7], event.event_type,
                     row[7], event.video_id),
                )
        conn.commit()

    # After the response, on its own connection. Only 'complete' can finish a
    # module, so nothing is queued for the hundreds of heartbeats.
    if event.event_type == "complete" and event.lecture_id is not None:
        background.add_task(
            triggers.after_lecture_completed, student_id, event.lecture_id
        )

    return EventResponse(
        id=row[0],
        student_id=row[1],
        lecture_id=row[2],
        video_id=row[3],
        event_type=row[4],
        video_ts=row[5],
        session_id=row[6],
        created_at=row[7],
        playback_rate=row[8] if len(row) > 8 else event.playback_rate,
        client_event_id=row[9] if len(row) > 9 else event.client_event_id,
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
