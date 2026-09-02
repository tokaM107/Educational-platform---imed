"""Paid access: which teachers a student has subscribed to.

Enrolment says which course a student is taking. A subscription says whether
they are entitled to it. Keeping them apart matters because they change
independently — a subscription lapses without un-enrolling anybody, and a
student subscribed to a teacher may be enrolled on none, one or several of that
teacher's courses.

Access is per *teacher*, not per course: a subscription buys everything that
teacher publishes, which is what "subscribe to a teacher" means.

Two things this table deliberately does not record, and which will need columns
of their own the day the product needs them:

  when it ends    there is no expiry. A row here means access, permanently,
                  until it is deleted. A term-limited or monthly subscription
                  needs `expires_at` and a check against it.
  what was paid   there is no amount, currency, or payment reference. This
                  records the entitlement a payment produced, not the payment.
                  Reconciling with a processor needs its own table.

Both are honest gaps rather than oversights: the schema was specified as it
stands, and inventing columns nobody asked for would be worse than naming what
is missing.
"""

import logging


logger = logging.getLogger(__name__)


ACCESS_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM subscriptions
        WHERE student_id = %s AND doctor_id = %s
    )
"""

LECTURE_DOCTOR_SQL = "SELECT doctor_id, title FROM lectures WHERE id = %s"

VIDEO_DOCTOR_SQL = """
    SELECT c.doctor_id, item.title, item.is_preview
    FROM course_items AS item
    JOIN courses AS c ON c.id = item.course_id
    WHERE item.id = %s AND item.type = 'video'
"""

COURSE_DOCTOR_SQL = "SELECT doctor_id, title FROM courses WHERE id = %s"


def has_access(conn, student_id, doctor_id):
    """Is this student subscribed to this teacher?"""

    if student_id is None or doctor_id is None:
        return False

    with conn.cursor() as cur:
        cur.execute(ACCESS_SQL, (student_id, doctor_id))
        return bool(cur.fetchone()[0])


def can_watch(conn, student_id, lecture_id):
    """(allowed, doctor_id, lecture_title) for one lecture.

    A student always has access to their own teacher's material; a doctor is not
    a subscriber and is never blocked from their own lecture.
    """

    with conn.cursor() as cur:

        cur.execute(LECTURE_DOCTOR_SQL, (lecture_id,))
        row = cur.fetchone()

    if row is None:
        return False, None, None

    doctor_id, title = row

    if student_id is not None and int(student_id) == doctor_id:
        return True, doctor_id, title

    return has_access(conn, student_id, doctor_id), doctor_id, title


def can_watch_video(conn, student_id, video_id):
    """(allowed, doctor_id, video_title) for one course-item video."""

    with conn.cursor() as cur:
        cur.execute(VIDEO_DOCTOR_SQL, (video_id,))
        row = cur.fetchone()

    if row is None:
        return False, None, None

    doctor_id, title, is_preview = row
    if is_preview or (student_id is not None and int(student_id) == doctor_id):
        return True, doctor_id, title

    return has_access(conn, student_id, doctor_id), doctor_id, title


def can_enrol(conn, student_id, course_id):
    """(allowed, doctor_id, course_title) for one course."""

    with conn.cursor() as cur:

        cur.execute(COURSE_DOCTOR_SQL, (course_id,))
        row = cur.fetchone()

    if row is None:
        return False, None, None

    doctor_id, title = row

    return has_access(conn, student_id, doctor_id), doctor_id, title


def subscribe(conn, student_id, doctor_id):
    """Grant access. Returns the row, and whether it was newly created.

    Idempotent: subscribing twice is not an error and does not reset the date a
    student has been paying since.
    """

    with conn.cursor() as cur:

        cur.execute(
            "SELECT id, role FROM users WHERE id = %s", (student_id,)
        )
        student = cur.fetchone()

        cur.execute("SELECT id, role FROM users WHERE id = %s", (doctor_id,))
        doctor = cur.fetchone()

        if student is None or doctor is None:
            return None, False

        if student[1] != "student" or doctor[1] != "doctor":
            logger.warning(
                "refusing subscription: user %s is %s, user %s is %s",
                student_id, student[1], doctor_id, doctor[1],
            )
            return None, False

        cur.execute(
            """
            INSERT INTO subscriptions (student_id, doctor_id)
            VALUES (%s, %s)
            ON CONFLICT (student_id, doctor_id) DO NOTHING
            RETURNING id, subscribed_at
            """,
            (student_id, doctor_id),
        )
        row = cur.fetchone()

        created = row is not None

        if not created:
            cur.execute(
                "SELECT id, subscribed_at FROM subscriptions "
                "WHERE student_id = %s AND doctor_id = %s",
                (student_id, doctor_id),
            )
            row = cur.fetchone()

    conn.commit()

    return {
        "id": row[0],
        "student_id": student_id,
        "doctor_id": doctor_id,
        "subscribed_at": row[1],
    }, created


def cancel(conn, student_id, doctor_id):
    """Revoke access. Enrolments and history are left alone.

    Deliberately: cancelling should stop them watching, not erase that they
    studied. Their reports stay true.
    """

    with conn.cursor() as cur:

        cur.execute(
            "DELETE FROM subscriptions WHERE student_id = %s AND doctor_id = %s",
            (student_id, doctor_id),
        )
        removed = cur.rowcount

    conn.commit()

    return removed


FOR_STUDENT_SQL = """
    SELECT
        s.id,
        s.doctor_id,
        d.name,
        s.subscribed_at,
        (SELECT count(*) FROM courses c WHERE c.doctor_id = s.doctor_id),
        (SELECT count(*) FROM lectures l WHERE l.doctor_id = s.doctor_id)
    FROM subscriptions AS s
    JOIN users AS d ON d.id = s.doctor_id
    WHERE s.student_id = %s
    ORDER BY s.subscribed_at DESC
"""

FOR_DOCTOR_SQL = """
    SELECT s.id, s.student_id, u.name, u.email, s.subscribed_at
    FROM subscriptions AS s
    JOIN users AS u ON u.id = s.student_id
    WHERE s.doctor_id = %s
    ORDER BY s.subscribed_at DESC
"""


def for_student(conn, student_id):
    """The teachers this student pays for, with what that unlocks."""

    with conn.cursor() as cur:

        cur.execute(FOR_STUDENT_SQL, (student_id,))

        return [
            {
                "id": row[0],
                "doctor_id": row[1],
                "doctor_name": row[2],
                "subscribed_at": row[3],
                "courses": row[4],
                "lectures": row[5],
            }
            for row in cur.fetchall()
        ]


def for_doctor(conn, doctor_id):
    """Who is paying this teacher."""

    with conn.cursor() as cur:

        cur.execute(FOR_DOCTOR_SQL, (doctor_id,))

        return [
            {
                "id": row[0],
                "student_id": row[1],
                "student_name": row[2],
                "student_email": row[3],
                "subscribed_at": row[4],
            }
            for row in cur.fetchall()
        ]
