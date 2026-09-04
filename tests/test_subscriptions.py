"""Paid access: who may watch what, and what the check refuses."""

from app.services import subscriptions


class FakeCursor:

    def __init__(self, rows):
        self.rows = list(rows)
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:

    def __init__(self, rows):
        self._cursor = FakeCursor(rows)

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


def test_a_subscribed_student_may_watch():

    allowed, doctor_id, title = subscriptions.can_watch(
        FakeConn([(7, "Anatomy 1"), (True,)]), student_id=3, lecture_id=1
    )

    assert (allowed, doctor_id, title) == (True, 7, "Anatomy 1")


def test_an_unsubscribed_student_may_not():

    allowed, _, _ = subscriptions.can_watch(
        FakeConn([(7, "Anatomy 1"), (False,)]), student_id=3, lecture_id=1
    )

    assert allowed is False


def test_a_doctor_is_never_locked_out_of_their_own_lecture():
    """They are not a subscriber to themselves, and must not need to be."""

    allowed, doctor_id, _ = subscriptions.can_watch(
        FakeConn([(7, "Anatomy 1")]), student_id=7, lecture_id=1
    )

    assert allowed is True
    assert doctor_id == 7


def test_an_unknown_lecture_is_refused_rather_than_allowed():
    """Fail closed: a missing row must not read as "no restriction"."""

    allowed, doctor_id, title = subscriptions.can_watch(
        FakeConn([None]), student_id=3, lecture_id=999
    )

    assert (allowed, doctor_id, title) == (False, None, None)


def test_an_enrolled_student_may_watch_a_course_item_video():
    allowed, doctor_id, title = subscriptions.can_watch_video(
        FakeConn([(7, "Anatomy video", False, 16), (True,)]),
        student_id=3,
        video_id=11,
    )

    assert (allowed, doctor_id, title) == (True, 7, "Anatomy video")


def test_a_preview_video_does_not_require_an_enrollment():
    allowed, doctor_id, title = subscriptions.can_watch_video(
        FakeConn([(7, "Preview", True, 16)]), student_id=3, video_id=11
    )

    assert (allowed, doctor_id, title) == (True, 7, "Preview")


def test_a_non_video_course_item_is_not_accepted_as_a_video():
    allowed, doctor_id, title = subscriptions.can_watch_video(
        FakeConn([None]), student_id=3, video_id=12
    )

    assert (allowed, doctor_id, title) == (False, None, None)


def test_enrolled_student_can_search_every_video_in_the_same_course():
    conn = FakeConn([
        (7, False, 16),
        (True,),
        (11,),
        (12,),
    ])

    video_ids = subscriptions.accessible_course_video_ids(
        conn, student_id=3, video_id=11, enforce_subscriptions=True
    )

    assert video_ids == [11, 12]
    assert conn._cursor.sql[-1][1] == (16, True)


def test_preview_user_can_search_preview_videos_but_not_paid_siblings():
    conn = FakeConn([
        (7, True, 16),
        (False,),
        (11,),
    ])

    video_ids = subscriptions.accessible_course_video_ids(
        conn, student_id=3, video_id=11, enforce_subscriptions=True
    )

    assert video_ids == [11]
    assert conn._cursor.sql[-1][1] == (16, False)


def test_paid_video_without_enrollment_has_no_transcript_scope():
    video_ids = subscriptions.accessible_course_video_ids(
        FakeConn([(7, False, 16), (False,)]),
        student_id=3,
        video_id=11,
        enforce_subscriptions=True,
    )

    assert video_ids == []


def test_course_access_requires_active_unexpired_enrollment():
    conn = FakeConn([(True,)])

    assert subscriptions.has_course_access(conn, student_id=3, course_id=16)
    sql, params = conn._cursor.sql[0]
    assert "status = 'active'" in sql
    assert "expires_at > CURRENT_TIMESTAMP" in sql
    assert params == (3, 16)


def test_an_anonymous_viewer_has_no_access():
    """No identity, no entitlement — the check cannot be skipped by omission."""

    assert subscriptions.has_access(FakeConn([]), None, 7) is False
    assert subscriptions.has_access(FakeConn([]), 3, None) is False
    assert subscriptions.has_course_access(FakeConn([]), None, 16) is False
    assert subscriptions.has_course_access(FakeConn([]), 3, None) is False


def test_enrolment_needs_a_subscription_to_that_course_s_teacher():

    allowed, doctor_id, title = subscriptions.can_enrol(
        FakeConn([(7, "Physiology 1"), (False,)]), student_id=3, course_id=2
    )

    assert allowed is False
    assert (doctor_id, title) == (7, "Physiology 1")


def test_subscribing_a_doctor_as_a_student_is_refused():
    """The pair is student -> doctor; the roles are not interchangeable."""

    row, created = subscriptions.subscribe(
        FakeConn([(1, "doctor"), (2, "student")]), student_id=1, doctor_id=2
    )

    assert row is None
    assert created is False


def test_subscribing_an_unknown_user_is_refused():

    row, created = subscriptions.subscribe(
        FakeConn([None, (2, "doctor")]), student_id=999, doctor_id=2
    )

    assert row is None
    assert created is False
