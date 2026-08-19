"""When a completion should fire a report, and when it should not."""

import pytest

from app.services import triggers


class FakeCursor:
    """Answers the next queued row, ignoring the SQL."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.seen = []

    def execute(self, sql, params=None):
        self.seen.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:

    def __init__(self, rows):
        self._cursor = FakeCursor(rows)

    def cursor(self):
        return self._cursor


# --- module: the last lecture of a course -------------------------------


def test_module_fires_when_the_last_lecture_is_finished():

    course_id, finished = triggers.module_finished(
        FakeConn([(7, 5, 5)]), student_id=1, lecture_id=3
    )

    assert (course_id, finished) == (7, True)


def test_module_does_not_fire_partway_through():

    _, finished = triggers.module_finished(
        FakeConn([(7, 5, 4)]), student_id=1, lecture_id=3
    )

    assert finished is False


def test_replaying_a_finished_lecture_does_not_fire_again():
    """`complete` repeats; the count is over distinct lectures, so it holds."""

    # Four of five done, and the event was for a lecture already counted.
    _, finished = triggers.module_finished(
        FakeConn([(7, 5, 4)]), student_id=1, lecture_id=3
    )

    assert finished is False


def test_a_lecture_outside_any_course_fires_nothing():
    """course_id is nullable, and a loose lecture has no module to complete."""

    course_id, finished = triggers.module_finished(
        FakeConn([(None, 0, 0)]), student_id=1, lecture_id=3
    )

    assert course_id is None
    assert finished is False


def test_an_empty_course_never_completes():
    """0 of 0 is not an achievement to announce."""

    _, finished = triggers.module_finished(
        FakeConn([(7, 0, 0)]), student_id=1, lecture_id=3
    )

    assert finished is False


# --- exam: the last question of a lecture -------------------------------


def test_exam_fires_on_the_last_unanswered_question():

    assert triggers.exam_finished(FakeConn([(4, 4)]), student_id=1, lecture_id=2) is True


def test_exam_does_not_fire_with_questions_outstanding():

    assert triggers.exam_finished(FakeConn([(4, 3)]), student_id=1, lecture_id=2) is False


def test_a_lecture_with_no_questions_never_fires():
    """Nothing to finish, so there is no completion to report."""

    assert triggers.exam_finished(FakeConn([(0, 0)]), student_id=1, lecture_id=2) is False


# --- failure containment -------------------------------------------------


def test_a_broken_background_job_never_raises(monkeypatch):
    """It runs after the response: raising would only lose the report."""

    def explode():
        raise RuntimeError("database on fire")

    monkeypatch.setattr(triggers, "connection", explode)

    assert triggers.after_lecture_completed(1, 2) is None
    assert triggers.after_question_attempt(1, 2) is None
