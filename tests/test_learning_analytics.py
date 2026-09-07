"""Learning analytics formulae: assessed evidence, missing data and consistency."""

from datetime import date, datetime, timedelta, timezone

from app.services import learning_analytics as analytics
from app.schemas.reports import ActionItem


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def checkpoint(number=1, *, answered=True, correct=True, skipped=False,
               timed_out=False, response_ms=10_000, topic="Anatomy"):
    return {
        "checkpoint_question_id": number,
        "topic": topic,
        "shown_at": NOW,
        "answered_at": NOW + timedelta(seconds=10) if answered else None,
        "response_time_ms": response_ms if answered else None,
        "allowed_time_seconds": 20,
        "is_correct": correct if answered else None,
        "skipped": skipped,
        "timed_out": timed_out,
        "attempt_number": 1,
    }


def test_checkpoint_rates_keep_skips_timeouts_and_answers_separate():
    rows = [
        checkpoint(1, correct=True),
        checkpoint(2, correct=False),
        checkpoint(3, answered=False, correct=False, skipped=True),
        checkpoint(4, answered=False, correct=False, timed_out=True),
    ]
    result = analytics.checkpoint_metrics(rows)
    assert result == {
        "shown": 4,
        "answered": 2,
        "correct": 1,
        "accuracy": 50.0,
        "completion": 50.0,
        "timeout_rate": 25.0,
        "skip_rate": 25.0,
        "median_response_time_seconds": 10.0,
        "median_allowed_time_ratio": 50.0,
        "first_attempt_accuracy": 50.0,
    }


def test_repeated_checkpoint_attempts_do_not_change_first_attempt_accuracy():
    first = checkpoint(1, correct=False)
    retry = checkpoint(1, correct=True)
    retry["attempt_number"] = 2
    result = analytics.checkpoint_metrics([first, retry])
    assert result["accuracy"] == 50.0
    assert result["first_attempt_accuracy"] == 0.0


def test_watch_coverage_alone_never_creates_mastery():
    assert analytics.mastery(coverage=100)["score"] is None


def test_mastery_weights_are_central_and_assessment_dominates():
    result = analytics.mastery(
        assessment_accuracy=50,
        checkpoint_accuracy=100,
        coverage=100,
        assessment_items=6,
        checkpoint_items=4,
    )
    assert result["score"] == 70.0
    assert result["confidence"] == "HIGH"
    assert result["components"]["assessment"]["configured_weight"] == 0.60


def test_thin_evidence_is_low_confidence_even_with_a_high_score():
    result = analytics.mastery(checkpoint_accuracy=100, checkpoint_items=1)
    assert result["score"] == 100.0
    assert result["confidence"] == "LOW"


def test_retention_is_missing_until_a_real_delayed_reassessment_exists():
    result = analytics.retention([
        {"topic": "Spine", "date": date(2026, 9, 1), "correct": True},
        {"topic": "Spine", "date": date(2026, 9, 2), "correct": False},
    ])
    assert result["status"] == "NOT_ENOUGH_DATA"
    assert result["delayed_retention"] is None


def test_retention_uses_same_topic_after_three_days():
    result = analytics.retention([
        {"topic": "Spine", "date": date(2026, 9, 1), "correct": True},
        {"topic": "Spine", "date": date(2026, 9, 4), "correct": False},
    ])
    assert result["status"] == "AVAILABLE"
    assert result["immediate_mastery"] == 100.0
    assert result["delayed_retention"] == 0.0
    assert result["retention_drop_pp"] == -100.0


def test_consistency_reports_inactivity_and_last_minute_clustering_without_judgment():
    first = date(2026, 9, 1)
    result = analytics.consistency(
        {date(2026, 9, 1), date(2026, 9, 6), date(2026, 9, 7)},
        session_count=6,
        first_day=first,
        last_day=date(2026, 9, 7),
        daily_seconds={first: 100, date(2026, 9, 6): 300, date(2026, 9, 7): 600},
    )
    assert result["longest_inactivity_gap_days"] == 4
    assert result["average_sessions_per_active_day"] == 2.0
    assert result["last_two_days_share"] == 90.0


def test_expected_progress_refuses_to_invent_an_assignment_schedule():
    result = analytics.expected_progress([], {}, NOW)
    assert result["status"] == "NOT_ENOUGH_DATA"
    assert result["expected_percentage"] is None


def test_expected_progress_weights_real_assignments_by_duration():
    assignments = [
        {"lecture_id": 1, "available_from": NOW - timedelta(days=2),
         "due_at": NOW + timedelta(days=2), "duration_seconds": 100},
        {"lecture_id": 2, "available_from": NOW - timedelta(days=2),
         "due_at": NOW + timedelta(days=2), "duration_seconds": 300},
    ]
    result = analytics.expected_progress(assignments, {1: 100, 2: 0}, NOW)
    assert result["expected_percentage"] == 50.0
    assert result["actual_percentage"] == 25.0
    assert result["gap_pp"] == -25.0


def test_report_renderer_has_exactly_three_bounded_print_pages():
    js = open("app/static/report.js", encoding="utf-8").read()
    css = open("app/static/report.css", encoding="utf-8").read()
    assert js.count('<div class="report-page">') == 3
    assert ".report-page { page-break-after: always" in css
    assert "height: 270mm; overflow: hidden" in css


def test_action_items_can_identify_modern_videos():
    item = ActionItem(action="review", evidence="missed", video_id=17, timestamp=90)
    assert item.video_id == 17
    assert item.lecture is None
