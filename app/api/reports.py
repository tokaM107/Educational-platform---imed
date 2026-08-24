"""Weekly report endpoints.

The measured half is recomputed on every request — it is a replay of that week's
events and costs milliseconds. The written half is stored per student, course and
week, so a report is a stable document rather than something that says something
different each time it is opened.

A report is the most private thing this application produces: it says how much
somebody studied, what they got wrong and what their doctor should worry about.
Every route here therefore decides for itself whose report is being asked for,
and refuses rather than guesses.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_conn, get_current_user
from app.schemas.reports import ReportSubject, WeeklyReport
from app.services import authz, report, report_store


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
    WHERE {scope}
    ORDER BY u.name, c.title
"""


@router.get("/subjects", response_model=list[ReportSubject])
def report_subjects(
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """The student/course pairs *this caller* may open a report for.

    A student gets their own enrolments; a doctor gets the students on the
    courses they teach. Nobody gets the list of everyone.

    It used to return every student and course on the platform to anybody who
    asked, which made it a staff directory — names, courses and last-seen times
    — for an endpoint whose only job is filling in a dropdown.
    """

    if current_user["role"] == "doctor":
        scope, params = "c.doctor_id = %s", (current_user["id"],)
    else:
        scope, params = "u.id = %s", (current_user["id"],)

    with conn.cursor() as cur:
        cur.execute(SUBJECTS_SQL.format(scope=scope), params)
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
    student_id: int | None = None,
    course_id: int | None = None,
    week_start: date | None = None,
    narrative: bool = True,
    refresh: bool = Query(
        False,
        description="Rewrite the narrative even if a stored one still matches "
                    "the figures.",
    ),
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """One student's week on one course.

    `student_id` defaults to whoever is asking, and that is the only value a
    student may use. It stays in the signature because a doctor legitimately
    reads their own students' weeks — but it is now a request that gets checked,
    not an identity that gets believed.

    `week_start` picks a specific window; the default is the seven days ending
    today, measured in REPORT_TIMEZONE. `course_id` picks one of several
    enrolments; the default is the most recent. `narrative=false` skips the model
    entirely, which is the fast path when only the numbers are wanted.
    """

    target = current_user["id"] if student_id is None else student_id

    if not authz.may_view_student(conn, current_user, target):
        raise HTTPException(
            status_code=403,
            detail="Not allowed to read this student's report",
        )

    result = report.build(
        conn,
        student_id=target,
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
def stored_report(
    report_id: int,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """A report a completion produced, exactly as it read when it was issued.

    Frozen rather than recomputed: "you finished the module" describes a moment,
    and re-running the query a fortnight later would quietly rewrite history.

    Report ids are sequential integers, so without the ownership check below,
    counting from 1 walks the private study record of every student who has ever
    finished anything.
    """

    owner = report_store.owner(conn, report_id)

    if owner is None:
        raise HTTPException(status_code=404, detail="Report not found")

    if not authz.may_view_student(conn, current_user, owner[0]):
        raise HTTPException(
            status_code=403,
            detail="Not allowed to read this report",
        )

    payload = report_store.get(conn, report_id)

    if payload is None:
        raise HTTPException(status_code=404, detail="Report not found")

    return WeeklyReport(**payload)
