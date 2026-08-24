"""Instructor post-exam statistics.

Read-only aggregation over `question_attempts` joined to `questions`. No model
call, no stored state: the same attempts always produce the same page, in
milliseconds.

Doctors only. These figures describe a cohort rather than one student — pass
rates, which distractor is catching people, how the class did — and that is a
teacher's view of their own class, not something a classmate should be able to
read about everyone around them.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_conn, require_doctor
from app.schemas.exams import ExamListing, ExamStats
from app.services import authz, exam_stats


router = APIRouter(
    prefix="/api/exams",
    tags=["Exams"],
)


@router.get("", response_model=list[ExamListing])
def list_exams(
    course_id: int | None = None,
    conn=Depends(get_conn),
    current_user=Depends(require_doctor),
):
    """The caller's own lectures that have questions, most recently answered first.

    `doctor_id` is gone: it selected whose teaching to report on, and the only
    answer this endpoint should give is "yours".
    """

    return [
        ExamListing(**row)
        for row in exam_stats.available(conn, course_id, current_user["id"])
    ]


@router.get("/{lecture_id}", response_model=ExamStats)
def exam_statistics(
    lecture_id: int,
    pass_mark: float = Query(
        exam_stats.DEFAULT_PASS_MARK, ge=0, le=100,
        description="Mark at or above which a student has passed.",
    ),
    conn=Depends(get_conn),
    current_user=Depends(require_doctor),
):
    """How the class did on one lecture's questions.

    Restricted to the doctor who owns the lecture: being a teacher somewhere is
    not a reason to read the results of somebody else's class.

    Figures cover the enrolled cohort. Attempts by anyone not enrolled on the
    course are excluded and counted in `attempts_from_non_enrolled`, so a stray
    row cannot quietly move an average.
    """

    if not authz.owns_lecture(conn, current_user["id"], lecture_id):
        # 404 rather than 403: this lecture is not the caller's to know about,
        # and a 403 would confirm it exists.
        raise HTTPException(status_code=404, detail="Lecture not found")

    result = exam_stats.fetch(conn, lecture_id, pass_mark=pass_mark)

    if result is None:
        raise HTTPException(status_code=404, detail="Lecture not found")

    return ExamStats(**result)
