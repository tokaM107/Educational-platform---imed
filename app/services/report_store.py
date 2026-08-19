"""Frozen reports: the ones a moment produced rather than a calendar.

A weekly report covers a rolling window, so its numbers are recomputed on every
read and only the narrative is cached. A completion report is the opposite. "You
have finished the module" describes an instant: recomputed a fortnight later,
after the student has gone back and rewatched half of it, the same query returns
different numbers and the document no longer says what it said when it was
issued. So these are stored whole.

The uniqueness rule lives in the database (`idx_reports_once`), because the event
that triggers one can legitimately arrive twice — a student can replay the last
minute of the last lecture — and two identical reports would mean two
notifications for one achievement.
"""

import json
import logging

from psycopg.types.json import Jsonb


logger = logging.getLogger(__name__)


def _dumps(payload):
    """JSON for the payload column.

    A report carries dates and timestamps — the week's bounds, when each lecture
    was opened — and the default encoder refuses both. `default=str` writes them
    as ISO strings, which is what the response schema parses back into dates
    anyway, so the round trip is lossless in the only direction that matters.
    """

    return json.dumps(payload, default=str, ensure_ascii=False)


def exists(conn, student_id, course_id, kind, lecture_id=None):
    """Has this completion already been reported?"""

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT id FROM reports
            WHERE student_id = %s AND course_id = %s AND kind = %s
              AND COALESCE(lecture_id, 0) = COALESCE(%s, 0)
            """,
            (student_id, course_id, kind, lecture_id),
        )

        row = cur.fetchone()

    return row[0] if row else None


def store(conn, student_id, course_id, kind, payload, lecture_id=None):
    """Freeze a report. Returns its id, or None if one already existed.

    `ON CONFLICT DO NOTHING` rather than a check-then-insert: two events arriving
    together would both pass the check, and the index is the only thing that can
    actually settle it.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            INSERT INTO reports (student_id, course_id, kind, lecture_id, payload)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (student_id, course_id, kind, lecture_id, Jsonb(payload, dumps=_dumps)),
        )

        row = cur.fetchone()

    conn.commit()

    return row[0] if row else None


def get(conn, report_id):
    """A stored report, exactly as it read when it was issued."""

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT r.id, r.student_id, r.course_id, r.kind, r.lecture_id,
                   r.payload, r.generated_at
            FROM reports AS r
            WHERE r.id = %s
            """,
            (report_id,),
        )

        row = cur.fetchone()

    if row is None:
        return None

    payload = dict(row[5])
    payload["report_id"] = row[0]
    payload["kind"] = row[3]
    payload["generated_at"] = row[6]

    return payload


def for_student(conn, student_id, limit=20):
    """Their completion reports, newest first."""

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT id, kind, lecture_id, generated_at
            FROM reports
            WHERE student_id = %s
            ORDER BY generated_at DESC
            LIMIT %s
            """,
            (student_id, limit),
        )

        return [
            {
                "report_id": row[0],
                "kind": row[1],
                "lecture_id": row[2],
                "generated_at": row[3],
            }
            for row in cur.fetchall()
        ]
