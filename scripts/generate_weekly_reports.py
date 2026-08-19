"""Generate the week's reports for everyone, ahead of anyone asking.

    python -m scripts.generate_weekly_reports
    python -m scripts.generate_weekly_reports --week-start 2026-08-11
    python -m scripts.generate_weekly_reports --student-id 2 --refresh

Writing a narrative takes a model call of about half a minute. Doing that while a
student waits on a spinner is the wrong time for it, so this runs through every
enrolment and stores each narrative in `report_narratives`; the page then loads
from the store in milliseconds.

Run it once after the week closes — the natural cron is early on the first day of
the new week:

    5 6 * * 6  cd /srv/app && .venv/bin/python -m scripts.generate_weekly_reports \\
                   --week-start "$(date -d 'last saturday -7 days' +%%F)" >> log 2>&1

Safe to run repeatedly and safe to run mid-week. A narrative whose figures have
not moved since last time is left alone, so a second run costs one cheap replay
per student and no model calls at all. `--refresh` overrides that.

Exit status is 0 when every report was produced, 1 if any failed, so a cron
wrapper can alert on it.
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta

from app.db import connection
from app.services import report


logger = logging.getLogger("weekly_reports")


ENROLMENTS_SQL = """
    SELECT en.student_id, u.name, en.course_id, c.title
    FROM enrollments AS en
    JOIN users AS u ON u.id = en.student_id
    JOIN courses AS c ON c.id = en.course_id
    WHERE (%(student_id)s::int IS NULL OR en.student_id = %(student_id)s)
      AND (%(course_id)s::int IS NULL OR en.course_id = %(course_id)s)
    ORDER BY u.name, c.title
"""


def enrolments(conn, student_id=None, course_id=None):

    with conn.cursor() as cur:
        cur.execute(ENROLMENTS_SQL, {"student_id": student_id, "course_id": course_id})
        return cur.fetchall()


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--week-start",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        help="first day of the week to report on (default: the last 7 days)",
    )
    parser.add_argument("--student-id", type=int, help="just this student")
    parser.add_argument("--course-id", type=int, help="just this course")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="rewrite narratives even when the figures have not changed",
    )
    parser.add_argument(
        "--numbers-only",
        action="store_true",
        help="compute and print the figures without calling the model",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    window = report.week_window(args.week_start)

    logger.info(
        "week %s .. %s (%s)",
        window.first_day,
        window.last_day,
        report.zone(),
    )

    failures = 0
    written = 0

    with connection() as conn:

        rows = enrolments(conn, args.student_id, args.course_id)

        if not rows:
            logger.warning(
                "no enrolments found — nobody is registered on a course, so "
                "there is nothing to report on"
            )
            return 0

        for student_id, student_name, course_id, course_title in rows:

            started = datetime.now()

            try:
                result = report.build(
                    conn,
                    student_id=student_id,
                    course_id=course_id,
                    week_start=window.first_day,
                    with_narrative=not args.numbers_only,
                    refresh=args.refresh,
                )

            except Exception as error:            # noqa: BLE001 - one bad row must
                failures += 1                      # not stop the whole run
                logger.exception(
                    "%s / %s failed: %s", student_name, course_title, error
                )
                continue

            if result is None or result["totals"] is None:
                logger.warning("%s / %s: nothing to report", student_name, course_title)
                continue

            totals = result["totals"]
            elapsed = (datetime.now() - started).total_seconds()

            written += 1

            logger.info(
                "%-22s %-28s watched %5.1f min · covered %s%% · %d/%d lectures "
                "· narrative %s (%.1fs)",
                student_name[:22],
                course_title[:28],
                totals["watch_time_seconds"] / 60,
                totals["coverage_percentage"],
                totals["lectures_opened"],
                totals["lectures_registered"],
                "yes" if result["narrative"] else "no",
                elapsed,
            )

            if result["notice"]:
                logger.warning("  notice: %s", result["notice"])

    logger.info("%d report(s) produced, %d failed", written, failures)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
