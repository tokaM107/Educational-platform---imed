"""Course structure and enrolment: the real data a weekly report needs.

    python -m scripts.enroll list
    python -m scripts.enroll course --title "Anatomy 1" --doctor-id 1
    python -m scripts.enroll assign --course-id 1 --lectures 1,2,3
    python -m scripts.enroll add --student-id 2 --course-id 1
    python -m scripts.enroll rename --user-id 1 --name "د. سامي"

A weekly report says "watched 3 of the 5 lectures you are registered for". The
five has to come from somewhere real — which lectures belong to a course, and who
is registered on it — and that is all this writes.

It invents nothing else. In particular it does not invent watching: every figure
in a report comes from `video_events` the player recorded while a student was
actually in front of a lecture, and from questions they actually answered. A
student with no activity gets a report that says so, which is the truth and is
more useful than a fabricated week.

This is where an admin API will eventually live. Until there is one, it is a CLI.
"""

import argparse
import sys

from app.db import connection
from app.services import subscriptions


def stamp(seconds):

    if not seconds:
        return "—"

    seconds = int(seconds)

    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


# -------------------------
# list
# -------------------------


USERS_SQL = """
    SELECT
        u.id,
        u.role,
        u.name,
        u.email,
        (
            SELECT string_agg(c.title || ' (#' || c.id || ')', ', ' ORDER BY c.id)
            FROM enrollments AS e
            JOIN courses AS c ON c.id = e.course_id
            WHERE e.student_id = u.id
        ) AS enrolled,
        (SELECT count(*) FROM video_events AS v WHERE v.student_id = u.id) AS events
    FROM users AS u
    ORDER BY u.role, u.id
"""

COURSES_SQL = """
    SELECT
        c.id,
        c.title,
        d.name,
        (SELECT count(*) FROM lectures AS l WHERE l.course_id = c.id),
        (SELECT count(*) FROM enrollments AS e WHERE e.course_id = c.id)
    FROM courses AS c
    JOIN users AS d ON d.id = c.doctor_id
    ORDER BY c.id
"""

LECTURES_SQL = """
    SELECT
        l.id,
        l.course_id,
        l.title,
        l.video_url,
        count(t.embedding) AS embedded,
        COALESCE(MAX(t.end_ts), 0) AS duration
    FROM lectures AS l
    LEFT JOIN transcript_chunks AS t ON t.lecture_id = l.id
    GROUP BY l.id
    ORDER BY l.id
"""


def show(conn):
    """Everything the report depends on, and whether it is ready."""

    with conn.cursor() as cur:

        cur.execute(USERS_SQL)
        users = cur.fetchall()

        cur.execute(COURSES_SQL)
        courses = cur.fetchall()

        cur.execute(LECTURES_SQL)
        lectures = cur.fetchall()

    print("USERS")
    print(f"  {'id':>3}  {'role':<8} {'name':<26} {'events':>6}  enrolled on")

    for user_id, role, name, _email, enrolled, events in users:
        print(f"  {user_id:>3}  {role:<8} {name[:26]:<26} {events:>6}  {enrolled or '—'}")

    print()
    print("COURSES")

    if not courses:
        print("  none — create one with:  enroll course --title '...' --doctor-id N")

    for course_id, title, doctor, lecture_count, enrolled in courses:
        print(f"  {course_id:>3}  {title[:38]:<38} {doctor[:18]:<18} "
              f"{lecture_count} lecture(s), {enrolled} enrolled")

    print()
    print("LECTURES")
    print(f"  {'id':>3}  {'course':>6}  {'transcript':<10} {'length':<9} title")

    for lecture_id, course_id, title, video_url, embedded, duration in lectures:
        print(f"  {lecture_id:>3}  {str(course_id or '—'):>6}  "
              f"{('yes' if embedded else 'no'):<10} {stamp(duration):<9} "
              f"{title[:36]}{'' if video_url else '   (no video file)'}")

    unassigned = [row for row in lectures if row[1] is None]
    no_transcript = [row for row in lectures if not row[4]]

    print()

    if unassigned:
        ids = ",".join(str(row[0]) for row in unassigned)
        print(f"  {len(unassigned)} lecture(s) not in a course, so no report counts "
              f"them. Fix with:")
        print(f"    enroll assign --course-id N --lectures {ids}")

    if no_transcript:
        ids = ",".join(str(row[0]) for row in no_transcript)
        print(f"  {len(no_transcript)} lecture(s) have no transcript, so their length "
              f"is unknown and coverage")
        print(f"    cannot be measured for them: {ids}. Run rag.ingest on them.")

    if not any(row[4] for row in users if row[1] == "student"):
        pass

    students = [row for row in users if row[1] == "student"]

    if not students:
        print("  No students exist yet, so there is nobody to report on.")
    elif not any(row[4] for row in students):
        print("  No student is enrolled on a course. Enrol one with:")
        print("    enroll add --student-id N --course-id M")

    return 0


