"""Database-backed learning layer for the concise weekly report."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from app.services import engagement, learning_analytics as analytics, prompts, report


CHECKPOINT_SQL = """
    SELECT q.lecture_id, q.video_id, q.id, COALESCE(t.name, 'غير مصنّف'),
           q.checkpoint_timestamp_seconds, a.shown_at, a.answered_at,
           a.response_time_ms, q.allowed_time_seconds, a.selected_answer,
           q.correct_answer, a.is_correct, a.skipped, a.timed_out,
           a.attempt_number, a.session_id
    FROM checkpoint_attempts AS a
    JOIN checkpoint_questions AS q ON q.id = a.checkpoint_question_id
    LEFT JOIN topics AS t ON t.id = q.topic_id
    WHERE a.student_id = %(student_id)s AND a.course_id = %(course_id)s
      AND a.shown_at >= %(since)s AND a.shown_at < %(until)s
    ORDER BY a.shown_at, a.id
"""

ASSIGNMENT_SQL = """
    SELECT a.lecture_id, a.video_id, a.available_from, a.due_at,
           COALESCE(item.duration_seconds, MAX(tc.end_ts), 0) AS duration_seconds
    FROM course_weekly_assignments AS a
    LEFT JOIN course_items AS item ON item.id = a.video_id
    LEFT JOIN transcript_chunks AS tc
      ON tc.lecture_id = a.lecture_id OR tc.video_id = a.video_id
    WHERE a.course_id = %(course_id)s AND a.week_start = %(week_start)s
      AND a.required
    GROUP BY a.id, a.lecture_id, a.video_id, a.available_from, a.due_at,
             item.duration_seconds
    ORDER BY a.due_at, a.id
"""

CHAT_SQL = """
    SELECT cs.lecture_id, cs.video_id, COALESCE(t.name, 'غير مصنّف'), m.content,
           m.grounded, m.created_at
    FROM chat_messages AS m
    JOIN chat_sessions AS cs ON cs.id = m.session_id
    LEFT JOIN lectures AS l ON l.id = cs.lecture_id
    LEFT JOIN course_items AS item ON item.id = cs.video_id
    LEFT JOIN topics AS t ON t.id = m.topic_id
    WHERE cs.student_id = %(student_id)s
      AND COALESCE(l.course_id, item.course_id) = %(course_id)s
      AND m.role = 'user' AND m.status = 'completed'
      AND m.created_at >= %(since)s AND m.created_at < %(until)s
    ORDER BY m.created_at, m.id
"""

RETENTION_SQL = """
    SELECT t.name, r.assessed_at, r.is_correct
    FROM retention_assessment_results AS r
    JOIN topics AS t ON t.id = r.topic_id
    WHERE r.student_id = %(student_id)s AND r.course_id = %(course_id)s
      AND r.assessed_at < %(until)s
    ORDER BY t.name, r.assessed_at, r.id
"""

ASSESSMENT_SQL = """
    SELECT r.exam_question_id, COALESCE(q.topic, 'غير مصنّف'), r.is_correct,
           r.assessed_at, q.learning_objective, q.cognitive_level, q.difficulty
    FROM assessment_question_results AS r
    JOIN exam_questions AS q ON q.id = r.exam_question_id
    WHERE r.student_id = %(student_id)s AND r.course_id = %(course_id)s
      AND r.assessed_at >= %(since)s AND r.assessed_at < %(until)s
    ORDER BY r.assessed_at, r.id
