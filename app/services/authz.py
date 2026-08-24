"""Who may look at whose data.

Roles alone cannot answer this. "Is a doctor" is not a licence to read every
student on the platform — it is a licence to read the students that doctor
actually teaches, and the difference between those two readings is the entire
gap between an access check and a rubber stamp. So the questions here are about
a relationship in the data, not a string in a token.

Kept apart from the HTTP layer for the usual reason: these are the rules, and
they are the same rules whether a route, a background job or a test is asking.
"""


TEACHES_SQL = """
    SELECT 1
    FROM enrollments AS e
    JOIN courses AS c ON c.id = e.course_id
    WHERE e.student_id = %s AND c.doctor_id = %s
    LIMIT 1
"""


OWNS_LECTURE_SQL = """
    SELECT 1
    FROM lectures
    WHERE id = %s AND doctor_id = %s
    LIMIT 1
"""


OWNS_COURSE_SQL = """
    SELECT 1
    FROM courses
    WHERE id = %s AND doctor_id = %s
    LIMIT 1
"""


def _exists(conn, sql, params):

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() is not None


def teaches(conn, doctor_id, student_id):
    """Whether this doctor teaches a course this student is enrolled on."""

    return _exists(conn, TEACHES_SQL, (student_id, doctor_id))


def owns_lecture(conn, doctor_id, lecture_id):

    return _exists(conn, OWNS_LECTURE_SQL, (lecture_id, doctor_id))


def owns_course(conn, doctor_id, course_id):

    return _exists(conn, OWNS_COURSE_SQL, (course_id, doctor_id))


def may_view_student(conn, current_user, student_id):
    """Whether `current_user` may read `student_id`'s private data.

    Two ways to qualify and no others: it is your own data, or you are the
    doctor teaching them. A student is never allowed to read another student
    regardless of role, which is the case the query-parameter endpoints used to
    grant to anybody who could type a different number.
    """

    if current_user["id"] == student_id:
        return True

    if current_user["role"] == "doctor":
        return teaches(conn, current_user["id"], student_id)

    return False
