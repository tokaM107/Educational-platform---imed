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


def test_a_subscribed_student_may_watch_a_course_item_video():
    allowed, doctor_id, title = subscriptions.can_watch_video(
        FakeConn([(7, "Anatomy video", False), (True,)]),
        student_id=3,
        video_id=11,
    )

    assert (allowed, doctor_id, title) == (True, 7, "Anatomy video")


def test_a_preview_video_does_not_require_a_subscription():
    allowed, doctor_id, title = subscriptions.can_watch_video(
        FakeConn([(7, "Preview", True)]), student_id=3, video_id=11
    )

    assert (allowed, doctor_id, title) == (True, 7, "Preview")


def test_a_non_video_course_item_is_not_accepted_as_a_video():
    allowed, doctor_id, title = subscriptions.can_watch_video(
        FakeConn([None]), student_id=3, video_id=12
    )

    assert (allowed, doctor_id, title) == (False, None, None)


def test_an_anonymous_viewer_has_no_access():
    """No identity, no entitlement — the check cannot be skipped by omission."""

    assert subscriptions.has_access(FakeConn([]), None, 7) is False
    assert subscriptions.has_access(FakeConn([]), 3, None) is False


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