"""


def _rows(conn, sql, params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _checkpoint_rows(conn, student_id, course_id, since, until):
    rows = _rows(conn, CHECKPOINT_SQL, {
        "student_id": student_id, "course_id": course_id,
        "since": since, "until": until,
    })
    return [
        {
            "lecture_id": row[0], "video_id": row[1],
            "checkpoint_question_id": row[2], "topic": row[3],
            "checkpoint_timestamp": float(row[4]),
            "shown_at": row[5], "answered_at": row[6],
            "response_time_ms": row[7], "allowed_time_seconds": float(row[8]),
            "selected_answer": row[9], "correct_answer": row[10],
            "is_correct": row[11], "skipped": row[12], "timed_out": row[13],
            "attempt_number": row[14], "session_id": row[15],
        }
        for row in rows
    ]


def _assessment_rows(conn, student_id, course_id, since, until):
    return [
        {
            "lecture_id": None, "question_id": f"exam:{row[0]}",
            "topic": row[1], "is_correct": row[2], "answered_at": row[3],
            "learning_objective": row[4], "cognitive_level": row[5],
            "difficulty": row[6],
        }
        for row in _rows(conn, ASSESSMENT_SQL, {
            "student_id": student_id, "course_id": course_id,
            "since": since, "until": until,
        })
    ]


def _playback_context(attempt, session_events):
    before = [event for event in session_events if event.created_at <= attempt["shown_at"]]
    recent = [
        event for event in before
        if (attempt["shown_at"] - event.created_at).total_seconds() <= 120
    ]
    hidden = False
    for event in before:
        if event.event_type == "tab_hidden":
            hidden = True
        elif event.event_type == "tab_visible":
            hidden = False
    if hidden:
        return "page_hidden"

    for index, event in enumerate(recent):
        if event.event_type == "seek" and index:
            if event.video_ts < recent[index - 1].video_ts:
                return "after_backward_seek"
    if any(event.event_type == "pause" for event in recent):
        return "after_pause"

    replayed = engagement.repeated_spans(
        engagement.replay(session_events).watched_spans, min_length=5
    )
    if any(span.start <= attempt["checkpoint_timestamp"] <= span.end for span in replayed):
        return "after_rewatch"
    if recent and not any(
        event.event_type in {"pause", "seek", "tab_hidden", "tab_visible"}
        for event in recent
    ):
        return "after_uninterrupted_viewing"
    return "other"


def _period_metrics(lecture_rows, events_by_lecture, attempts, checkpoints, window):
    here = report.zone()
    coverage_numerator = coverage_denominator = 0.0
    days = set()
    sessions = set()
    for lecture in lecture_rows:
        resource_key = (lecture["source_type"], lecture["id"])
        events = [
            event for event in events_by_lecture.get(resource_key, [])
            if window.since <= event.created_at < window.until
        ]
        totals = engagement.replay_sessions(events)
        if lecture["duration"] > 0:
            coverage_numerator += min(
                engagement.covered_seconds(totals.watched_spans), lecture["duration"]
            )
            coverage_denominator += lecture["duration"]
        days.update(event.created_at.astimezone(here).date() for event in events)
        sessions.update((resource_key, event.session_id) for event in events)

    period_attempts = [
        row for row in attempts if window.since <= row["answered_at"] < window.until
    ]
    period_checkpoints = [
        row for row in checkpoints if window.since <= row["shown_at"] < window.until
    ]
    quiz = report._score(period_attempts)
    cp = analytics.checkpoint_metrics(period_checkpoints)
    mastery = analytics.mastery(
        quiz["accuracy"], cp["accuracy"],
        analytics.rate(coverage_numerator, coverage_denominator),
        quiz["questions_attempted"], cp["answered"],
    )
    return {
        "coverage_percentage": analytics.rate(coverage_numerator, coverage_denominator),
        "active_days": len(days),
        "checkpoint_accuracy": cp["accuracy"],
        "quiz_accuracy": quiz["accuracy"],
        "mastery": mastery["score"],
        "sessions": len(sessions),
    }


def _topic_mastery(attempts, checkpoints, lecture_coverage):
    quiz = defaultdict(list)
    cp = defaultdict(list)
    topic_lectures = defaultdict(set)
    for row in attempts:
        quiz[row["topic"]].append(row)
        if row["lecture_id"] is not None:
            topic_lectures[row["topic"]].add(("lecture", row["lecture_id"]))
    for row in checkpoints:
        cp[row["topic"]].append(row)
        key = (
            ("lecture", row["lecture_id"])
            if row["lecture_id"] is not None else ("video", row["video_id"])
        )
        topic_lectures[row["topic"]].add(key)

    output = []
    for topic in sorted(set(quiz) | set(cp)):
        q = report._score(quiz[topic])
        c = analytics.checkpoint_metrics(cp[topic])
        coverages = [
            lecture_coverage.get(resource_key)
            for resource_key in topic_lectures[topic]
            if lecture_coverage.get(resource_key) is not None
        ]
        coverage = round(sum(coverages) / len(coverages), 1) if coverages else None
        result = analytics.mastery(
            q["accuracy"], c["accuracy"], coverage,
            q["questions_attempted"], c["answered"], topic != "غير مصنّف",
        )
        output.append({
            "topic": topic, "coverage": coverage,
            "checkpoint_accuracy": c["accuracy"], "quiz_accuracy": q["accuracy"],
            **result,
        })
    return sorted(output, key=lambda row: (row["score"] is None, row["score"] or 0))


def _lecture_mastery(lectures, attempts, checkpoints):
    by_quiz = defaultdict(list)
    by_cp = defaultdict(list)
    for row in attempts:
        by_quiz[("lecture", row["lecture_id"])].append(row)
    for row in checkpoints:
        key = (
            ("lecture", row["lecture_id"])
            if row["lecture_id"] is not None else ("video", row["video_id"])
        )
        by_cp[key].append(row)
    output = []
    for lecture in lectures:
        key = (lecture["source_type"], lecture["lecture_id"] or lecture["video_id"])
        q = report._score(by_quiz[key])
        c = analytics.checkpoint_metrics(by_cp[key])
        result = analytics.mastery(
            q["accuracy"], c["accuracy"], lecture["coverage_percentage"],
            q["questions_attempted"], c["answered"],
        )
        output.append({
            "lecture_id": lecture["lecture_id"], "video_id": lecture["video_id"],
            "lecture": lecture["title"],
            "coverage": lecture["coverage_percentage"],
            "checkpoint_accuracy": c["accuracy"], "quiz_accuracy": q["accuracy"],
            **result,
        })
    return output


def _assessment_analysis(attempts):
    def breakdown(key):
        grouped = defaultdict(list)
        for row in attempts:
            if row.get(key):
                grouped[row[key]].append(row)
        return [
            {key: name, **report._score(rows)}
            for name, rows in sorted(grouped.items())
        ]

    errors = Counter(
        row["topic"] for row in attempts if not row["is_correct"]
    )
    return {
        "by_topic": breakdown("topic"),
        "by_learning_objective": breakdown("learning_objective"),
        "by_difficulty": breakdown("difficulty"),
        "by_cognitive_level": breakdown("cognitive_level"),
        "repeated_error_concepts": [
            {"topic": topic, "incorrect_attempts": count}
            for topic, count in errors.most_common(5) if count > 1
        ],
    }


def _chat(rows, weak_topics):
    normalized = Counter()
    topics = Counter()
    grounded = 0
    for _, _, topic, content, is_grounded, _ in rows:
        key = re.sub(r"\s+", " ", content.strip().casefold())
        if key:
            normalized[key] += 1
        if topic != "غير مصنّف":
            topics[topic] += 1
        grounded += is_grounded is True
    repeated = [
        {"question": question, "count": count}
        for question, count in normalized.most_common(5) if count > 1
    ]
    corroborated = [
        {"topic": topic, "questions": count}
        for topic, count in topics.most_common()
        if count > 1 and topic in weak_topics
    ][:3]
    return {
        "questions_asked": len(rows), "grounded_questions": grounded,
        "repeated_questions": repeated,
        "corroborated_confusion_topics": corroborated,
    }


def _actions(progress, topic_mastery, checkpoints, lectures):
    actions = []
    if progress["status"] == "AVAILABLE" and (progress["gap_pp"] or 0) < -5:
        actions.append({
            "action": "أكمل المادة المطلوبة المتأخرة قبل بدء مادة جديدة.",
            "evidence": f"التقدم الفعلي أقل من المتوقع بـ {abs(progress['gap_pp']):.1f} نقطة مئوية.",
            "lecture": None, "video_id": None, "timestamp": None,
        })
    for row in topic_mastery:
        if row["score"] is not None and row["score"] < 65 and len(actions) < 3:
            actions.append({
                "action": f"راجع موضوع {row['topic']} ثم أجب عن سؤالين جديدين عليه.",
                "evidence": f"الإتقان {row['score']}% ({row['confidence']}).",
                "lecture": None, "video_id": None, "timestamp": None,
            })
    missed = next((row for row in checkpoints if row["skipped"] or row["timed_out"]), None)
    if missed and len(actions) < 4:
        actions.append({
            "action": (
                f"ارجع لنقطة التحقق عند {prompts.to_stamp(missed['checkpoint_timestamp'])} "
                "وحاولها بعد مراجعة الشرح."
            ),
            "evidence": "محاولة مسجلة كتخطٍ أو انتهاء وقت.",
            "lecture": missed["lecture_id"],
            "video_id": missed["video_id"],
            "timestamp": missed["checkpoint_timestamp"],
        })
    for lecture in lectures:
        if lecture["skipped_spans"] and len(actions) < 5:
            span = lecture["skipped_spans"][0]
            actions.append({
                "action": f"شاهد الجزء {span['start_label']}–{span['end_label']} من {lecture['title']}.",
                "evidence": "هذا النطاق غير مغطى في بيانات المشاهدة.",
                "lecture": lecture["lecture_id"], "video_id": lecture["video_id"],
                "timestamp": span["start"],
            })
            break
    if not actions:
        actions.append({
            "action": "حافظ على نفس توزيع المذاكرة، وأضف أسئلة تقييم لقياس الفهم.",
            "evidence": "لا توجد فجوة قوية بما يكفي لتوصية أكثر تحديداً.",
            "lecture": None, "video_id": None, "timestamp": None,
        })
    return actions[:5]


def enrich(conn, payload, window, attempts):
    """Attach learning, trend and action sections to an existing report dict."""

    student_id = payload["student"]["id"]
    course_id = payload["course"]["id"]
    lecture_rows = [
        {"id": row["lecture_id"] or row["video_id"],
         "lecture_id": row["lecture_id"], "video_id": row["video_id"],
         "source_type": row["source_type"], "duration": row["duration_seconds"]}
        for row in payload["lectures"]
    ]
    lecture_ids = [row["lecture_id"] for row in lecture_rows if row["lecture_id"] is not None]
    video_ids = [row["video_id"] for row in lecture_rows if row["video_id"] is not None]
    history_since = window.since - timedelta(days=35)
    all_events = engagement.fetch_events_for_resources(
        conn, student_id, lecture_ids, video_ids, history_since, window.until
    )
    all_attempts = report.fetch_attempts(
        conn, student_id, lecture_ids, history_since, window.until
    )
    all_attempts += _assessment_rows(
        conn, student_id, course_id, history_since, window.until
    )
    attempts = [
        row for row in all_attempts
        if window.since <= row["answered_at"] < window.until
    ]
    combined_score = report._score(attempts)
    payload["totals"].update(combined_score)
    all_checkpoints = _checkpoint_rows(
        conn, student_id, course_id, history_since, window.until
    )

    # Attach playback context from actual events in the same browser session.
    sessions = defaultdict(list)
    for resource_key, events in all_events.items():
        for event in events:
            sessions[(resource_key, event.session_id)].append(event)
    for row in all_checkpoints:
        row["playback_context"] = _playback_context(
            row, sessions[(("lecture", row["lecture_id"]) if row["lecture_id"] is not None else ("video", row["video_id"]), row["session_id"])]
        )

    current_cp = [row for row in all_checkpoints if window.since <= row["shown_at"] < window.until]
    cp_metrics = analytics.checkpoint_metrics(current_cp)
    by_topic = analytics.checkpoint_breakdown(current_cp, "topic", "topic")
    lecture_names = {
        (row["source_type"], row["lecture_id"] or row["video_id"]): row["title"]
        for row in payload["lectures"]
    }
    by_lecture_rows = []
    checkpoint_groups = defaultdict(list)
    for item in current_cp:
        key = ("lecture", item["lecture_id"]) if item["lecture_id"] is not None else ("video", item["video_id"])
        checkpoint_groups[key].append(item)
    for key, items in checkpoint_groups.items():
        row = {"lecture_id": key[1], **analytics.checkpoint_metrics(items)}
        row["lecture"] = lecture_names.get(key, "غير معروف")
        by_lecture_rows.append(row)
    payload["checkpoints"] = {
        **analytics.attention_evidence(current_cp),
        "by_topic": by_topic, "by_lecture": by_lecture_rows,
        "by_playback_context": analytics.checkpoint_breakdown(
            current_cp, "playback_context", "context"
        ),
    }

    coverage = {
        (row["source_type"], row["lecture_id"] or row["video_id"]): row["coverage_percentage"]
        for row in payload["lectures"]
    }
    topics = _topic_mastery(attempts, current_cp, coverage)
    overall = analytics.mastery(
        payload["totals"]["accuracy"], cp_metrics["accuracy"],
        payload["totals"]["coverage_percentage"],
        payload["totals"]["questions_attempted"], cp_metrics["answered"],
    )
    payload["mastery"] = {
        "overall": overall, "topics": topics,
        "lectures": _lecture_mastery(payload["lectures"], attempts, current_cp),
    }
    payload["assessment_analysis"] = _assessment_analysis(attempts)

    previous_window = report.Window(
        window.since - timedelta(days=7), window.since,
        window.first_day - timedelta(days=7), window.first_day - timedelta(days=1),
    )
    current_metrics = _period_metrics(
        lecture_rows, all_events, all_attempts, all_checkpoints, window
    )
    previous_metrics = _period_metrics(
        lecture_rows, all_events, all_attempts, all_checkpoints, previous_window
    )
    payload["trend"] = analytics.trend(
        current_metrics, previous_metrics,
        ("coverage_percentage", "active_days", "checkpoint_accuracy",
         "quiz_accuracy", "mastery"),
    )
    # Active days is a count, so its delta is a count even though the other
    # values are percentage-point deltas.
    active = payload["trend"]["active_days"]
    active["delta"] = (
        active["current"] - active["previous"]
        if active["current"] is not None and active["previous"] is not None else None
    )

    history = []
    for weeks_back in range(2, 6):
        end = window.since - timedelta(days=7 * (weeks_back - 1))
        historical = report.Window(
            end - timedelta(days=7), end,
            window.first_day - timedelta(days=7 * weeks_back),
            window.last_day - timedelta(days=7 * weeks_back),
        )
        history.append(_period_metrics(
            lecture_rows, all_events, all_attempts, all_checkpoints, historical
        ))
    payload["personal_baseline"] = analytics.personal_baseline(
        history,
        ("coverage_percentage", "active_days", "checkpoint_accuracy",
         "quiz_accuracy", "mastery"),
    )

    assignments = [
        {"lecture_id": row[0], "video_id": row[1],
         "resource_key": (("lecture", row[0]) if row[0] is not None else ("video", row[1])),
         "available_from": row[2], "due_at": row[3],
         "duration_seconds": float(row[4])}
        for row in _rows(conn, ASSIGNMENT_SQL, {
            "course_id": course_id, "week_start": window.first_day,
        })
    ]
    as_of = min(datetime.now(timezone.utc), window.until)
    payload["progress"] = analytics.expected_progress(assignments, coverage, as_of)
    payload["progress"]["completion_velocity_per_active_day"] = (
        round(payload["totals"]["lectures_completed"] / payload["totals"]["active_days"], 2)
        if payload["totals"]["active_days"] else None
    )
    payload["progress"]["estimated_finish_date"] = None

    active_dates = {
        row["date"] for row in payload["totals"]["daily"] if row["active"]
    }
    daily = {row["date"]: row["watch_time_seconds"] for row in payload["totals"]["daily"]}
    payload["consistency"] = analytics.consistency(
        active_dates, current_metrics["sessions"], window.first_day, window.last_day, daily
    )
    payload["efficiency"] = {
        "active_session_ratio": analytics.rate(
            payload["totals"]["watch_time_seconds"],
            payload["totals"]["session_duration_seconds"],
        ),
        "page_hidden_seconds": payload["totals"]["time_away_seconds"],
        "caution": "Page-hidden time does not prove distraction.",
    }

    retention_rows = _rows(conn, RETENTION_SQL, {
        "student_id": student_id, "course_id": course_id, "until": window.until,
    })
    payload["retention"] = analytics.retention([
        {"topic": row[0], "date": row[1].date(), "correct": row[2]}
        for row in retention_rows
    ])

    weak_topics = {
        row["topic"] for row in topics
        if row["score"] is not None and row["score"] < 65
    }
    chat_rows = _rows(conn, CHAT_SQL, {
        "student_id": student_id, "course_id": course_id,
        "since": window.since, "until": window.until,
    })
    payload["ai_chat"] = _chat(chat_rows, weak_topics)
    payload["action_plan"] = _actions(
        payload["progress"], topics, current_cp, payload["lectures"]
    )
    payload["evidence_note"] = (
        "النتائج تفصل بين المشاهدة والفهم والإتقان؛ السلوك وحده لا يثبت الفهم."
    )
    payload["insights"] = []
    if cp_metrics["shown"]:
        payload["insights"].append({
            "observation": payload["checkpoints"]["explanation"],
            "interpretation": "دليل مباشر على المشاركة والفهم أثناء الشرح.",
            "confidence": overall["confidence"],
        })
    if topics:
        weakest = next((row for row in topics if row["score"] is not None), None)
        if weakest:
            payload["insights"].append({
                "observation": f"إتقان {weakest['topic']} = {weakest['score']}% من {weakest['evidence_items']} عناصر.",
                "interpretation": "أولوية مراجعة، وليس حكماً نهائياً على قدرة الطالب.",
                "confidence": weakest["confidence"],
            })
    if payload["totals"]["time_away_seconds"]:
        payload["insights"].append({
            "observation": f"الصفحة كانت مخفية {payload['totals']['time_away_seconds']} ثانية.",
            "interpretation": "وقت غير محسوب كمشاهدة؛ لا يثبت التشتت.",
            "confidence": "HIGH",
        })
    payload["insights"] = payload["insights"][:5]
    return payload
