"""Instructor post-exam statistics.

Read-only aggregation over `question_attempts` joined to `questions`. No model
call, no stored state: the same attempts always produce the same page, in
milliseconds.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_conn
from app.schemas.exams import ExamListing, ExamStats
from app.services import exam_stats


router = APIRouter(
    prefix="/api/exams",
    tags=["Exams"],
)


@router.get("", response_model=list[ExamListing])
def list_exams(
    course_id: int | None = None,
    doctor_id: int | None = None,
    conn=Depends(get_conn),
):
    """Lectures that have questions, most recently answered first."""

    return [ExamListing(**row) for row in exam_stats.available(conn, course_id, doctor_id)]


@router.get("/{lecture_id}", response_model=ExamStats)
def exam_statistics(
    lecture_id: int,
    pass_mark: float = Query(
        exam_stats.DEFAULT_PASS_MARK, ge=0, le=100,
        description="Mark at or above which a student has passed.",
    ),
    conn=Depends(get_conn),
):
    """How the class did on one lecture's questions.

    Figures cover the enrolled cohort. Attempts by anyone not enrolled on the
    course are excluded and counted in `attempts_from_non_enrolled`, so a stray
    row cannot quietly move an average.
    """

    result = exam_stats.fetch(conn, lecture_id, pass_mark=pass_mark)

    if result is None:
        raise HTTPException(status_code=404, detail="Lecture not found")

    return ExamStats(**result)
