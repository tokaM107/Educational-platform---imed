"""Reports fired by what a student did, not by the calendar.

The pipeline is the one that already exists: Stage 1 replays `video_events` into
figures (`report.py`), Stage 2 hands those figures to the model to narrate. Only
the trigger is different — instead of a weekly cron, a report is produced the
moment a student finishes something:

    finishing the last lecture of a course   -> a module report
    answering the last question of a lecture -> an exam report

Both checks are "is this the one that completed the set", which is a counting
question the database can answer in a single round trip. Neither runs inline: the
endpoints hand these to FastAPI's BackgroundTasks so the student's request
returns immediately and the model call happens after the response.

Because they run after the response, they cannot use the request's connection —
that is returned to the pool when the request ends — so each opens its own.
Everything is wrapped: a failure here must never surface as a failed event
capture or a lost quiz answer, and the report can always be produced later.
"""

import logging

from app.db import connection
from app.services import notifications, report, report_store


logger = logging.getLogger(__name__)


MODULE_SQL = """
    SELECT
        l.course_id,
        (SELECT count(*) FROM lectures WHERE course_id = l.course_id),
        (
            SELECT count(DISTINCT v.lecture_id)
            FROM video_events AS v
            JOIN lectures AS inner_l ON inner_l.id = v.lecture_id
            WHERE v.student_id = %(student_id)s
              AND inner_l.course_id = l.course_id
              AND v.event_type = 'complete'
        )
    FROM lectures AS l
    WHERE l.id = %(lecture_id)s
"""

EXAM_SQL = """
    SELECT
        count(*),
        count(*) FILTER (
            WHERE EXISTS (
                SELECT 1 FROM question_attempts AS a
                WHERE a.question_id = q.id AND a.student_id = %(student_id)s
            )
        )
    FROM questions AS q
    WHERE q.lecture_id = %(lecture_id)s
"""

DOCTOR_SQL = "SELECT doctor_id FROM courses WHERE id = %s"

LECTURE_COURSE_SQL = "SELECT course_id FROM lectures WHERE id = %s"


def module_finished(conn, student_id, lecture_id):
    """(course_id, True) when this completion was the last one the course needed.

    Counted over distinct lectures with a `complete` event, so replaying the end
    of one already-finished lecture cannot push the count over the line.
    """

    with conn.cursor() as cur:
        cur.execute(MODULE_SQL, {"student_id": student_id, "lecture_id": lecture_id})
        row = cur.fetchone()

    if row is None or row[0] is None:
        return None, False              # lecture is not part of any course

    course_id, total, done = row

    return course_id, bool(total) and done >= total


def exam_finished(conn, student_id, lecture_id):
    """True when every question on this lecture has now been attempted.

    A lecture with no questions is never "finished" — there is nothing to finish,
    and firing a report for it would announce an achievement that did not happen.
    """

    with conn.cursor() as cur:
        cur.execute(EXAM_SQL, {"student_id": student_id, "lecture_id": lecture_id})
        total, answered = cur.fetchone()

    return bool(total) and answered >= total


def _course_of(conn, lecture_id):

    with conn.cursor() as cur:
        cur.execute(LECTURE_COURSE_SQL, (lecture_id,))
        row = cur.fetchone()

    return row[0] if row and row[0] else None


def _doctor_of(conn, course_id):

    with conn.cursor() as cur:
        cur.execute(DOCTOR_SQL, (course_id,))
        row = cur.fetchone()

    return row[0] if row else None


def _produce(conn, student_id, course_id, kind, lecture_id=None):
    """Stage 1 + Stage 2 + freeze + notify. Returns the report id, or None."""

    if report_store.exists(conn, student_id, course_id, kind, lecture_id):
        logger.info(
            "%s report already exists for student %s course %s lecture %s",
            kind, student_id, course_id, lecture_id,
        )
        return None

    payload = report.build(
        conn,
        student_id=student_id,
        course_id=course_id,
        window=report.full_window(conn, student_id, course_id),
        kind=kind,
        lecture_id=lecture_id,
    )

    if payload is None or payload.get("totals") is None:
        logger.warning("no report to build for student %s course %s", student_id, course_id)
        return None

    report_id = report_store.store(
        conn, student_id, course_id, kind, payload, lecture_id=lecture_id
    )

    if report_id is None:
        # Another request got there first between the check and the insert.
        return None

    notifications.announce(
        conn, report_id, payload, kind, doctor_id=_doctor_of(conn, course_id)
    )

    logger.info("%s report %s ready for student %s", kind, report_id, student_id)

    return report_id


def after_lecture_completed(student_id, lecture_id):
    """Background task for a `complete` video event."""

    try:
        with connection() as conn:

            course_id, finished = module_finished(conn, student_id, lecture_id)

            if not finished:
                return None

            return _produce(conn, student_id, course_id, "module")

    except Exception:                    # noqa: BLE001 - never surface to the student
        logger.exception(
            "module report failed for student %s lecture %s", student_id, lecture_id
        )
        return None


def after_question_attempt(student_id, lecture_id):
    """Background task for a quiz answer."""

    try:
        with connection() as conn:

            if not exam_finished(conn, student_id, lecture_id):
                return None

            course_id = _course_of(conn, lecture_id)

            if course_id is None:
                return None

            return _produce(conn, student_id, course_id, "exam", lecture_id=lecture_id)

    except Exception:                    # noqa: BLE001
        logger.exception(
            "exam report failed for student %s lecture %s", student_id, lecture_id
        )
        return None
