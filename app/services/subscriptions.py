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

There is a second, independent source of entitlement, and it is the one the
way2APlus platform actually writes: `enrollments`. That table is filled by
access-code redemption and admin grants in the Nest API, which never touches
`subscriptions` at all. A student who bought a course with a code therefore has
every right to its videos and to the tutor over them, and would be refused by a
subscription check alone.

So course-item access asks two questions and admits on either: is this student
subscribed to the teacher, and is their enrolment in this course live. "Live"
means exactly what Nest means by it — `status = 'active'` and an `expires_at`
that is null or still in the future — because two systems disagreeing about
when access ends is worse than either rule on its own.

The subscription is checked first and short-circuits, so the common case for
the standalone product stays one query.
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

VIDEO_SCOPE_SQL = """
    SELECT c.doctor_id, item.is_preview, item.course_id
    FROM course_items AS item
    JOIN courses AS c ON c.id = item.course_id
    WHERE item.id = %s AND item.type = 'video'
"""

COURSE_VIDEOS_SQL = """
    SELECT id
    FROM course_items
    WHERE course_id = %s AND type = 'video'
      AND (%s OR is_preview)
    ORDER BY order_index, id
"""

COURSE_DOCTOR_SQL = "SELECT doctor_id, title FROM courses WHERE id = %s"

# `now()` rather than a timestamp passed in from Python: the comparison then
# happens in the database's clock, which is the same one that stamped the row.
LIVE_ENROLMENT_PREDICATE = """
    e.student_id = %s
      AND e.status = 'active'
      AND (e.expires_at IS NULL OR e.expires_at > now())
"""

COURSE_ENROLMENT_SQL = f"""
    SELECT EXISTS (
        SELECT 1 FROM enrollments AS e
        WHERE e.course_id = %s AND {LIVE_ENROLMENT_PREDICATE}
    )
"""

# Starts from the video rather than the course so `can_watch_video` does not
# have to widen its row just to reach the course id.
VIDEO_ENROLMENT_SQL = f"""
    SELECT EXISTS (
        SELECT 1
        FROM course_items AS item
        JOIN enrollments AS e ON e.course_id = item.course_id
        WHERE item.id = %s AND {LIVE_ENROLMENT_PREDICATE}
    )
"""


def has_access(conn, student_id, doctor_id):
    """Is this student subscribed to this teacher?"""

    if student_id is None or doctor_id is None:
        return False

    with conn.cursor() as cur:
        cur.execute(ACCESS_SQL, (student_id, doctor_id))
        return bool(cur.fetchone()[0])


def _enrolled(conn, student_id, sql, scope_id):
    """Shared body of the two live-enrolment checks."""

    if student_id is None or scope_id is None:
        return False

    with conn.cursor() as cur:
        cur.execute(sql, (scope_id, student_id))
        return bool(cur.fetchone()[0])


def enrolled_in_course(conn, student_id, course_id):
    """Does this student hold a live way2APlus enrolment in this course?"""

    return _enrolled(conn, student_id, COURSE_ENROLMENT_SQL, course_id)


def enrolled_for_video(conn, student_id, video_id):
    """Same question, asked from a course-item video instead of a course."""

    return _enrolled(conn, student_id, VIDEO_ENROLMENT_SQL, video_id)


def entitled_to_course(conn, student_id, doctor_id, course_id):
    """Either entitlement is enough. Subscription first: it is one query."""

    return has_access(conn, student_id, doctor_id) or enrolled_in_course(
        conn, student_id, course_id
    )


def entitled_to_video(conn, student_id, doctor_id, video_id):
    """`entitled_to_course`, reached from the video's own id."""

    return has_access(conn, student_id, doctor_id) or enrolled_for_video(
        conn, student_id, video_id
    )


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

    allowed = entitled_to_video(conn, student_id, doctor_id, video_id)
    return allowed, doctor_id, title


def accessible_course_video_ids(
    conn, student_id, video_id, enforce_subscriptions=True
):
    """Videos whose transcripts may be exposed from this video's course.

    A subscribed student (or the owning doctor) may use every video in the
    course. A student who reached the opened video only because it is a preview
    may use preview transcripts only. Disabling subscription enforcement in
    development opens the whole course, but never another course.
    """

    with conn.cursor() as cur:
        cur.execute(VIDEO_SCOPE_SQL, (video_id,))
        row = cur.fetchone()

    if row is None:
        return []

    doctor_id, is_preview, course_id = row
    owns_content = student_id is not None and int(student_id) == doctor_id
    entitled = owns_content or entitled_to_course(
        conn, student_id, doctor_id, course_id
    )
    include_paid = not enforce_subscriptions or entitled

    if enforce_subscriptions and not include_paid and not is_preview:
        return []

    with conn.cursor() as cur:
        cur.execute(COURSE_VIDEOS_SQL, (course_id, include_paid))
        return [row[0] for row in cur.fetchall()]


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
