"""Boundary and psychometric tests for deterministic assessment analytics."""

from app.services import exam_stats


def test_score_percentage_keeps_zero_distinct_from_missing():
    assert exam_stats._percent(0, 6) == 0
    assert exam_stats._percent(1, 6) == 16.7
    assert exam_stats._percent(0, 0) is None


def test_distribution_counts_unanswered_score_and_full_marks_once():
    buckets = exam_stats._distribution([0, 19.9, 20, 40, 59.9, 60, 80, 100])
    assert [b["students"] for b in buckets] == [2, 1, 2, 1, 2]


def test_percentiles_match_continuous_interpolation():
    assert exam_stats._percentile([1, 2, 3, 4], .25) == 1.8
    assert exam_stats._stats([1000, 2000, 3000], 1000) == {
        "mean": 2.0, "median": 2.0, "p25": 1.5, "p75": 2.5, "sample_size": 3,
    }


def test_configured_difficulty_boundaries():
    assert exam_stats._difficulty(80) == "easy"
    assert exam_stats._difficulty(79.9) == "moderate"
    assert exam_stats._difficulty(50) == "moderate"
    assert exam_stats._difficulty(49.9) == "difficult"
    assert exam_stats._difficulty(None) is None


def test_one_student_cohort_is_low_confidence_and_no_discrimination():
    value, label, sample = exam_stats._discrimination(
        {1: True}, {(1, 10): True, (1, 11): False}, 10, 2
    )
    assert (value, label, sample) == (None, "insufficient_data", 1)
    assert exam_stats._confidence(1, 20, 2) == "LOW"


def test_point_biserial_excludes_the_item_and_finds_good_discrimination():
    item = {sid: sid >= 6 for sid in range(1, 11)}
    all_results = {}
    for sid in range(1, 11):
        all_results[(sid, 10)] = item[sid]
        all_results[(sid, 11)] = sid >= 6
        all_results[(sid, 12)] = sid >= 6
    value, label, sample = exam_stats._discrimination(item, all_results, 10, 3)
    assert value == 1.0
    assert label == "good"
    assert sample == 10


def test_negative_discrimination_is_neutral_review_signal():
    item = {sid: sid <= 5 for sid in range(1, 11)}
    results = {}
    for sid in range(1, 11):
        results[(sid, 10)] = item[sid]
        results[(sid, 11)] = sid >= 6
        results[(sid, 12)] = sid >= 6
    value, label, _ = exam_stats._discrimination(item, results, 10, 3)
    assert value == -1.0
    assert label == "negative"
    _, priority, reasons = exam_stats._priority(50, label, [], 0, 0, 5, 10)
    assert priority == "MEDIUM"
    assert any("review recommended" in reason for reason in reasons)
    assert all("wrong" not in reason for reason in reasons)


def test_zero_variance_returns_insufficient_data_instead_of_dividing_by_zero():
    item = {sid: True for sid in range(1, 11)}
    results = {(sid, qid): True for sid in range(1, 11) for qid in (10, 11)}
    assert exam_stats._discrimination(item, results, 10, 2)[:2] == (
        None, "insufficient_variance"
    )


def test_non_functioning_and_misconception_inputs_affect_priority_deterministically():
    options = [
        {"classification": "non_functioning", "percent": 0, "order": 2},
        {"classification": "strong_misconception", "percent": 50, "order": 3},
    ]
    score, category, reasons = exam_stats._priority(
        25, "negative", options, 67, 10, 20, 15
    )
    assert score == 87.8
    assert category == "HIGH"
    assert "50.0% selected distractor 3" in reasons
    assert "67.0% required retry" in reasons


def test_support_segmentation_is_not_score_only():
    # A high score without question-level evidence is explicitly insufficient.
    assert exam_stats._support(True, 100, None, None, None, [])[0] == "INCOMPLETE_OR_INSUFFICIENT"
    assert exam_stats._support(False, 100, 100, 100, 0, [])[0] == "INCOMPLETE_OR_INSUFFICIENT"
    assert exam_stats._support(True, 90, 90, 90, 0, [])[0] == "STRONG_PERFORMANCE"
    assert exam_stats._support(True, 90, 90, 90, 60, [])[1] == "High retry dependency"
    assert exam_stats._support(True, 70, 70, 70, 0, ["Anatomy"])[1] == "Weak topic: Anatomy"


def test_topic_evidence_thresholds_are_configured_in_one_place():
    config = exam_stats.DEFAULT_CONFIG
    assert config.min_topic_questions == 2
    assert config.min_topic_attempts == 10
    assert config.min_discrimination_students == 10
