"""Weekly report shaping: windows, rates, per-day rows, topic verdicts."""

from datetime import date, datetime, timedelta

from app.services import report, report_cache


def test_week_window_covers_seven_local_days():

    window = report.week_window(date(2026, 8, 11))

    assert window.first_day == date(2026, 8, 11)
    assert window.last_day == date(2026, 8, 17)

    # Seven local days. Allow an hour either side: a week containing a
    # daylight-saving change is still seven days, just not 168 hours.
    span = window.until - window.since
    assert timedelta(days=6, hours=23) <= span <= timedelta(days=7, hours=1)

    # Half-open, so this week and the next cannot both claim the same event.
    assert report.week_window(date(2026, 8, 18)).since == window.until


def test_week_window_is_anchored_to_local_midnight():
    """A 23:00 session is that evening's work, not the next day's."""

    window = report.week_window(date(2026, 8, 11))

    # Cairo is ahead of UTC, so a local midnight start is the previous UTC day.
    assert window.since < datetime.combine(
        date(2026, 8, 11), datetime.min.time(), tzinfo=window.since.tzinfo
    ).replace(hour=1)


def test_rate_separates_unknown_from_zero():
    """None means "no denominator"; 0 means "measured, and it is zero"."""

    assert report._rate(30, 60) == 50.0
    assert report._rate(0, 60) == 0.0
    assert report._rate(30, 0) is None
    assert report._rate(30, None) is None


def test_daily_rows_include_the_empty_days():
    """The week is drawn as a shape, so a day with no study still needs a row."""

    window = report.week_window(date(2026, 8, 11))

    rows = report._daily_out(
        window,
        {date(2026, 8, 11): 600.0, date(2026, 8, 14): 1200.0},
        {date(2026, 8, 11), date(2026, 8, 14)},
    )

    assert len(rows) == 7
    assert [row["date"] for row in rows][0] == date(2026, 8, 11)
    assert rows[0]["watch_time_seconds"] == 600.0
    assert rows[0]["active"] is True
    assert rows[1]["watch_time_seconds"] == 0.0
    assert rows[1]["active"] is False


def attempt(question_id, topic, correct):
    return {
        "lecture_id": 1,
        "question_id": question_id,
        "topic": topic,
        "is_correct": correct,
        "difficulty": "medium",
    }


def test_a_question_answered_right_after_a_retry_counts_once():
    """Wrong then right is one question learned, not a 50% student."""

    score = report._score([attempt(1, "t", False), attempt(1, "t", True)])

    assert score["questions_attempted"] == 1
    assert score["questions_correct"] == 1
    assert score["attempts"] == 2
    assert score["accuracy"] == 100.0


def test_a_thin_topic_is_not_called_a_strength_or_a_gap():
    """One lucky answer is not a strength, and the page must be told so."""

    rows = report.topic_breakdown(
        [
            attempt(1, "قليل", True),
            attempt(2, "كفاية", True),
            attempt(3, "كفاية", False),
        ]
    )

    thin = next(row for row in rows if row["topic"] == "قليل")
    enough = next(row for row in rows if row["topic"] == "كفاية")

    assert thin["accuracy"] == 100.0 and thin["conclusive"] is False
    assert enough["accuracy"] == 50.0 and enough["conclusive"] is True


def test_topics_are_ordered_worst_first():

    rows = report.topic_breakdown(
        [
            attempt(1, "good", True),
            attempt(2, "good", True),
            attempt(3, "bad", False),
            attempt(4, "bad", False),
        ]
    )

    assert [row["topic"] for row in rows] == ["bad", "good"]


def test_fingerprint_follows_the_figures():
    """The stored narrative is reused only while the numbers behind it hold."""

    assert report_cache.fingerprint("watched 40 min") == report_cache.fingerprint(
        "watched 40 min"
    )
    assert report_cache.fingerprint("watched 40 min") != report_cache.fingerprint(
        "watched 41 min"
    )
