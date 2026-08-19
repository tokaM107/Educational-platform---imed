"""Take the fabricated demo rows back out of the database.

    python -m scripts.remove_demo_data            # list what it would remove
    python -m scripts.remove_demo_data --apply    # remove it

An earlier version of this project shipped a seed script that invented a week of
study: students who never existed, lectures with no video and a one-line
placeholder transcript, questions nobody wrote, and `video_events` describing
watching that never happened. None of that belongs in a database that is meant to
hold real engagement, so this removes it.

What counts as fabricated is deliberately narrow, and nothing is deleted on a
guess:

  students      by the exact demo email addresses the seed used
  lectures      no video file AND a single transcript chunk marked "[demo]"
  questions     on a real lecture, by exact stem text the seed inserted
  events        session ids beginning "seed-"
  topics        only if nothing references them once the above is gone

Anything a real person or the real ingest pipeline produced is left alone. A
lecture with an embedded transcript is never touched, and a student with captured
events keeps them.

Two things it cannot judge and only reports: a name that was overwritten (the
placeholder "Test Doctor" was renamed) and an invented course title. Fix those
with `python -m scripts.enroll rename`.
"""

import argparse
import sys

from app.db import connection


DEMO_EMAILS = ("student.demo@imed.local", "student.week@imed.local")

# Questions the seed added to a lecture that is otherwise real, so they cannot be
# found by looking at the lecture. Matched on exact stem.
DEMO_STEMS = (
    "الوضع التشريحي القياسي الكفوف بتبقى فيه ناحية إيه؟",
    "العظم الطويل بيتكوّن من كام جزء رئيسي؟",
    "الـ diaphysis هو أنهي جزء في العظمة الطويلة؟",
)

DEMO_TOPICS = (
    "تصنيف العظام",
    "العمود الفقري",
    "المفاصل",
    "العضلات الهيكلية",
    "مصطلحات تشريحية",
)

SESSION_PREFIX = "seed-"


# A lecture the seed created: never given a video file, and its only transcript
# chunk is the unembedded marker the seed inserted to give it a length. A really
# ingested lecture has embedded chunks and fails both halves.
DEMO_LECTURES_SQL = """
    SELECT l.id, l.title
    FROM lectures AS l
    JOIN transcript_chunks AS t ON t.lecture_id = l.id
    WHERE l.video_url IS NULL
    GROUP BY l.id, l.title
    HAVING count(t.id) = 1
       AND count(t.embedding) = 0
       AND max(t.text) LIKE '[demo] %'
"""


def survey(cur):
    """Everything that will go, with the counts that will go with it."""

    found = {}

    cur.execute(
        "SELECT id, name, email FROM users WHERE email = ANY(%s)", (list(DEMO_EMAILS),)
    )
    found["students"] = cur.fetchall()

    cur.execute(DEMO_LECTURES_SQL)
    found["lectures"] = cur.fetchall()

    cur.execute(
        "SELECT id, lecture_id FROM questions WHERE stem = ANY(%s)", (list(DEMO_STEMS),)
    )
    found["questions"] = cur.fetchall()

    cur.execute(
        "SELECT count(*) FROM video_events WHERE session_id LIKE %s",
        (f"{SESSION_PREFIX}%",),
    )
    found["seeded_events"] = cur.fetchone()[0]

    student_ids = [row[0] for row in found["students"]]

    if student_ids:

        cur.execute(
            "SELECT count(*) FROM video_events WHERE student_id = ANY(%s) "
            "AND session_id NOT LIKE %s",
            (student_ids, f"{SESSION_PREFIX}%"),
        )
        found["real_events_of_demo_students"] = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM question_attempts WHERE student_id = ANY(%s)",
            (student_ids,),
        )
        found["attempts"] = cur.fetchone()[0]

    else:
        found["real_events_of_demo_students"] = 0
        found["attempts"] = 0

    lecture_ids = [row[0] for row in found["lectures"]]

    if lecture_ids:

        cur.execute(
            "SELECT count(*) FROM questions WHERE lecture_id = ANY(%s)", (lecture_ids,)
        )
        found["questions_on_demo_lectures"] = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM video_events WHERE lecture_id = ANY(%s)", (lecture_ids,)
        )
        found["events_on_demo_lectures"] = cur.fetchone()[0]

    else:
        found["questions_on_demo_lectures"] = 0
        found["events_on_demo_lectures"] = 0

    return found


