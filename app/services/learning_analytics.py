"""Deterministic learning analytics used by the weekly report.

This module deliberately contains no database or LLM code.  It turns persisted
observations into explainable aggregates; callers decide how to fetch and show
them.  Keeping the formulae here makes weights and evidence thresholds auditable
and prevents a report prompt from becoming a second, inconsistent calculator.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median


# Assessment evidence dominates; merely playing a video is never enough to
# produce mastery.  Missing evidence is removed and the remaining weights are
# normalised, but at least one assessed component is required.
MASTERY_WEIGHTS = {
    "assessment": 0.60,
    "checkpoint": 0.35,
    "coverage": 0.05,
}

CONFIDENCE_HIGH_ITEMS = 8
CONFIDENCE_MEDIUM_ITEMS = 3


def rate(part, whole):
    if whole is None or whole <= 0:
        return None
    return round(part / whole * 100, 1)


def percentage_point_delta(current, previous):
    if current is None or previous is None:
        return None
    return round(current - previous, 1)


def checkpoint_metrics(attempts):
    """Aggregate shown checkpoint attempts without hiding skips or retries."""

    shown = len(attempts)
    answered = [row for row in attempts if row.get("answered_at") is not None]
    correct = [row for row in answered if row.get("is_correct") is True]
    first = [row for row in answered if int(row.get("attempt_number") or 1) == 1]
    first_correct = [row for row in first if row.get("is_correct") is True]
    response_times = [
        float(row["response_time_ms"])
        for row in answered
        if row.get("response_time_ms") is not None
    ]
    allowed_ratios = [
        float(row["response_time_ms"]) / (float(row["allowed_time_seconds"]) * 1000)
        for row in answered
        if row.get("response_time_ms") is not None
        and row.get("allowed_time_seconds")
    ]

    return {
        "shown": shown,
        "answered": len(answered),
        "correct": len(correct),
        "accuracy": rate(len(correct), len(answered)),
        "completion": rate(len(answered), shown),
        "timeout_rate": rate(sum(bool(row.get("timed_out")) for row in attempts), shown),
        "skip_rate": rate(sum(bool(row.get("skipped")) for row in attempts), shown),
        "median_response_time_seconds": (
            round(median(response_times) / 1000, 1) if response_times else None
        ),
        "median_allowed_time_ratio": (
            round(median(allowed_ratios) * 100, 1) if allowed_ratios else None
        ),
        "first_attempt_accuracy": rate(len(first_correct), len(first)),
    }


def checkpoint_breakdown(attempts, key, label=None):
    grouped = defaultdict(list)
    for row in attempts:
        grouped[row.get(key) or "غير مصنّف"].append(row)
    name = label or key
    return [
        {name: group, **checkpoint_metrics(rows)}
        for group, rows in sorted(grouped.items(), key=lambda item: str(item[0]))
    ]


def attention_evidence(attempts):
    """Explain in-lecture evidence; never collapse it into a focus score."""

    metrics = checkpoint_metrics(attempts)
    contexts = Counter(
        row.get("playback_context") for row in attempts if row.get("playback_context")
    )
    return {
        **metrics,
        "playback_contexts": dict(contexts),
        "explanation": (
            f"{metrics['answered']}/{metrics['shown']} checkpoints answered, "
            f"{metrics['correct']} correct, median response time "
            f"{metrics['median_response_time_seconds']}s."
            if metrics["shown"]
            else "No checkpoint evidence was available."
        ),
    }


def confidence(graded_items, signal_types, has_topic_metadata=True):
    """Evidence level for a conclusion, based only on quantity and quality."""

    types = sum(bool(value) for value in signal_types)
    if graded_items >= CONFIDENCE_HIGH_ITEMS and types >= 2 and has_topic_metadata:
        return "HIGH"
    if graded_items >= CONFIDENCE_MEDIUM_ITEMS and types >= 1:
        return "MEDIUM"
    return "LOW"


def mastery(assessment_accuracy=None, checkpoint_accuracy=None, coverage=None,
            assessment_items=0, checkpoint_items=0, has_topic_metadata=True):
    """Weighted mastery with its formula exposed beside the result.

    Coverage can support an assessed result, but cannot create one.  This is the
    guard that keeps a long watch session from becoming academic achievement.
    """

    assessed = assessment_accuracy is not None or checkpoint_accuracy is not None
    values = {
        "assessment": assessment_accuracy,
        "checkpoint": checkpoint_accuracy,
        "coverage": coverage if assessed else None,
    }
    present = {key: value for key, value in values.items() if value is not None}
    if not present:
        return {
            "score": None,
            "confidence": "LOW",
            "evidence_items": 0,
            "components": {},
        }

    denominator = sum(MASTERY_WEIGHTS[key] for key in present)
    score = sum(value * MASTERY_WEIGHTS[key] for key, value in present.items()) / denominator
    items = assessment_items + checkpoint_items
    return {
        "score": round(score, 1),
        "confidence": confidence(
            items,
            (assessment_items > 0, checkpoint_items > 0),
            has_topic_metadata,
        ),
        "evidence_items": items,
        "components": {
            key: {"value": value, "configured_weight": MASTERY_WEIGHTS[key]}
            for key, value in present.items()
        },
    }


def consistency(active_dates, session_count, first_day, last_day, daily_seconds=None):
    active = sorted(set(active_dates))
    all_days = []
    cursor = first_day
    while cursor <= last_day:
        all_days.append(cursor)
        cursor += timedelta(days=1)

    longest = current = 0
    for day in all_days:
        if day in active:
            current = 0
        else:
            current += 1
            longest = max(longest, current)

    daily_seconds = daily_seconds or {}
    total = sum(daily_seconds.values())
    final_days = set(all_days[-2:])
    final_load = sum(seconds for day, seconds in daily_seconds.items() if day in final_days)
    return {
        "active_days": len(active),
        "longest_inactivity_gap_days": longest,
        "average_sessions_per_active_day": (
            round(session_count / len(active), 1) if active else None
        ),
        "last_two_days_share": rate(final_load, total),
    }


def trend(current, previous, keys):
    return {
        key: {
            "previous": previous.get(key),
            "current": current.get(key),
            "delta": percentage_point_delta(current.get(key), previous.get(key)),
        }
        for key in keys
    }


def personal_baseline(history, keys):
    result = {}
    for key in keys:
        values = [row.get(key) for row in history if row.get(key) is not None]
        result[key] = round(median(values), 1) if values else None
    return result


def retention(evidence, minimum_delay_days=3):
    """Immediate vs delayed assessed accuracy, or an honest missing result.

    Evidence rows require ``topic``, ``date`` and ``correct``.  A later row is a
    retention observation only when the same topic was assessed at least the
    configured delay after its first observation.
    """

    by_topic = defaultdict(list)
    for row in evidence:
        if row.get("topic") and row.get("date") is not None:
            by_topic[row["topic"]].append(row)

    immediate, delayed = [], []
    for rows in by_topic.values():
        rows.sort(key=lambda row: row["date"])
        first_day = rows[0]["date"]
        immediate.extend(row for row in rows if row["date"] == first_day)
        delayed.extend(
            row for row in rows
            if row["date"] >= first_day + timedelta(days=minimum_delay_days)
        )

    if not delayed:
        return {
            "status": "NOT_ENOUGH_DATA",
            "immediate_mastery": rate(
                sum(bool(row.get("correct")) for row in immediate), len(immediate)
            ),
            "delayed_retention": None,
            "retention_drop_pp": None,
            "delayed_items": 0,
        }

    first = rate(sum(bool(row.get("correct")) for row in immediate), len(immediate))
    later = rate(sum(bool(row.get("correct")) for row in delayed), len(delayed))
    return {
        "status": "AVAILABLE",
        "immediate_mastery": first,
        "delayed_retention": later,
        "retention_drop_pp": percentage_point_delta(later, first),
        "delayed_items": len(delayed),
    }


def expected_progress(assignments, covered_by_lecture, as_of):
    """Schedule-aware progress; returns missing when nothing was assigned."""

    if not assignments:
        return {
            "status": "NOT_ENOUGH_DATA",
            "expected_percentage": None,
            "actual_percentage": None,
            "gap_pp": None,
            "assigned_items": 0,
        }

    expected_weight = actual_weight = total_weight = 0.0
    for row in assignments:
        weight = float(row.get("duration_seconds") or 1)
        start = row["available_from"]
        due = row["due_at"]
        elapsed = (as_of - start).total_seconds()
        span = max((due - start).total_seconds(), 1)
        expected_weight += min(max(elapsed / span, 0), 1) * weight
        key = row.get("resource_key", row.get("lecture_id"))
        actual_weight += min(max(covered_by_lecture.get(key, 0) or 0, 0), 100) / 100 * weight
        total_weight += weight

    expected = rate(expected_weight, total_weight)
    actual = rate(actual_weight, total_weight)
    return {
        "status": "AVAILABLE",
        "expected_percentage": expected,
        "actual_percentage": actual,
        "gap_pp": percentage_point_delta(actual, expected),
        "assigned_items": len(assignments),
    }