# -------------------------
# writes
# -------------------------


def create_course(conn, title, doctor_id):

    with conn.cursor() as cur:

        cur.execute("SELECT id, role FROM users WHERE id = %s", (doctor_id,))
        row = cur.fetchone()

        if row is None:
            print(f"No user {doctor_id}.")
            return 1

        if row[1] != "doctor":
            print(f"User {doctor_id} is a {row[1]}, not a doctor.")
            return 1

        cur.execute(
            "INSERT INTO courses (doctor_id, title) VALUES (%s, %s) RETURNING id",
            (doctor_id, title),
        )
        course_id = cur.fetchone()[0]

    conn.commit()

    print(f"course {course_id}: «{title}»")

    return 0


def assign(conn, course_id, lecture_ids):

    with conn.cursor() as cur:

        cur.execute(
            "SELECT title, doctor_id FROM courses WHERE id = %s", (course_id,)
        )
        row = cur.fetchone()

        if row is None:
            print(f"No course {course_id}.")
            return 1

        title, course_doctor = row

        # Which of these were recorded by somebody else. Asked before the update
        # because the answer stops existing after it, and moving a lecture
        # between doctors is worth saying out loud rather than doing quietly.
        cur.execute(
            """
            SELECT l.id, u.name
            FROM lectures l JOIN users u ON u.id = l.doctor_id
            WHERE l.id = ANY(%s) AND l.doctor_id <> %s
            """,
            (lecture_ids, course_doctor),
        )
        reassigned = cur.fetchall()

        # doctor_id travels with course_id. A lecture inside a course is taught
        # by that course's doctor — the database enforces it (migration 011), so
        # setting course_id alone here would be rejected, not merely untidy.
        cur.execute(
            "UPDATE lectures SET course_id = %s, doctor_id = %s "
            "WHERE id = ANY(%s) RETURNING id",
            (course_id, course_doctor, lecture_ids),
        )
        moved = [found[0] for found in cur.fetchall()]

    conn.commit()

    missing = sorted(set(lecture_ids) - set(moved))

    print(f"course {course_id} «{title}» now includes lecture(s) {moved or '—'}")

    for lecture_id, was in reassigned:
        print(f"  lecture {lecture_id} moved from «{was}» to the course's doctor")

    if missing:
        print(f"  no such lecture(s): {missing}")

    return 0


def add_enrolment(conn, course_id, student_id=None, student_email=None, force=False):

    with conn.cursor() as cur:

        if student_id is None:

            cur.execute("SELECT id FROM users WHERE email = %s", (student_email,))
            row = cur.fetchone()

            if row is None:
                print(f"No user with email {student_email}.")
                return 1

            student_id = row[0]

        cur.execute("SELECT name, role FROM users WHERE id = %s", (student_id,))
        student = cur.fetchone()

        if student is None:
            print(f"No user {student_id}.")
            return 1

        if student[1] != "student":
            print(f"User {student_id} ({student[0]}) is a {student[1]}. "
                  f"A report is about a student.")
            return 1

        cur.execute("SELECT title FROM courses WHERE id = %s", (course_id,))
        course = cur.fetchone()

        if course is None:
            print(f"No course {course_id}.")
            return 1

    # Enrolment needs a subscription to the course's teacher. Checked here
    # rather than only at the video, so a student is never enrolled on
    # something they cannot open.
    allowed, doctor_id, title = subscriptions.can_enrol(conn, student_id, course_id)

    if not allowed and not force:
        print(f"{student[0]} (#{student_id}) is not subscribed to the teacher of "
              f"«{title}» (doctor #{doctor_id}).")
        print("Subscribe them first:")
        print(f"    python -m scripts.enroll subscribe --student-id {student_id} "
              f"--doctor-id {doctor_id}")
        print("or pass --force to enrol anyway (they still cannot watch).")
        return 1

    with conn.cursor() as cur:

        cur.execute(
            """
            INSERT INTO enrollments (student_id, course_id) VALUES (%s, %s)
            ON CONFLICT (student_id, course_id) DO NOTHING
            """,
            (student_id, course_id),
        )

    conn.commit()

    print(f"{student[0]} (#{student_id}) is enrolled on «{course[0]}» (#{course_id})")

    if not allowed:
        print("  warning: no subscription to that teacher, so they cannot watch yet.")

    print(f"  report: /static/report.html?student_id={student_id}")

    return 0


