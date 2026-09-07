"""Persist and verify an isolated production-schema learning report.

Run against development/staging only, after the learning analytics migration:

    python -m scripts.validate_learning_analytics --database-url postgresql://...

Rows are idempotently namespaced by ``[ANALYTICS-VALIDATION]`` and remain in
the database for SQL inspection.  No existing row is updated or deleted.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from app.schemas.reports import WeeklyReport
from app.services import report


MARKER = "[ANALYTICS-VALIDATION]"


def one(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def upsert_user(cur, email, name):
    return one(cur, """
        INSERT INTO users (role, name, email)
        VALUES ('student', %s, %s)
        ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (name, email))


def seed(conn, window):
    key = str(window.first_day)
    with conn.cursor() as cur:
        doctor = one(cur, """
            INSERT INTO users (role, name, email)
            VALUES ('doctor', %s, %s)
            ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """, (f"{MARKER} Dr Analytics", f"analytics.doctor.{key}@example.invalid"))
        student = upsert_user(
            cur, f"analytics.student.{key}@example.invalid",
            f"{MARKER} Student A",
        )
        empty_student = upsert_user(
            cur, f"analytics.empty.{key}@example.invalid",
            f"{MARKER} Student No Evidence",
        )
        course = one(cur, """
            INSERT INTO courses (doctor_id, title, slug, status)
            VALUES (%s, %s, %s, 'draft')
            ON CONFLICT (slug) DO UPDATE SET title = EXCLUDED.title
            RETURNING id
        """, (doctor, f"{MARKER} Human Anatomy", f"analytics-validation-{key}"))
        for sid in (student, empty_student):
            cur.execute("""
                INSERT INTO enrollments (student_id, course_id)
                VALUES (%s, %s) ON CONFLICT (student_id, course_id) DO NOTHING
            """, (sid, course))
        module = one(cur, """
            INSERT INTO course_modules (course_id, title, order_index)
            VALUES (%s, %s, 1)
            ON CONFLICT (course_id, order_index) DO UPDATE SET title = EXCLUDED.title
            RETURNING id
        """, (course, f"{MARKER} Skeletal System"))
        video = one(cur, """
            INSERT INTO course_items (
                course_id, module_id, type, title, order_index, video_provider,
                video_ref, duration_seconds
            ) VALUES (%s, %s, 'video', %s, 1, 'bunny', %s, 600)
            ON CONFLICT (module_id, order_index) DO UPDATE
            SET title = EXCLUDED.title, duration_seconds = EXCLUDED.duration_seconds
            RETURNING id
        """, (course, module, f"{MARKER} Vertebral Column", f"validation-{key}"))
        cur.execute("SELECT id FROM topics WHERE name = %s", (f"{MARKER} Vertebral column anatomy",))
        topic_row = cur.fetchone()
        topic = topic_row[0] if topic_row else one(
            cur, "INSERT INTO topics (name) VALUES (%s) RETURNING id",
            (f"{MARKER} Vertebral column anatomy",),
        )

        current = window.since + timedelta(hours=9)
        previous = window.since - timedelta(days=7) + timedelta(hours=9)

        def event(at, kind, video_ts, session, ordinal, playback=1.0):
            event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{key}:{session}:{ordinal}")
            cur.execute("""
                INSERT INTO video_events (
                    student_id, video_id, event_type, video_ts, session_id,
                    playback_rate, client_event_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (student_id, client_event_id) WHERE client_event_id IS NOT NULL
                DO NOTHING
            """, (student, video, kind, video_ts, session, playback, event_id, at))

        # Previous week: one short day and lower checkpoint performance.
        event(previous, "play", 0, f"validation-prev-{key}", 1)
        event(previous + timedelta(seconds=30), "heartbeat", 30, f"validation-prev-{key}", 2)
        event(previous + timedelta(seconds=60), "pause", 60, f"validation-prev-{key}", 3)

        # Current week: two active days, a forward seek, pause, rewind and replay.
        session = f"validation-current-a-{key}"
        for ordinal, seconds, kind, position in [
            (10, 0, "play", 0), (11, 30, "heartbeat", 30),
            (12, 40, "seek", 300), (13, 70, "heartbeat", 330),
            (14, 100, "pause", 360),
        ]:
            event(current + timedelta(seconds=seconds), kind, position, session, ordinal)
        session_b = f"validation-current-b-{key}"
        day_b = current + timedelta(days=3)
        for ordinal, seconds, kind, position in [
            (20, 0, "play", 300), (21, 30, "heartbeat", 330),
            (22, 60, "seek", 300), (23, 90, "heartbeat", 330),
            (24, 120, "pause", 360),
        ]:
            event(day_b + timedelta(seconds=seconds), kind, position, session_b, ordinal)

        cur.execute("""
            INSERT INTO student_study_sessions (
                student_id, course_id, video_id, session_key, started_at,
                last_event_at, ended_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (student, course, video, session, current,
              current + timedelta(seconds=100), current + timedelta(seconds=100)))

        checkpoint_ids = []
        for position, stamp in enumerate((60, 210, 330, 480), 1):
            checkpoint_ids.append(one(cur, """
                INSERT INTO checkpoint_questions (
                    course_id, video_id, topic_id, source_key, prompt, options,
                    correct_answer, checkpoint_timestamp_seconds,
                    allowed_time_seconds, position
                ) VALUES (%s, %s, %s, %s, %s, %s, 'B', %s, 20, %s)
                ON CONFLICT (course_id, source_key) DO UPDATE SET prompt = EXCLUDED.prompt
                RETURNING id
            """, (course, video, topic, f"validation-{key}-{position}",
                  f"Checkpoint {position}", Jsonb(["A", "B", "C"]), stamp, position)))

        def checkpoint_attempt(question, shown, session_id, number, answer=None, timeout=False):
            answered = answer is not None
            cur.execute("""
                INSERT INTO checkpoint_attempts (
                    student_id, course_id, checkpoint_question_id, session_id,
                    shown_at, answered_at, response_time_ms, selected_answer,
                    is_correct, skipped, timed_out, attempt_number
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s, %s)
                ON CONFLICT (student_id, checkpoint_question_id, session_id, attempt_number)
                DO NOTHING
            """, (
                student, course, question, session_id, shown,
                shown + timedelta(seconds=10) if answered else None,
                10000 if answered else None, answer,
                answer == "B" if answered else None, timeout, number,
            ))

        checkpoint_attempt(checkpoint_ids[0], previous + timedelta(seconds=65), f"validation-prev-{key}", 1, "A")
        checkpoint_attempt(checkpoint_ids[0], current + timedelta(seconds=65), session, 1, "B")
        checkpoint_attempt(checkpoint_ids[1], current + timedelta(seconds=75), session, 1, "A")
        checkpoint_attempt(checkpoint_ids[2], day_b + timedelta(seconds=95), session_b, 1, "B")
        checkpoint_attempt(checkpoint_ids[3], day_b + timedelta(seconds=125), session_b, 1, timeout=True)

        cur.execute("SELECT id FROM exams WHERE course_id = %s AND title = %s", (course, f"{MARKER} Weekly quiz"))
        found = cur.fetchone()
        exam = found[0] if found else one(cur, """
            INSERT INTO exams (course_id, title, duration_minutes, pass_score)
            VALUES (%s, %s, 20, 60) RETURNING id
        """, (course, f"{MARKER} Weekly quiz"))
        cur.execute(
            "SELECT id FROM exam_attempts WHERE exam_id = %s AND user_id = %s AND started_at = %s",
            (exam, student, current),
        )
        found = cur.fetchone()
        exam_attempt = found[0] if found else one(cur, """
            INSERT INTO exam_attempts (
                exam_id, user_id, started_at, submitted_at, answers, score, passed
            ) VALUES (%s, %s, %s, %s, '{}'::jsonb, 67, true) RETURNING id
        """, (exam, student, current, current + timedelta(minutes=10)))
        for order, correct in enumerate((True, True, False), 1):
            cur.execute(
                "SELECT id FROM exam_questions WHERE exam_id = %s AND order_index = %s",
                (exam, order),
            )
            found = cur.fetchone()
            question = found[0] if found else one(cur, """
                INSERT INTO exam_questions (
                    exam_id, text, type, points, order_index, topic,
                    learning_objective, cognitive_level, difficulty
                ) VALUES (%s, %s, 'single_choice', 1, %s, %s, %s,
                          'understanding', 'medium') RETURNING id
            """, (exam, f"Validation assessment {order}", order,
                  f"{MARKER} Vertebral column anatomy", "Identify vertebral structures"))
            cur.execute("""
                INSERT INTO assessment_question_results (
                    student_id, course_id, exam_attempt_id, exam_question_id,
                    selected_answer, is_correct, awarded_points, assessed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (exam_attempt_id, exam_question_id) DO NOTHING
            """, (student, course, exam_attempt, question, Jsonb(["B"]), correct,
                  1 if correct else 0, current + timedelta(minutes=10)))

        cur.execute("""
            INSERT INTO course_weekly_assignments (
                course_id, video_id, week_start, available_from, due_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (course, video, window.first_day, window.since, window.until))

        for source_id, assessed, correct in [
            (f"immediate-{key}-1", window.since - timedelta(days=4), True),
            (f"immediate-{key}-2", window.since - timedelta(days=4), True),
            (f"delayed-{key}", current, False),
        ]:
            cur.execute("""
                INSERT INTO retention_assessment_results (
                    student_id, course_id, topic_id, source_type,
                    source_result_id, is_correct, assessed_at
                ) VALUES (%s, %s, %s, 'practice', %s, %s, %s)
                ON CONFLICT (student_id, source_type, source_result_id) DO NOTHING
            """, (student, course, topic, source_id, correct, assessed))

    conn.commit()
    return {
        "doctor_id": doctor, "student_id": student,
        "empty_student_id": empty_student, "course_id": course,
        "video_id": video, "topic_id": topic,
        "checkpoint_question_ids": checkpoint_ids,
    }


def validate(conn, ids, window):
    payload = report.build(
        conn, ids["student_id"], ids["course_id"],
        week_start=window.first_day, with_narrative=False,
    )
    model = WeeklyReport(**payload)
    empty = report.build(
        conn, ids["empty_student_id"], ids["course_id"],
        week_start=window.first_day, with_narrative=False,
    )

    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) AS shown,
                   count(*) FILTER (WHERE answered_at IS NOT NULL) AS answered,
                   count(*) FILTER (WHERE is_correct) AS correct,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY response_time_ms)
                       FILTER (WHERE answered_at IS NOT NULL) AS median_ms
            FROM checkpoint_attempts
            WHERE student_id = %s AND course_id = %s
              AND shown_at >= %s AND shown_at < %s
        """, (ids["student_id"], ids["course_id"], window.since, window.until))
        sql_checkpoint = cur.fetchone()
        cur.execute("""
            SELECT count(*), count(*) FILTER (WHERE is_correct)
            FROM assessment_question_results
            WHERE student_id = %s AND course_id = %s
              AND assessed_at >= %s AND assessed_at < %s
        """, (ids["student_id"], ids["course_id"], window.since, window.until))
        sql_assessment = cur.fetchone()
        cur.execute("""
            SELECT count(*) FROM weekly_analytics_snapshots
            WHERE student_id = %s AND course_id = %s AND week_start = %s
        """, (ids["student_id"], ids["course_id"], window.first_day))
        snapshots = cur.fetchone()[0]

    assert tuple(sql_checkpoint[:3]) == (
        model.checkpoints.shown, model.checkpoints.answered, model.checkpoints.correct
    )
    assert float(sql_checkpoint[3]) / 1000 == model.checkpoints.median_response_time_seconds
    assert tuple(sql_assessment) == (3, 2)
    assert model.totals.questions_attempted == 3
    assert model.mastery.overall.score is not None
    assert model.retention.status == "AVAILABLE"
    assert snapshots == 1
    assert empty["mastery"]["overall"]["score"] is None
    assert empty["checkpoints"]["shown"] == 0
    assert empty["totals"]["watch_time_seconds"] == 0

    return {
        "rows": ids,
        "sql_checkpoint": {
            "shown": sql_checkpoint[0], "answered": sql_checkpoint[1],
            "correct": sql_checkpoint[2], "median_response_ms": float(sql_checkpoint[3]),
        },
        "sql_assessment": {"questions": sql_assessment[0], "correct": sql_assessment[1]},
        "report": {
            "coverage_percentage": model.totals.coverage_percentage,
            "watch_time_seconds": model.totals.watch_time_seconds,
            "active_days": model.totals.active_days,
            "checkpoint_accuracy": model.checkpoints.accuracy,
            "checkpoint_completion": model.checkpoints.completion,
            "quiz_accuracy": model.totals.accuracy,
            "mastery": model.mastery.overall.score,
            "mastery_confidence": model.mastery.overall.confidence,
            "retention": model.retention.model_dump(),
            "trend": {key: value.model_dump() for key, value in model.trend.items()},
            "progress": model.progress.model_dump(),
            "page_count": 3,
        },
        "student_isolation": "PASS: second enrolled student has zero evidence and no scores",
        "snapshot_rows": snapshots,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--week-start")
    parser.add_argument("--output-html")
    args = parser.parse_args()
    window = report.week_window(
        __import__("datetime").date.fromisoformat(args.week_start)
        if args.week_start else None
    )
    with psycopg.connect(args.database_url) as conn:
        ids = seed(conn, window)
        result = validate(conn, ids, window)
        if args.output_html:
            payload = WeeklyReport(**report.build(
                conn, ids["student_id"], ids["course_id"],
                week_start=window.first_day, with_narrative=False,
            )).model_dump(mode="json")
            root = Path(__file__).resolve().parent.parent
            data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
            html = f"""<!doctype html><html lang='ar' dir='rtl'><head>
<meta charset='utf-8'><link rel='stylesheet' href='{(root / 'app/static/report.css').as_uri()}'>
</head><body><div class='toolbar' id='toolbar'><label class='field'><select id='subject'></select></label>
<div class='week-nav'><button id='prev'></button><span id='week-label'></span><button id='next'></button></div>
<button id='print'></button><button id='rewrite'></button></div><main class='sheet' id='sheet'></main>
<script>const VALIDATION_REPORT={data}; function requireSession(){{return true}}
async function api(url){{return {{ok:true,json:async()=>url.includes('/subjects')?[{{student_id:VALIDATION_REPORT.student.id,course_id:VALIDATION_REPORT.course.id,student_name:VALIDATION_REPORT.student.name,course_title:VALIDATION_REPORT.course.title}}]:VALIDATION_REPORT,text:async()=>''}}}}</script>
<script src='{(root / 'app/static/report.js').as_uri()}'></script></body></html>"""
            Path(args.output_html).write_text(html, encoding="utf-8")
            result["rendered_html"] = args.output_html
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
