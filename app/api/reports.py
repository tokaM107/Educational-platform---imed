"""Weekly report endpoints.

The measured half is recomputed on every request — it is a replay of that week's
events and costs milliseconds. The written half is stored per student, course and
week, so a report is a stable document rather than something that says something
different each time it is opened.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_conn
from app.schemas.reports import ReportSubject, WeeklyReport
from app.services import report, report_store


router = APIRouter(
    prefix="/api/reports",
    tags=["Reports"],
)


SUBJECTS_SQL = """
    SELECT
        u.id,
        u.name,
        c.id,
        c.title,
        d.name,
        (
            SELECT max(e.created_at)
            FROM video_events AS e
            JOIN lectures AS l ON l.id = e.lecture_id
            WHERE e.student_id = u.id AND l.course_id = c.id
        ) AS last_activity
    FROM enrollments AS en
    JOIN users AS u ON u.id = en.student_id
    JOIN courses AS c ON c.id = en.course_id
    JOIN users AS d ON d.id = c.doctor_id
    ORDER BY u.name, c.title
"""


@router.get("/subjects", response_model=list[ReportSubject])
def report_subjects(conn=Depends(get_conn)):
    """Every student/course pair a weekly report can be built for.

    The report page uses this to fill its own picker, so nobody has to know a
    student id to open a report. There is no session to read the current user
    from yet — `POST /api/auth/login` is still a stub — so the alternative would
    be guessing, and a wrong guess shows one student another student's week.
    """

    with conn.cursor() as cur:
        cur.execute(SUBJECTS_SQL)
        rows = cur.fetchall()

    return [
        ReportSubject(
            student_id=row[0],
            student_name=row[1],
            course_id=row[2],
            course_title=row[3],
            doctor_name=row[4],
            last_activity=row[5],
        )
        for row in rows
    ]


@router.get("/weekly", response_model=WeeklyReport)
def weekly_report(
    student_id: int,
    course_id: int | None = None,
    week_start: date | None = None,
    narrative: bool = True,
    refresh: bool = Query(
        False,
        description="Rewrite the narrative even if a stored one still matches "
                    "the figures.",
    ),
    conn=Depends(get_conn),
):
    """One student's week on one course.

    `week_start` picks a specific window; the default is the seven days ending
    today, measured in REPORT_TIMEZONE. `course_id` picks one of several
    enrolments; the default is the most recent. `narrative=false` skips the model
    entirely, which is the fast path when only the numbers are wanted.
    """

    result = report.build(
        conn,
        student_id=student_id,
        course_id=course_id,
        week_start=week_start,
        with_narrative=narrative,
        refresh=refresh,
    )

    if result is None:
        raise HTTPException(status_code=404, detail="Student not found")

    return WeeklyReport(**result)


# Declared last on purpose: FastAPI matches in order, and a path parameter would
# otherwise swallow /weekly and /subjects.
@router.get("/{report_id}", response_model=WeeklyReport)
def stored_report(report_id: int, conn=Depends(get_conn)):
    """A report a completion produced, exactly as it read when it was issued.

    Frozen rather than recomputed: "you finished the module" describes a moment,
    and re-running the query a fortnight later would quietly rewrite history.
    """

    payload = report_store.get(conn, report_id)

    if payload is None:
        raise HTTPException(status_code=404, detail="Report not found")

    return WeeklyReport(**payload)
