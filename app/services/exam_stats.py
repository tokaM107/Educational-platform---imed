"""Deterministic teacher assessment analytics over persisted exam evidence.

Score is final correct responses / all exam questions (unanswered is wrong).
Attempted accuracy is final correct responses / answered questions. Item
discrimination is point-biserial correlation against the other-item score.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, median

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalyticsConfig:
    pass_mark: float = 60.0
    easy_at: float = 80.0
    moderate_at: float = 50.0
    min_discrimination_students: int = 10
    good_discrimination_at: float = 0.20
    non_functioning_distractor_below: float = 5.0
    misconception_distractor_at: float = 25.0
    suspicious_high_performer_gap: float = 20.0
    min_group_students: int = 5
    min_topic_questions: int = 2
    min_topic_attempts: int = 10
    mastery_threshold: float = 60.0
    high_confidence_students: int = 30
    medium_confidence_students: int = 10
    high_confidence_attempts: int = 60
    medium_confidence_attempts: int = 20
    priority_high_at: float = 50.0
    priority_medium_at: float = 25.0
    strong_score_at: float = 85.0
    high_retry_dependency_at: float = 50.0


DEFAULT_CONFIG = AnalyticsConfig()
DEFAULT_PASS_MARK = DEFAULT_CONFIG.pass_mark

EXAM_SQL = """
SELECT e.id,e.title,e.course_id,c.title,d.name,e.pass_score
FROM exams e JOIN courses c ON c.id=e.course_id JOIN users d ON d.id=c.doctor_id
WHERE e.id=%s
"""
COHORT_SQL = """
SELECT u.id,u.name,u.email FROM enrollments en JOIN users u ON u.id=en.student_id
WHERE en.course_id=%s AND en.status='active'
AND (en.expires_at IS NULL OR en.expires_at>now()) ORDER BY u.name,u.id
"""
QUESTIONS_SQL = """
SELECT q.id,q.text,q.type,q.points,q.order_index,
       COALESCE(t.name,q.topic,'Uncategorised'),q.learning_objective,q.difficulty,
       o.id,o.text,o.is_correct,o.order_index
FROM exam_questions q LEFT JOIN topics t ON t.id=q.topic_id
LEFT JOIN exam_options o ON o.question_id=q.id WHERE q.exam_id=%s
ORDER BY q.order_index,q.id,o.order_index,o.id
"""
SITTINGS_SQL = """
SELECT id,user_id,status,attempt_number,started_at,submitted_at,score,max_score,
       awarded_points,submission_reason FROM exam_attempts
WHERE exam_id=%(exam_id)s AND user_id=ANY(%(cohort)s)
ORDER BY user_id,attempt_number,started_at,id
"""
RESPONSES_SQL = """
SELECT r.id,r.exam_attempt_id,r.exam_question_id,r.student_id,r.attempt_number,
       r.selected_option_ids,r.is_correct,r.shown_at,r.answered_at,
       r.response_time_ms,r.skipped,r.timed_out,a.attempt_number
FROM assessment_question_attempts r JOIN exam_attempts a ON a.id=r.exam_attempt_id
JOIN exam_questions q ON q.id=r.exam_question_id
WHERE q.exam_id=%(exam_id)s AND r.student_id=ANY(%(cohort)s)
ORDER BY r.student_id,q.order_index,a.attempt_number,r.attempt_number,r.shown_at,r.id
"""
OUTSIDERS_SQL = """
SELECT count(*) FROM assessment_question_attempts r
JOIN exam_questions q ON q.id=r.exam_question_id
WHERE q.exam_id=%(exam_id)s AND NOT (r.student_id=ANY(%(cohort)s))
"""
LIST_SQL = """
SELECT e.id,e.title,e.course_id,c.title,count(DISTINCT q.id),
       count(DISTINCT a.user_id),count(a.id),max(a.submitted_at)