def report(found, cur):

    print("WOULD REMOVE")
    print()

    if found["students"]:
        print("  students (invented by the seed):")
        for user_id, name, email in found["students"]:
            print(f"    #{user_id}  {name}  <{email}>")
        if found["real_events_of_demo_students"]:
            print(f"    …along with {found['real_events_of_demo_students']} event(s) "
                  f"these students captured for real,")
            print(f"      because the row they belong to is going. Keep a student with "
                  f"--keep-student ID.")
        if found["attempts"]:
            print(f"    …and {found['attempts']} question attempt(s) of theirs.")
    else:
        print("  students: none")

    print()

    if found["lectures"]:
        print("  lectures (no video, placeholder transcript):")
        for lecture_id, title in found["lectures"]:
            print(f"    #{lecture_id}  {title}")
        print(f"    …with {found['questions_on_demo_lectures']} question(s) and "
              f"{found['events_on_demo_lectures']} event(s) on them (cascade).")
    else:
        print("  lectures: none")

    print()
    print(f"  seeded events (session id '{SESSION_PREFIX}…'): "
          f"{found['seeded_events']}")
    print(f"  questions added to real lectures: {len(found['questions'])}")

    cur.execute("SELECT name FROM users WHERE name = 'د. أحمد سليم'")

    if cur.fetchone():
        print()
        print("  NOT REMOVED, but worth knowing: the placeholder doctor 'Test Doctor'")
        print("  was renamed to «د. أحمد سليم» by the seed, and the course title was")
        print("  invented. Reports print both. Set the real ones with:")
        print("    python -m scripts.enroll rename --user-id 1 --name '...'")
        print("    python -m scripts.enroll rename --course-id 1 --title '...'")


def remove(cur, found, keep_students):
    """Delete in dependency order: only lectures and courses cascade."""

    student_ids = [
        row[0] for row in found["students"] if row[0] not in keep_students
    ]

    cur.execute(
        "DELETE FROM video_events WHERE session_id LIKE %s", (f"{SESSION_PREFIX}%",)
    )
    seeded = cur.rowcount

    if student_ids:
        # users has no ON DELETE CASCADE, so every child goes first.
        cur.execute("DELETE FROM report_narratives WHERE student_id = ANY(%s)",
                    (student_ids,))
        cur.execute("DELETE FROM question_attempts WHERE student_id = ANY(%s)",
                    (student_ids,))
        cur.execute("DELETE FROM video_events WHERE student_id = ANY(%s)",
                    (student_ids,))
        cur.execute("DELETE FROM enrollments WHERE student_id = ANY(%s)", (student_ids,))
        cur.execute("DELETE FROM users WHERE id = ANY(%s)", (student_ids,))

    if found["questions"]:
        cur.execute(
            "DELETE FROM questions WHERE id = ANY(%s)",
            ([row[0] for row in found["questions"]],),
        )

    lecture_ids = [row[0] for row in found["lectures"]]

    if lecture_ids:
        # lectures cascades to transcript_chunks, questions and video_events.
        cur.execute("DELETE FROM lectures WHERE id = ANY(%s)", (lecture_ids,))

    # Topics the seed created, but only once nothing points at them.
    cur.execute(
        """
        DELETE FROM topics
        WHERE name = ANY(%s)
          AND NOT EXISTS (SELECT 1 FROM questions WHERE questions.topic_id = topics.id)
        RETURNING name
        """,
        (list(DEMO_TOPICS),),
    )
    topics = [row[0] for row in cur.fetchall()]

    return seeded, student_ids, lecture_ids, topics


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually delete")
    parser.add_argument(
        "--keep-student",
        type=int,
        action="append",
        default=[],
        help="keep this student (and their captured events); repeatable",
    )
    args = parser.parse_args()

    with connection() as conn:

        with conn.cursor() as cur:

            found = survey(cur)

            if not args.apply:
                report(found, cur)
                print()
                print("Nothing changed. Re-run with --apply to remove it.")
                return 0

            seeded, students, lectures, topics = remove(cur, found, set(args.keep_student))

        conn.commit()

    print(f"removed  {seeded} seeded event(s)")
    print(f"         {len(students)} student(s) {students or ''}")
    print(f"         {len(lectures)} lecture(s) {lectures or ''}")
    print(f"         {len(topics)} unused topic(s)")
    print()
    print("Check what is left with:  python -m scripts.enroll list")

    return 0


if __name__ == "__main__":
    sys.exit(main())
