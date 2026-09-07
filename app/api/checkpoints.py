"""Persist video checkpoint observations for the authenticated student."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_conn, get_current_user
from app.schemas.checkpoints import (
    CheckpointAttemptCreate,
    CheckpointAttemptResult,
    CheckpointQuestion,
)
from app.services import subscriptions


router = APIRouter(prefix="/api/checkpoints", tags=["Checkpoints"])


def _questions(conn, column, resource_id):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT q.id, q.lecture_id, q.video_id, t.name, q.prompt, q.options,
                   q.checkpoint_timestamp_seconds, q.allowed_time_seconds,
                   q.position
            FROM checkpoint_questions AS q
            LEFT JOIN topics AS t ON t.id = q.topic_id
            WHERE q.{column} = %s
            ORDER BY q.position, q.id
            """,
            (resource_id,),
        )
        rows = cur.fetchall()

    # Deliberately no answer key in the student response.
    return [
        CheckpointQuestion(
            id=row[0], lecture_id=row[1], video_id=row[2], topic=row[3],
            prompt=row[4], options=row[5], checkpoint_timestamp_seconds=row[6],
            allowed_time_seconds=row[7], position=row[8],
        )
        for row in rows
    ]


@router.get("/lecture/{lecture_id}", response_model=list[CheckpointQuestion])
def list_lecture_checkpoints(
    lecture_id: int,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT l.course_id, c.doctor_id FROM lectures AS l
               JOIN courses AS c ON c.id = l.course_id WHERE l.id = %s""",
            (lecture_id,),
        )
        scope = cur.fetchone()
    if scope is None:
        raise HTTPException(status_code=404, detail="Lecture not found")
    allowed = (
        current_user["id"] == scope[1]
        or subscriptions.entitled_to_course(
            conn, current_user["id"], scope[1], scope[0]
        )
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Not allowed to read checkpoints")

    return _questions(conn, "lecture_id", lecture_id)


@router.get("/video/{video_id}", response_model=list[CheckpointQuestion])
def list_video_checkpoints(
    video_id: int,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    allowed, _, _ = subscriptions.can_watch_video(
        conn, current_user["id"], video_id
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Not allowed to read checkpoints")
    return _questions(conn, "video_id", video_id)


@router.post("/{question_id}/attempt", response_model=CheckpointAttemptResult)
def create_checkpoint_attempt(
    question_id: int,
    attempt: CheckpointAttemptCreate,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    student_id = current_user["id"]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT q.course_id, q.lecture_id, q.video_id, q.correct_answer,
                   q.allowed_time_seconds, c.doctor_id
            FROM checkpoint_questions AS q
            JOIN courses AS c ON c.id = q.course_id
            WHERE q.id = %s
            """,
            (question_id,),
        )
        question = cur.fetchone()

    if question is None:
        raise HTTPException(status_code=404, detail="Checkpoint not found")

    course_id, lecture_id, video_id, correct_answer, allowed_seconds, doctor_id = question
    if lecture_id is not None:
        allowed = subscriptions.entitled_to_course(
            conn, student_id, doctor_id, course_id
        ) or student_id == doctor_id
    else:
        allowed, _, _ = subscriptions.can_watch_video(conn, student_id, video_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Not allowed to answer checkpoint")

    if attempt.answered_at and attempt.answered_at < attempt.shown_at:
        raise HTTPException(status_code=422, detail="answered_at precedes shown_at")

    is_answered = attempt.outcome == "answered"
    selected = attempt.selected_answer.strip() if is_answered else None
    is_correct = (
        selected.casefold() == correct_answer.strip().casefold()
        if is_answered else None
    )

    # A client-provided response time is checked against the timestamps and the
    # authored time limit.  The small tolerance covers browser scheduling; a
    # mismatch is rejected rather than silently producing false latency data.
    if is_answered:
        elapsed_ms = int((attempt.answered_at - attempt.shown_at).total_seconds() * 1000)
        if abs(elapsed_ms - attempt.response_time_ms) > 1500:
            raise HTTPException(status_code=422, detail="response time mismatch")
        if attempt.response_time_ms > float(allowed_seconds) * 1000 + 1500:
            raise HTTPException(status_code=422, detail="response exceeded allowed time")

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH inserted AS (
              INSERT INTO checkpoint_attempts (
                  student_id, course_id, checkpoint_question_id, session_id,
                  shown_at, answered_at, response_time_ms, selected_answer,
                  is_correct, skipped, timed_out, attempt_number
              ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
              ON CONFLICT (student_id, checkpoint_question_id, session_id, attempt_number)
              DO NOTHING
              RETURNING id, checkpoint_question_id, is_correct, skipped, timed_out,
                        attempt_number, created_at
            )
            SELECT * FROM inserted
            UNION ALL
            SELECT id, checkpoint_question_id, is_correct, skipped, timed_out,
                   attempt_number, created_at
            FROM checkpoint_attempts
            WHERE student_id = %s AND checkpoint_question_id = %s
              AND session_id = %s AND attempt_number = %s
              AND NOT EXISTS (SELECT 1 FROM inserted)
            LIMIT 1
            """,
            (
                student_id, course_id, question_id, attempt.session_id,
                attempt.shown_at, attempt.answered_at, attempt.response_time_ms,
                selected, is_correct, attempt.outcome == "skipped",
                attempt.outcome == "timed_out", attempt.attempt_number,
                student_id, question_id, attempt.session_id, attempt.attempt_number,
            ),
        )
        row = cur.fetchone()
        conn.commit()

    outcome = "skipped" if row[3] else "timed_out" if row[4] else "answered"
    return CheckpointAttemptResult(
        id=row[0], checkpoint_question_id=row[1], is_correct=row[2],
        outcome=outcome, attempt_number=row[5], created_at=row[6],
    )
