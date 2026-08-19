"""In-site notifications: how a report reaches the people it concerns.

A report nobody is told about is a report nobody reads. When one is generated,
two people need to know — the student it is about, and the doctor who teaches the
course — so each gets a row here and the site shows an unread count.

Deliberately a table the browser polls, not a message broker. The requirement is
"tell them next time they are on the site", and a row with a `read_at` timestamp
does that exactly: nothing is lost if the browser was closed when the report was
written, a refresh cannot duplicate it, and there is no second system to keep
running.
"""

import logging


logger = logging.getLogger(__name__)


# What each occasion says, to the student and to their doctor. Kept together so
# the two wordings cannot drift apart.
WORDING = {
    "module": {
        "student": (
            "خلّصت المقرر — تقريرك جاهز 🎓",
            "خلّصت كل محاضرات «{course}». التقرير فيه اللي غطّيته، والأجزاء اللي "
            "عدّيتها بسرعة وهتحتاجها في المراجعة.",
        ),
        "doctor": (
            "{student} خلّص «{course}»",
            "الطالب خلّص كل محاضرات المقرر. التقرير فيه نسبة المادة اللي شافها "
            "فعلاً والأجزاء اللي محتاجة متابعة معاه.",
        ),
    },
    "exam": {
        "student": (
            "خلّصت أسئلة «{lecture}» — تقريرك جاهز 📝",
            "حلّيت كل أسئلة المحاضرة. التقرير بيربط نتيجتك بالأجزاء اللي شفتها "
            "واللي مشفتهاش منها.",
        ),
        "doctor": (
            "{student} خلّص أسئلة «{lecture}»",
            "الطالب حلّ كل أسئلة المحاضرة. التقرير بيوضّح النتيجة جنب نسبة "
            "المادة اللي شافها.",
        ),
    },
}


def create(conn, user_id, kind, title, body, report_id=None, student_id=None):
    """One notification. Ignored if this user already has one for this report."""

    with conn.cursor() as cur:

        cur.execute(
            """
            INSERT INTO notifications
                (user_id, kind, title, body, report_id, student_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (user_id, kind, title, body, report_id, student_id),
        )

        row = cur.fetchone()

    return row[0] if row else None


def announce(conn, report_id, payload, kind, doctor_id):
    """Tell the student and their doctor that a report exists.

    Returns the notification ids created. Called from a background task, so it
    commits its own work.
    """

    words = WORDING.get(kind)

    if words is None:
        logger.warning("no notification wording for report kind %r", kind)
        return []

    fields = {
        "student": payload["student"]["name"],
        "course": payload["course"]["title"],
        "lecture": payload.get("lecture_title") or payload["course"]["title"],
    }

    created = []

    for role, user_id in (("student", payload["student"]["id"]), ("doctor", doctor_id)):

        if user_id is None:
            continue

        title, body = words[role]

        notification_id = create(
            conn,
            user_id=user_id,
            kind=f"{kind}_report",
            title=title.format(**fields),
            body=body.format(**fields),
            report_id=report_id,
            student_id=payload["student"]["id"],
        )

        if notification_id:
            created.append(notification_id)

    conn.commit()

    return created


LIST_SQL = """
    SELECT
        n.id,
        n.kind,
        n.title,
        n.body,
        n.report_id,
        n.student_id,
        n.read_at,
        n.created_at
    FROM notifications AS n
    WHERE n.user_id = %(user_id)s
      AND (%(unread_only)s = false OR n.read_at IS NULL)
    ORDER BY n.created_at DESC
    LIMIT %(limit)s
"""


def inbox(conn, user_id, unread_only=False, limit=30):

    with conn.cursor() as cur:

        cur.execute(
            LIST_SQL,
            {"user_id": user_id, "unread_only": unread_only, "limit": limit},
        )

        rows = cur.fetchall()

        cur.execute(
            "SELECT count(*) FROM notifications WHERE user_id = %s AND read_at IS NULL",
            (user_id,),
        )
        unread = cur.fetchone()[0]

    return {
        "unread": unread,
        "items": [
            {
                "id": row[0],
                "kind": row[1],
                "title": row[2],
                "body": row[3],
                "report_id": row[4],
                "student_id": row[5],
                "read_at": row[6],
                "created_at": row[7],
            }
            for row in rows
        ],
    }


def mark_read(conn, notification_id=None, user_id=None):
    """Mark one notification read, or every one this user has."""

    with conn.cursor() as cur:

        if notification_id is not None:
            cur.execute(
                "UPDATE notifications SET read_at = now() "
                "WHERE id = %s AND read_at IS NULL",
                (notification_id,),
            )
        else:
            cur.execute(
                "UPDATE notifications SET read_at = now() "
                "WHERE user_id = %s AND read_at IS NULL",
                (user_id,),
            )

        changed = cur.rowcount

    conn.commit()

    return changed