FROM exams e JOIN courses c ON c.id=e.course_id
LEFT JOIN exam_questions q ON q.exam_id=e.id LEFT JOIN exam_attempts a ON a.exam_id=e.id
WHERE c.doctor_id=%(doctor_id)s
AND (%(course_id)s::int IS NULL OR e.course_id=%(course_id)s)
GROUP BY e.id,e.title,e.course_id,c.title
ORDER BY max(a.submitted_at) DESC NULLS LAST,e.id
"""


def _percent(part, whole):
    return None if not whole else round(part / whole * 100, 1)


def _percentile(values, p):
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    pos = (len(ordered) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    value = ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
    return round(value, 1)


def _stats(values, scale=1.0):
    values = [float(v) / scale for v in values if v is not None]
    return {"mean": round(mean(values), 1) if values else None,
            "median": round(median(values), 1) if values else None,
            "p25": _percentile(values, .25), "p75": _percentile(values, .75),
            "sample_size": len(values)}


def _distribution(scores):
    result = []
    for low, high in ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100)):
        count = sum(low <= s <= high if high == 100 else low <= s < high for s in scores)
        result.append({"low": low, "high": high, "students": count})
    return result


def _difficulty(value, config=DEFAULT_CONFIG):
    if value is None:
        return None
    return "easy" if value >= config.easy_at else "moderate" if value >= config.moderate_at else "difficult"


def _confidence(students, attempts, questions=1, config=DEFAULT_CONFIG):
    if students >= config.high_confidence_students and attempts >= config.high_confidence_attempts:
        return "HIGH"
    if students >= config.medium_confidence_students and attempts >= config.medium_confidence_attempts:
        return "MEDIUM"
    return "LOW"


def _correlation(xs, ys):
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = mean(xs), mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx, dy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    return None if not dx or not dy else round(numerator / math.sqrt(dx * dy), 3)


def _discrimination(item_results, all_results, question_id, total_questions,
                    config=DEFAULT_CONFIG):
    students = sorted(item_results)
    if len(students) < config.min_discrimination_students or total_questions < 2:
        return None, "insufficient_data", len(students)
    xs, ys = [], []
    for sid in students:
        other = [correct for (student, qid), correct in all_results.items()
                 if student == sid and qid != question_id]
        xs.append(float(item_results[sid]))
        ys.append(sum(other) / (total_questions - 1))
    value = _correlation(xs, ys)
    if value is None:
        return None, "insufficient_variance", len(students)
    return value, ("negative" if value < 0 else
                   "weak" if value < config.good_discrimination_at else "good"), len(students)


def _priority(accuracy, discrimination, options, retry_rate, skip_rate,
              response_median, exam_p75, config=DEFAULT_CONFIG):
    score, reasons = 0.0, []
    if accuracy is not None:
        score += (100 - accuracy) * .35
        if accuracy < config.moderate_at:
            reasons.append(f"{accuracy:.1f}% correct")
    if discrimination == "negative":
        score += 25
        reasons.append("negative discrimination; review recommended")
    elif discrimination == "weak":
        score += 12
        reasons.append("weak discrimination")
    misconception = next((o for o in options if o["classification"] == "strong_misconception"), None)
    suspicious = next((o for o in options if o["classification"] == "suspicious"), None)
    if misconception:
        score += 15
        reasons.append(f"{misconception['percent']:.1f}% selected distractor {misconception['order']}")
    if suspicious:
        score += 10
        reasons.append(f"distractor {suspicious['order']} over-selected by higher performers")
    if retry_rate is not None:
        score += retry_rate * .15
        if retry_rate >= 25:
            reasons.append(f"{retry_rate:.1f}% required retry")
    if skip_rate is not None:
        score += skip_rate * .15
        if skip_rate >= 10:
            reasons.append(f"{skip_rate:.1f}% skipped/timed out")
    if response_median is not None and exam_p75 is not None and response_median > exam_p75:
        score += 10
        reasons.append("response time above exam p75")
    score = round(min(score, 100), 1)
    category = "HIGH" if score >= config.priority_high_at else "MEDIUM" if score >= config.priority_medium_at else "LOW"
    return score, category, reasons


def _support(completed, score, accuracy, first_accuracy, retry_rate, weak_topics,
             config=DEFAULT_CONFIG):
    if not completed:
        return "INCOMPLETE_OR_INSUFFICIENT", "Low completion"
    if accuracy is None or first_accuracy is None or score is None:
        return "INCOMPLETE_OR_INSUFFICIENT", "Insufficient question-level evidence"
    if weak_topics:
        return "NEEDS_SUPPORT", f"Weak topic: {weak_topics[0]}"
    if retry_rate is not None and retry_rate >= config.high_retry_dependency_at:
        return "NEEDS_SUPPORT", "High retry dependency"
    if accuracy < config.mastery_threshold:
        return "NEEDS_SUPPORT", "Low attempted accuracy"
    if first_accuracy < config.moderate_at:
        return "NEEDS_SUPPORT", "Low first-attempt knowledge"
    if score >= config.strong_score_at and accuracy >= 80 and (retry_rate or 0) < 25:
        return "STRONG_PERFORMANCE", "Secure first-attempt performance"
    return "ON_TRACK", "No immediate concern"


def _shape_questions(rows):
    questions, by_id = [], {}
    for row in rows:
        if row[0] not in by_id:
            item = {"question_id": row[0], "stem": row[1], "type": row[2],
                    "points": row[3], "order": row[4], "topic": row[5],
                    "learning_objective": row[6], "declared_difficulty": row[7],
                    "options": []}
            by_id[row[0]] = item
            questions.append(item)
        if row[8] is not None:
            by_id[row[0]]["options"].append({"option_id": row[8], "text": row[9],
                                             "is_correct": row[10], "order": row[11]})
    return questions


def fetch(conn, exam_id, pass_mark=None, config=DEFAULT_CONFIG):
    """Build one report using a bounded set of bulk queries (no N+1 reads)."""
    with conn.cursor() as cur:
        cur.execute(EXAM_SQL, (exam_id,))
        exam = cur.fetchone()
        if exam is None:
            return None
        cur.execute(COHORT_SQL, (exam[2],))
        cohort = cur.fetchall()
        cohort_ids = [r[0] for r in cohort]
        cur.execute(QUESTIONS_SQL, (exam_id,))
        questions = _shape_questions(cur.fetchall())
        if cohort_ids:
            params = {"exam_id": exam_id, "cohort": cohort_ids}
            cur.execute(SITTINGS_SQL, params)
            sitting_rows = cur.fetchall()
            cur.execute(RESPONSES_SQL, params)
            response_rows = cur.fetchall()
            cur.execute(OUTSIDERS_SQL, params)
            outsiders = cur.fetchone()[0]
        else:
            sitting_rows, response_rows, outsiders = [], [], 0

    pass_mark = float(exam[5] if pass_mark is None else pass_mark)
    qids = {q["question_id"] for q in questions}
    sittings, responses = defaultdict(list), defaultdict(list)
    for r in sitting_rows:
        sittings[r[1]].append({"id": r[0], "status": r[2], "attempt_number": r[3],
                               "started_at": r[4], "submitted_at": r[5], "score": r[6],
                               "max_score": r[7], "awarded_points": r[8],
                               "submission_reason": r[9]})
    for r in response_rows:
        responses[(r[3], r[2])].append({"id": r[0], "exam_attempt_id": r[1],
            "question_id": r[2], "student_id": r[3], "attempt_number": r[4],
            "selected_option_ids": list(r[5]) if r[5] is not None else None,
            "is_correct": r[6], "shown_at": r[7], "answered_at": r[8],
            "response_time_ms": r[9], "skipped": r[10], "timed_out": r[11],
            "exam_attempt_number": r[12]})
    first = {key: value[0] for key, value in responses.items()}
    final = {key: value[-1] for key, value in responses.items()}
    final_correct = {key: bool(value["is_correct"]) for key, value in final.items()
                     if value["answered_at"] is not None}
    all_times = [e["response_time_ms"] for values in responses.values() for e in values
                 if e["response_time_ms"] is not None]
    response_time = _stats(all_times, 1000)

    students, student_scores = [], {}
    student_topics = defaultdict(lambda: defaultdict(list))
    topic_by_qid = {q["question_id"]: q["topic"] for q in questions}
    for sid, name, email in cohort:
        student_sittings = sittings.get(sid, [])
        terminal = [s for s in student_sittings if s["status"] in ("submitted", "expired")]
        latest = terminal[-1] if terminal else (student_sittings[-1] if student_sittings else None)
        detailed = [(qid, final[(sid, qid)]) for qid in qids if (sid, qid) in final]
        answered = [(qid, e) for qid, e in detailed if e["answered_at"] is not None]
        correct = sum(bool(e["is_correct"]) for _, e in answered)
        initial = [first[(sid, qid)] for qid, _ in detailed
                   if first[(sid, qid)]["answered_at"] is not None]
        retries = sum(max(len(responses[(sid, qid)]) - 1, 0) for qid, _ in detailed)
        retried = sum(len(responses[(sid, qid)]) > 1 for qid, _ in detailed)
        if detailed and qids:
            score = _percent(correct, len(qids))
        elif latest and latest["awarded_points"] is not None and latest["max_score"]:
            score = _percent(latest["awarded_points"], latest["max_score"])
        elif latest and latest["score"] is not None:
            score = float(latest["score"])
        else:
            score = None
        accuracy = _percent(correct, len(answered))
        first_accuracy = _percent(sum(bool(e["is_correct"]) for e in initial), len(initial))
        retry_rate = _percent(retried, len(detailed))
        for qid, event in answered:
            student_topics[sid][topic_by_qid[qid]].append(bool(event["is_correct"]))
        durations = [(s["submitted_at"] - s["started_at"]).total_seconds() for s in terminal
                     if s["submitted_at"] and s["started_at"]]
        student_scores[sid] = score
        students.append({"student_id": sid, "name": name, "email": email,
            "started": bool(student_sittings),
            "completed": any(s["status"] == "submitted" for s in student_sittings),
            "questions_answered": len(answered), "questions_correct": correct,
            "score_percent": score, "attempted_accuracy": accuracy,
            "first_attempt_accuracy": first_accuracy, "retries": retries,
            "retry_rate": retry_rate, "exam_attempts": len(student_sittings),
            "duration_seconds": round(durations[-1], 1) if durations else None})

    score_values = [v for v in student_scores.values() if v is not None]
    score_median = median(score_values) if score_values else None
    high = {sid for sid, score in student_scores.items() if score_median is not None and score is not None and score > score_median}
    low = {sid for sid, score in student_scores.items() if score_median is not None and score is not None and score < score_median}
    question_stats, difficulty_counts = [], {"easy": 0, "moderate": 0, "difficult": 0}
    topic_qids, topic_students, topic_attempts = defaultdict(set), defaultdict(set), defaultdict(int)
    for question in questions:
        qid = question["question_id"]
        shown = {sid: e for (sid, item), e in final.items() if item == qid}
        answered = {sid: e for sid, e in shown.items() if e["answered_at"] is not None}
        initial = {sid: e for (sid, item), e in first.items() if item == qid and e["answered_at"] is not None}
        accuracy = _percent(sum(bool(e["is_correct"]) for e in answered.values()), len(answered))
        first_accuracy = _percent(sum(bool(e["is_correct"]) for e in initial.values()), len(initial))
        attempts = sum(len(responses[(sid, qid)]) for sid in shown)
        retry_rate = _percent(sum(len(responses[(sid, qid)]) > 1 for sid in shown), len(shown))
        skip_rate = _percent(sum(e["skipped"] or e["timed_out"] for e in shown.values()), len(shown))
        q_times = [e["response_time_ms"] for e in answered.values() if e["response_time_ms"] is not None]
        correct_times = [e["response_time_ms"] for e in answered.values() if e["is_correct"] and e["response_time_ms"] is not None]
        wrong_times = [e["response_time_ms"] for e in answered.values() if not e["is_correct"] and e["response_time_ms"] is not None]
        initial_times = [e["response_time_ms"] for e in initial.values() if e["response_time_ms"] is not None]
        timing = _stats(q_times, 1000)
        timing.update({"correct_median": _stats(correct_times, 1000)["median"],
                       "incorrect_median": _stats(wrong_times, 1000)["median"],
                       "first_attempt_median": _stats(initial_times, 1000)["median"]})
        discr, discr_label, discr_n = _discrimination(
            {sid: bool(e["is_correct"]) for sid, e in answered.items()}, final_correct,
            qid, len(qids), config)
        option_rows = []
        for option in question["options"]:
            selected = {sid for sid, e in answered.items()
                        if option["option_id"] in (e["selected_option_ids"] or [])}
            pct = _percent(len(selected), len(answered))
            high_pct, low_pct = _percent(len(selected & high), len(high)), _percent(len(selected & low), len(low))
            classification = "correct" if option["is_correct"] else "functioning"
            if not option["is_correct"]:
                if (pct or 0) < config.non_functioning_distractor_below:
                    classification = "non_functioning"
                if (pct or 0) >= config.misconception_distractor_at:
                    classification = "strong_misconception"
                if (len(high) >= config.min_group_students and len(low) >= config.min_group_students
                        and high_pct is not None and low_pct is not None
                        and high_pct - low_pct >= config.suspicious_high_performer_gap):
                    classification = "suspicious"
            option_rows.append({**option, "picks": len(selected), "percent": pct,
                "high_performer_percent": high_pct, "low_performer_percent": low_pct,
                "classification": classification})
        priority_score, priority, reasons = _priority(accuracy, discr_label, option_rows,
            retry_rate, skip_rate, timing["median"], response_time["p75"], config)
        empirical = _difficulty(accuracy, config)
        if empirical:
            difficulty_counts[empirical] += 1
        topic_qids[question["topic"]].add(qid)
        topic_students[question["topic"]].update(shown)
        topic_attempts[question["topic"]] += attempts
        question_stats.append({**question, "students_answered": len(answered),
            "students_shown": len(shown), "students_correct": sum(bool(e["is_correct"]) for e in answered.values()),
            "attempts": attempts, "correct_percent": accuracy,
            "first_attempt_percent": first_accuracy, "final_accuracy": accuracy,
            "retry_gain": round(accuracy-first_accuracy, 1) if accuracy is not None and first_accuracy is not None else None,
            "retry_rate": retry_rate, "attempts_per_student": round(attempts/len(shown), 2) if shown else None,
            "skip_timeout_rate": skip_rate, "empirical_difficulty": empirical,
            "confidence": _confidence(len(answered), attempts, 1, config),
            "discrimination": discr, "discrimination_label": discr_label,
            "discrimination_sample_size": discr_n, "response_time": timing,
            "options": option_rows, "review_priority_score": priority_score,
            "review_priority": priority, "review_reasons": reasons})

    topics = []
    for topic, topic_items in topic_qids.items():
        keys = [key for key in final if key[1] in topic_items]
        answered = [final[key] for key in keys if final[key]["answered_at"] is not None]
        initial = [first[key] for key in keys if first[key]["answered_at"] is not None]
        final_acc = _percent(sum(bool(e["is_correct"]) for e in answered), len(answered))
        first_acc = _percent(sum(bool(e["is_correct"]) for e in initial), len(initial))
        participants = {sid for sid, _ in keys}
        retried = {sid for sid, qid in keys if len(responses[(sid, qid)]) > 1}
        below = [values for sid in participants if (values := student_topics[sid].get(topic, []))
                 and _percent(sum(values), len(values)) < config.mastery_threshold]
        evidenced = sum(bool(student_topics[sid].get(topic)) for sid in participants)
        conclusive = len(topic_items) >= config.min_topic_questions and topic_attempts[topic] >= config.min_topic_attempts
        topics.append({"topic": topic, "questions": len(topic_items),
            "participating_students": len(participants), "attempts": topic_attempts[topic],
            "first_attempt_accuracy": first_acc, "final_accuracy": final_acc,
            "retry_gain": round(final_acc-first_acc, 1) if final_acc is not None and first_acc is not None else None,
            "retry_rate": _percent(len(retried), len(participants)),
            "median_response_time": _stats([e["response_time_ms"] for e in answered if e["response_time_ms"] is not None], 1000)["median"],
            "students_below_mastery_percent": _percent(len(below), evidenced),
            "confidence": _confidence(len(participants), topic_attempts[topic], len(topic_items), config) if conclusive else "LOW",
            "conclusive": conclusive, "evidence_note": None if conclusive else "insufficient evidence"})
    topics.sort(key=lambda t: (t["final_accuracy"] is None, t["final_accuracy"] or 0))

    weak_topics = {t["topic"] for t in topics if t["conclusive"] and t["final_accuracy"] is not None
                   and t["final_accuracy"] < config.mastery_threshold}
    for row in students:
        weak = sorted(topic for topic in weak_topics if (values := student_topics[row["student_id"]].get(topic, []))
                      and _percent(sum(values), len(values)) < config.mastery_threshold)
        category, issue = _support(row["completed"], row["score_percent"], row["attempted_accuracy"],
                                   row["first_attempt_accuracy"], row["retry_rate"], weak, config)
        row.update({"weakest_topics": weak[:2], "support_category": category, "primary_issue": issue})
    students.sort(key=lambda r: (r["support_category"] == "STRONG_PERFORMANCE", -(r["score_percent"] or -1), r["student_id"]))

    started, completed = [s for s in students if s["started"]], [s for s in students if s["completed"]]
    final_answered = [e for e in final.values() if e["answered_at"] is not None]
    first_answered = [e for e in first.values() if e["answered_at"] is not None]
    final_accuracy = _percent(sum(bool(e["is_correct"]) for e in final_answered), len(final_answered))
    first_accuracy = _percent(sum(bool(e["is_correct"]) for e in first_answered), len(first_answered))
    durations = [(s["submitted_at"]-s["started_at"]).total_seconds() for values in sittings.values()
                 for s in values if s["status"] == "submitted" and s["submitted_at"]]
    retry_students = {sid for (sid, _), values in responses.items() if len(values) > 1}
    summary = {"total_questions": len(qids), "cohort_size": len(cohort),
        "students_attempted": len(started), "students_completed": len(completed),
        "participation_percent": _percent(len(started), len(cohort)),
        "completion_percent": _percent(len(completed), len(started)),
        "abandonment_percent": _percent(len(started)-len(completed), len(started)),
        "average_score": round(mean(score_values), 1) if score_values else None,
        "median_score": round(median(score_values), 1) if score_values else None,
        "pass_mark": pass_mark, "pass_rate": _percent(sum(s >= pass_mark for s in score_values), len(score_values)),
        "average_accuracy": final_accuracy, "first_attempt_accuracy": first_accuracy,
        "final_accuracy": final_accuracy,
        "learning_gain": round(final_accuracy-first_accuracy, 1) if final_accuracy is not None and first_accuracy is not None else None,
        "average_attempts_per_question": round(len(response_rows)/len(responses), 2) if responses else None,
        "students_needing_retry_percent": _percent(len(retry_students), len(started)),
        "total_response_events": len(response_rows), "attempts_from_non_enrolled": outsiders,
        "duration": _stats(durations), "response_time": response_time,
        "difficulty_distribution": difficulty_counts,
        "confidence": _confidence(len(started), len(response_rows), len(qids), config)}

    clusters = []
    for topic in topics:
        related = [q for q in question_stats if q["topic"] == topic["topic"]]
        dominant = sum(any(o["classification"] == "strong_misconception" for o in q["options"]) for q in related)
        missed = sum((q["first_attempt_percent"] if q["first_attempt_percent"] is not None else 100) < config.mastery_threshold for q in related)
        if len(related) >= config.min_topic_questions and (dominant or missed >= 2):
            clusters.append({"topic": topic["topic"], "questions": len(related),
                "dominant_distractor_questions": dominant, "low_first_attempt_questions": missed,
                "confidence": topic["confidence"],
                "message": "Repeated response pattern; review concept or item wording"})
    priorities = sorted(question_stats, key=lambda q: (-q["review_priority_score"], q["order"]))
    actions = []
    if priorities and priorities[0]["review_priority"] == "HIGH":
        actions.append(f"Review wording and answer options for question {priorities[0]['order']}.")
    conclusive_weak = [t for t in topics if t["conclusive"] and t["final_accuracy"] is not None and t["final_accuracy"] < config.mastery_threshold]
    if conclusive_weak:
        actions.append(f"Reteach {conclusive_weak[0]['topic']} with a short worked example.")
    support_count = sum(s["support_category"] == "NEEDS_SUPPORT" for s in students)
    if support_count:
        actions.append(f"Plan targeted follow-up for {support_count} student(s) needing support.")
    if summary["abandonment_percent"]:
        actions.append("Contact students with incomplete attempts and check access or timing barriers.")
    if not actions:
        actions.append("Keep the current teaching sequence and monitor the next assessment.")
    logger.info("assessment analytics built", extra={"exam_id": exam_id,
        "cohort_size": len(cohort), "response_events": len(response_rows),
        "confidence": summary["confidence"]})
    answered_questions = [q for q in question_stats if q["correct_percent"] is not None]
    return {"exam_id": exam[0], "exam_title": exam[1], "course_id": exam[2],
        "course_title": exam[3], "doctor_name": exam[4], "pass_mark": pass_mark,
        "summary": summary, "score_distribution": _distribution(score_values),
        "questions": question_stats, "topics": topics, "roster": students,
        "misconception_clusters": clusters, "teaching_actions": actions[:5],
        "hardest": min(answered_questions, key=lambda q: q["correct_percent"])["question_id"] if answered_questions else None,
        "easiest": max(answered_questions, key=lambda q: q["correct_percent"])["question_id"] if answered_questions else None,
        "methodology": {"difficulty_thresholds": {"easy_at": config.easy_at,
            "moderate_at": config.moderate_at},
            "discrimination": "point-biserial Pearson correlation of final item result with score on all other items",
            "topic_min_questions": config.min_topic_questions,
            "topic_min_attempts": config.min_topic_attempts, "llm_used": False}}


def available(conn, course_id=None, doctor_id=None):
    if doctor_id is None:
        return []
    with conn.cursor() as cur:
        cur.execute(LIST_SQL, {"course_id": course_id, "doctor_id": doctor_id})
        return [{"exam_id": r[0], "exam_title": r[1], "course_id": r[2],
                 "course_title": r[3], "total_questions": r[4],
                 "students_attempted": r[5], "attempts": r[6],
                 "last_answered": r[7]} for r in cur.fetchall()]
