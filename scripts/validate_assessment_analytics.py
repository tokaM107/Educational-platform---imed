"""Seed and reconcile a clearly isolated persisted teacher analytics exam.

Run only against development/staging after migration 20260909120000. Rows are
idempotently namespaced with ``[ASSESSMENT-ANALYTICS-VALIDATION]`` and are left
in place for audit. No unrelated row is updated or deleted.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

from app.schemas.exams import ExamStats
from app.services import exam_stats

MARKER = "[ASSESSMENT-ANALYTICS-VALIDATION]"


def one(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def seed(conn):
    now = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)
    with conn.cursor() as cur:
        doctor = one(cur, """
            INSERT INTO users(role,name,email) VALUES('doctor',%s,%s)
            ON CONFLICT(email) DO UPDATE SET name=EXCLUDED.name RETURNING id
        """, (f"{MARKER} Teacher", "assessment.analytics.teacher@example.invalid"))
        course = one(cur, """
            INSERT INTO courses(doctor_id,title,slug,status)
            VALUES(%s,%s,%s,'draft') ON CONFLICT(slug) DO UPDATE SET title=EXCLUDED.title
            RETURNING id
        """, (doctor, f"{MARKER} Course", "assessment-analytics-validation"))
        topic_ids = {}
        for topic in ("Foundations", "Clinical application"):
            name = f"{MARKER} {topic}"
            cur.execute("SELECT id FROM topics WHERE name=%s", (name,))
            found = cur.fetchone()
            topic_ids[topic] = found[0] if found else one(
                cur, "INSERT INTO topics(name) VALUES(%s) RETURNING id", (name,)
            )
        cur.execute("SELECT id FROM exams WHERE course_id=%s AND title=%s", (course, f"{MARKER} Exam"))
        found = cur.fetchone()
        exam = found[0] if found else one(cur, """
            INSERT INTO exams(course_id,title,duration_minutes,pass_score,max_attempts,is_published)
            VALUES(%s,%s,30,60,2,false) RETURNING id
        """, (course, f"{MARKER} Exam"))

        question_ids, option_ids = [], {}
        for order in range(1, 5):
            topic = "Foundations" if order <= 2 else "Clinical application"
            cur.execute("SELECT id FROM exam_questions WHERE exam_id=%s AND order_index=%s", (exam, order))
            found = cur.fetchone()
            if found:
                question = found[0]
                cur.execute("""
                    UPDATE exam_questions SET text=%s,topic_id=%s,topic=%s,
                    learning_objective=%s,difficulty=%s WHERE id=%s
                """, (f"{MARKER} Question {order}", topic_ids[topic], topic,
                      f"Objective {order}", "moderate", question))
            else:
                question = one(cur, """
                    INSERT INTO exam_questions(exam_id,text,type,points,order_index,topic,
                        topic_id,learning_objective,difficulty)
                    VALUES(%s,%s,'single_choice',1,%s,%s,%s,%s,%s) RETURNING id
                """, (exam, f"{MARKER} Question {order}", order, topic,
                      topic_ids[topic], f"Objective {order}", "moderate"))
            question_ids.append(question)
            option_ids[question] = []
            for option_order, text in enumerate(("A distractor", "B correct", "C distractor"), 1):
                cur.execute("SELECT id FROM exam_options WHERE question_id=%s AND order_index=%s", (question, option_order))
                found = cur.fetchone()
                option = found[0] if found else one(cur, """
                    INSERT INTO exam_options(question_id,text,is_correct,order_index)
                    VALUES(%s,%s,%s,%s) RETURNING id
                """, (question, f"{MARKER} {text}", option_order == 2, option_order))
                option_ids[question].append(option)

        students = []
        for index in range(12):
            email = f"assessment.analytics.student.{index}@example.invalid"
            student = one(cur, """
                INSERT INTO users(role,name,email) VALUES('student',%s,%s)
                ON CONFLICT(email) DO UPDATE SET name=EXCLUDED.name RETURNING id
            """, (f"{MARKER} Student {index + 1:02d}", email))
            students.append(student)
            cur.execute("""
                INSERT INTO enrollments(student_id,course_id,status)
                VALUES(%s,%s,'active') ON CONFLICT(student_id,course_id)
                DO UPDATE SET status='active',expires_at=NULL
            """, (student, course))

        # Ten start, nine submit, one remains in progress, two never start.
        # Correctness patterns: q1=9/10, q2 final=7/10 after four useful retries,
        # q3=5/10 with deliberately negative discrimination, q4=4/9 + one timeout.
        patterns = {
            0: (True, True, False, False), 1: (True, True, False, False),
            2: (True, True, False, False), 3: (True, True, False, False),
            4: (True, True, False, True), 5: (True, True, True, True),
            6: (True, True, True, True), 7: (True, False, True, True),
            8: (True, False, True, False), 9: (False, False, True, None),
        }
        for index, student in enumerate(students[:10]):
            started = now + timedelta(minutes=index * 2)
            submitted = index < 9
            awarded = sum(value is True for value in patterns[index])
            cur.execute("SELECT id FROM exam_attempts WHERE exam_id=%s AND user_id=%s AND attempt_number=1", (exam, student))
            found = cur.fetchone()
            if found:
                attempt = found[0]
            elif submitted:
                attempt = one(cur, """
                    INSERT INTO exam_attempts(exam_id,user_id,started_at,submitted_at,
                        score,passed,status,attempt_number,deadline_at,max_score,
                        awarded_points,submission_reason)
                    VALUES(%s,%s,%s,%s,%s,%s,'submitted',1,%s,4,%s,'student') RETURNING id
                """, (exam, student, started, started + timedelta(minutes=10 + index),
                      round(awarded / 4 * 100), awarded >= 3, started + timedelta(minutes=30), awarded))
            else:
                attempt = one(cur, """
                    INSERT INTO exam_attempts(exam_id,user_id,started_at,status,attempt_number,
                        deadline_at,max_score) VALUES(%s,%s,%s,'in_progress',1,%s,4) RETURNING id
                """, (exam, student, started, started + timedelta(minutes=30)))

            for qindex, question in enumerate(question_ids):
                final_correct = patterns[index][qindex]
                shown = started + timedelta(minutes=qindex)
                # Students 1-4 first choose the concentrated wrong A on q2,
                # then four correct it; student 5 keeps the initial correct row.
                retry = qindex == 1 and index < 4
                first_correct = False if retry else final_correct
                first_option = option_ids[question][1 if first_correct else 0]
                if final_correct is None:
                    cur.execute("""
                        INSERT INTO assessment_question_attempts(exam_attempt_id,
                            exam_question_id,student_id,attempt_number,shown_at,timed_out)
                        VALUES(%s,%s,%s,1,%s,true)
                        ON CONFLICT(exam_attempt_id,exam_question_id,attempt_number) DO NOTHING
                    """, (attempt, question, student, shown))
                else:
                    cur.execute("""
                        INSERT INTO assessment_question_attempts(exam_attempt_id,
                            exam_question_id,student_id,attempt_number,selected_option_ids,
                            is_correct,shown_at,answered_at,response_time_ms)
                        VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s)
                        ON CONFLICT(exam_attempt_id,exam_question_id,attempt_number) DO NOTHING
                    """, (attempt, question, student, [first_option], first_correct, shown,
                          shown + timedelta(seconds=8 + qindex * 4), (8 + qindex * 4) * 1000))
                if retry:
                    cur.execute("""
                        INSERT INTO assessment_question_attempts(exam_attempt_id,
                            exam_question_id,student_id,attempt_number,selected_option_ids,
                            is_correct,shown_at,answered_at,response_time_ms)
                        VALUES(%s,%s,%s,2,%s,true,%s,%s,18000)
                        ON CONFLICT(exam_attempt_id,exam_question_id,attempt_number) DO NOTHING
                    """, (attempt, question, student, [option_ids[question][1]],
                          shown + timedelta(seconds=20), shown + timedelta(seconds=38)))
    conn.commit()
    return {"doctor_id": doctor, "course_id": course, "exam_id": exam,
            "student_ids": students, "question_ids": question_ids}


def validate(conn, ids):
    report = ExamStats(**exam_stats.fetch(conn, ids["exam_id"]))
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(DISTINCT a.user_id),
                   count(DISTINCT a.user_id) FILTER(WHERE a.status='submitted'),
                   avg(a.awarded_points::numeric/a.max_score*100),
                   percentile_cont(.5) WITHIN GROUP(ORDER BY a.awarded_points::numeric/a.max_score*100),
                   count(*) FILTER(WHERE a.awarded_points::numeric/a.max_score*100>=60)::numeric/count(*)*100
            FROM exam_attempts a JOIN enrollments en ON en.student_id=a.user_id
            WHERE a.exam_id=%s AND en.course_id=%s AND en.status='active'
        """, (ids["exam_id"], ids["course_id"]))
        exam_sql = cur.fetchone()
        cur.execute("""
            WITH ranked AS (
              SELECT r.*,row_number() OVER(PARTITION BY student_id,exam_question_id ORDER BY a.attempt_number,r.attempt_number,r.shown_at,r.id) first_rank,
                     row_number() OVER(PARTITION BY student_id,exam_question_id ORDER BY a.attempt_number DESC,r.attempt_number DESC,r.shown_at DESC,r.id DESC) final_rank
              FROM assessment_question_attempts r JOIN exam_attempts a ON a.id=r.exam_attempt_id
              JOIN exam_questions q ON q.id=r.exam_question_id WHERE q.exam_id=%s
            )
            SELECT count(*) FILTER(WHERE first_rank=1 AND answered_at IS NOT NULL),
                   count(*) FILTER(WHERE first_rank=1 AND is_correct),
                   count(*) FILTER(WHERE final_rank=1 AND answered_at IS NOT NULL),
                   count(*) FILTER(WHERE final_rank=1 AND is_correct),
                   count(DISTINCT student_id) FILTER(WHERE attempt_number>1)
            FROM ranked
        """, (ids["exam_id"],))
        response_sql = cur.fetchone()
        cur.execute("""
            SELECT q.order_index,count(*) FILTER(WHERE r.answered_at IS NOT NULL),
                   count(*) FILTER(WHERE r.is_correct),
                   percentile_cont(.5) WITHIN GROUP(ORDER BY r.response_time_ms)
                       FILTER(WHERE r.answered_at IS NOT NULL)
            FROM assessment_question_attempts r JOIN exam_questions q ON q.id=r.exam_question_id
            WHERE q.exam_id=%s AND r.attempt_number=(SELECT max(x.attempt_number)
              FROM assessment_question_attempts x WHERE x.exam_attempt_id=r.exam_attempt_id
              AND x.exam_question_id=r.exam_question_id)
            GROUP BY q.order_index ORDER BY q.order_index
        """, (ids["exam_id"],))
        items_sql = cur.fetchall()
        cur.execute("SELECT count(*) FROM assessment_question_attempts r JOIN exam_questions q ON q.id=r.exam_question_id WHERE q.exam_id=%s", (ids["exam_id"],))
        raw_events = cur.fetchone()[0]

    assert report.summary.cohort_size == 12
    assert report.summary.students_attempted == exam_sql[0] == 10
    assert report.summary.students_completed == exam_sql[1] == 9
    assert report.summary.participation_percent == 83.3
    assert report.summary.completion_percent == 90.0
    assert report.summary.abandonment_percent == 10.0
    assert report.summary.total_response_events == raw_events == 44
    assert report.summary.first_attempt_accuracy == round(response_sql[1] / response_sql[0] * 100, 1)
    assert report.summary.final_accuracy == round(response_sql[3] / response_sql[2] * 100, 1)
    assert report.summary.learning_gain == round(report.summary.final_accuracy-report.summary.first_attempt_accuracy, 1)
    assert [q.students_answered for q in report.questions] == [row[1] for row in items_sql]
    assert [q.students_correct for q in report.questions] == [row[2] for row in items_sql]
    assert all(topic.questions == 2 for topic in report.topics)
    assert all(topic.conclusive for topic in report.topics)
    assert any(q.discrimination_label == "negative" for q in report.questions)
    assert any(o.classification == "non_functioning" for q in report.questions for o in q.options)
    assert any(q.review_priority == "HIGH" for q in report.questions)
    assert {s.support_category for s in report.roster} >= {
        "STRONG_PERFORMANCE", "NEEDS_SUPPORT", "INCOMPLETE_OR_INSUFFICIENT"
    }
    return {"ids": ids, "report": report.model_dump(mode="json"),
        "sql_verification": {
            "exam_lifecycle_and_scores": {"source": "exam_attempts JOIN enrollments",
                "result": [str(value) for value in exam_sql]},
            "first_vs_final": {"source": "assessment_question_attempts ranked per student/question",
                "result": list(response_sql)},
            "item_accuracy_and_time": {"source": "assessment_question_attempts JOIN exam_questions",
                "result": [[str(value) for value in row] for row in items_sql]},
            "raw_response_events": raw_events}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output")
    parser.add_argument("--output-html")
    args = parser.parse_args()
    with psycopg.connect(args.database_url) as conn:
        result = validate(conn, seed(conn))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    if args.output_html:
        root = Path(__file__).resolve().parent.parent
        payload = json.dumps(result["report"], ensure_ascii=False).replace("</", "<\\/")
        listing = json.dumps([{
            "exam_id": result["report"]["exam_id"],
            "exam_title": result["report"]["exam_title"],
            "total_questions": result["report"]["summary"]["total_questions"],
            "students_attempted": result["report"]["summary"]["students_attempted"],
        }], ensure_ascii=False)
        html = f"""<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>
<link rel='stylesheet' href='{(root/'app/static/report.css').as_uri()}'>
<link rel='stylesheet' href='{(root/'app/static/exam.css').as_uri()}'></head><body>
<div class='toolbar'><label class='field'><select id='exam'></select></label>
<label class='field'><input id='pass-mark'></label><button id='print'>PDF</button></div>
<main class='sheet' id='sheet'></main><script>
function requireSession(){{return true}} function currentUser(){{return {{role:'doctor'}}}}
const validationReport={payload};const validationList={listing};
async function api(url){{return new Response(JSON.stringify(url.includes('?')&&url.includes('/api/exams/')?validationReport:validationList),{{status:200,headers:{{'Content-Type':'application/json'}}}})}}
</script><script src='{(root/'app/static/exam.js').as_uri()}'></script></body></html>"""
        Path(args.output_html).write_text(html, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