def rename(conn, user_id=None, name=None, course_id=None, title=None):
    """Fix a placeholder name. Reports print these, so they should be real."""

    with conn.cursor() as cur:

        if user_id is not None:

            cur.execute(
                "UPDATE users SET name = %s WHERE id = %s RETURNING name", (name, user_id)
            )
            row = cur.fetchone()

            if row is None:
                print(f"No user {user_id}.")
                return 1

            print(f"user {user_id} is now «{row[0]}»")

        if course_id is not None:

            cur.execute(
                "UPDATE courses SET title = %s WHERE id = %s RETURNING title",
                (title, course_id),
            )
            row = cur.fetchone()

            if row is None:
                print(f"No course {course_id}.")
                return 1

            print(f"course {course_id} is now «{row[0]}»")

    conn.commit()

    return 0


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show courses, lectures, students and enrolments")

    course = sub.add_parser("course", help="create a course")
    course.add_argument("--title", required=True)
    course.add_argument("--doctor-id", type=int, required=True)

    assign_parser = sub.add_parser("assign", help="put lectures into a course")
    assign_parser.add_argument("--course-id", type=int, required=True)
    assign_parser.add_argument(
        "--lectures", required=True, help="comma-separated lecture ids, e.g. 1,2,3"
    )

    add = sub.add_parser("add", help="enrol a student on a course")
    add.add_argument("--course-id", type=int, required=True)
    add.add_argument("--student-id", type=int)
    add.add_argument("--student-email")
    add.add_argument("--force", action="store_true",
                     help="enrol even without a subscription to the teacher")

    subscribe_parser = sub.add_parser(
        "subscribe", help="give a student paid access to a teacher")
    subscribe_parser.add_argument("--student-id", type=int, required=True)
    subscribe_parser.add_argument("--doctor-id", type=int, required=True)

    unsubscribe_parser = sub.add_parser("unsubscribe", help="revoke that access")
    unsubscribe_parser.add_argument("--student-id", type=int, required=True)
    unsubscribe_parser.add_argument("--doctor-id", type=int, required=True)

    rename_parser = sub.add_parser("rename", help="fix a person's or course's name")
    rename_parser.add_argument("--user-id", type=int)
    rename_parser.add_argument("--name")
    rename_parser.add_argument("--course-id", type=int)
    rename_parser.add_argument("--title")

    args = parser.parse_args()

    if args.command == "add" and not (args.student_id or args.student_email):
        parser.error("add needs --student-id or --student-email")

    if args.command == "rename" and not (
        (args.user_id and args.name) or (args.course_id and args.title)
    ):
        parser.error("rename needs --user-id with --name, or --course-id with --title")

    with connection() as conn:

        if args.command == "list":
            return show(conn)

        if args.command == "course":
            return create_course(conn, args.title, args.doctor_id)

        if args.command == "assign":

            try:
                ids = [int(part) for part in args.lectures.split(",") if part.strip()]
            except ValueError:
                parser.error("--lectures must be comma-separated numbers")

            return assign(conn, args.course_id, ids)

        if args.command == "add":
            return add_enrolment(conn, args.course_id, args.student_id,
                                 args.student_email, args.force)

        if args.command == "subscribe":

            row, created = subscriptions.subscribe(
                conn, args.student_id, args.doctor_id)

            if row is None:
                print("Needs an existing student and an existing doctor.")
                return 1

            print(f"student {args.student_id} "
                  f"{'subscribed to' if created else 'already subscribed to'} "
                  f"doctor {args.doctor_id} (since {row['subscribed_at']:%Y-%m-%d})")
            return 0

        if args.command == "unsubscribe":

            removed = subscriptions.cancel(conn, args.student_id, args.doctor_id)
            print(f"{removed} subscription(s) cancelled")
            return 0 if removed else 1

        if args.command == "rename":
            return rename(conn, args.user_id, args.name, args.course_id, args.title)

    return 0


if __name__ == "__main__":
    sys.exit(main())
