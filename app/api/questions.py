from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from app.api.deps import get_conn
from app.schemas.questions import Question, QuestionAttemptCreate
from app.services import triggers

router = APIRouter(
    prefix="/api/questions",
    tags=["Questions"],
)

@router.get("/{lecture_id}", response_model=list[Question])
def get_questions(
    lecture_id: int,
    conn=Depends(get_conn),
):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                lecture_id,
                topic_id,
                stem,
                options,
                difficulty
            FROM questions
            WHERE lecture_id = %s
            ORDER BY id
            """,
            (lecture_id,),
        )

        rows = cur.fetchall()

    return [
        Question(
            id=row[0],
            lecture_id=row[1],
            topic_id=row[2],
            stem=row[3],
            options=row[4],
            difficulty=row[5],
        )
        for row in rows
    ]


@router.post("/{question_id}/attempt")
def create_attempt(
    question_id: int,
    attempt: QuestionAttemptCreate,
    background: BackgroundTasks,
    conn=Depends(get_conn),
):
    """Record one answer and say whether it was right.

    Answering the last outstanding question on a lecture also earns a report.
    Writing one takes a model call, so it goes to a background task and the
    student gets their result straight away; the report arrives as a
    notification.
    """

    with conn.cursor() as cur:

        # 1. Get the correct answer, and which lecture this belongs to
        cur.execute(
            """
            SELECT correct_option, lecture_id
            FROM questions
            WHERE id = %s
            """,
            (question_id,),
        )

        question = cur.fetchone()

        if question is None:
            raise HTTPException(
                status_code=404,
                detail="Question not found",
            )

        correct_option, lecture_id = question

        # 2. Check the student's answer
        chosen = attempt.selected_option.strip().upper()
        is_correct = chosen == correct_option.strip().upper()

        # 3. Save the attempt, including which option was picked. Storing the
        #    choice is what lets the instructor view tell a hard question from
        #    one whose distractor is actively misleading the class.
        cur.execute(
            """
            INSERT INTO question_attempts
                (student_id, question_id, is_correct, selected_option)
            VALUES
                (%s, %s, %s, %s)
            RETURNING
                id,
                student_id,
                question_id,
                is_correct,
                selected_option,
                answered_at
            """,
            (
                attempt.student_id,
                question_id,
                is_correct,
                chosen,
            ),
        )

        row = cur.fetchone()

        conn.commit()

    # After the response, on its own connection.
    background.add_task(
        triggers.after_question_attempt, attempt.student_id, lecture_id
    )

    return {
        "id": row[0],
        "student_id": row[1],
        "question_id": row[2],
        "is_correct": row[3],
        "selected_option": row[4],
        "answered_at": row[5],
    }