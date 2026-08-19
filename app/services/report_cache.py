"""Stored weekly-report narratives.

The same idea as `query_cache.py`, applied to the other end of the app: an
expensive model call keyed by a hash of exactly what it was computed from, so a
repeat costs a lookup instead of thirty seconds and a slice of quota.

The key here is the report's own figures. `fingerprint()` hashes the prompt text,
which is built entirely out of the measured numbers — so:

  * while a week is still running and the student keeps watching, the numbers
    move, the fingerprint moves with them, and the narrative is rewritten to
    match;
  * once the week is over nothing can change, the fingerprint settles, and the
    report becomes a fixed document that says the same thing every time it is
    opened.

That second property is the important one. A weekly report a student is supposed
to act on cannot give different advice on every refresh.
"""

import hashlib
import logging

from psycopg.types.json import Jsonb


logger = logging.getLogger(__name__)


def fingerprint(prompt):
    """Hash of the figures a narrative was written from."""

    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


def get(conn, student_id, course_id, week_start, expected=None):
    """The stored narrative, or None.

    With `expected`, only a narrative written from those exact figures is
    returned. Without it, whatever is stored for that week comes back however
    stale — which is what makes it useful as a fallback when the model is
    unreachable: an out-of-date commentary beside correct numbers beats no
    commentary at all, as long as the caller says so.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT narrative, fingerprint, generated_at
            FROM report_narratives
            WHERE student_id = %s AND course_id = %s AND week_start = %s
            """,
            (student_id, course_id, week_start),
        )

        row = cur.fetchone()

    if row is None:
        return None

    narrative, stored, generated_at = row

    if expected is not None and stored != expected:
        return None

    return {
        "narrative": narrative,
        "stale": expected is None,
        "generated_at": generated_at,
    }


def put(conn, student_id, course_id, week_start, fingerprint_, narrative):
    """Store this week's narrative, replacing any earlier one."""

    with conn.cursor() as cur:

        cur.execute(
            """
            INSERT INTO report_narratives
                (student_id, course_id, week_start, fingerprint, narrative)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (student_id, course_id, week_start) DO UPDATE
            SET fingerprint = EXCLUDED.fingerprint,
                narrative = EXCLUDED.narrative,
                generated_at = now()
            """,
            (
                student_id,
                course_id,
                week_start,
                fingerprint_,
                Jsonb(narrative),
            ),
        )

    conn.commit()
