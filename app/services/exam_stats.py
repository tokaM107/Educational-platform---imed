"""Post-exam statistics for the instructor.

An "exam" here is the question set attached to one lecture — the same set whose
completion fires the exam report. This module answers the questions a lecturer
asks the morning after: how did the class do, which question did they fall over,
and which topic needs saying again.

Deterministic on purpose. Every figure is a GROUP BY over `question_attempts`
joined to `questions`; nothing is generated, nothing is interpreted, and the same
data always produces the same page. A written commentary can be layered on later
if the numbers turn out not to speak for themselves — but they usually do, and an
unnecessary model call would make a page that currently answers in milliseconds
take half a minute and cost quota.

Two definitions worth stating, because "score" is ambiguous:

    score      distinct questions answered correctly / questions in the exam.
               Unanswered counts as wrong, which is what a mark out of ten means.
    accuracy   distinct questions correct / distinct questions attempted.
               Fair to someone who only answered half, and the right number for
               judging a question rather than a student.

A question counts as correct for a student if any of their attempts was correct,
matching the convention the weekly report already uses; `first_attempt_percent`
keeps the stricter reading available beside it.

**Distractor analysis.** `question_attempts.selected_option` records the choice
itself, so the view can say *which* wrong answer the class went for. That is the
difference between "38% got it wrong" — which might just be a hard question — and
"34% of them chose C", which says that one distractor is teaching something false
or the stem is ambiguous. Attempts recorded before that column existed have no
choice to report, so every distribution carries the count it was built from and
never passes off a partial picture as a complete one.

**This endpoint exposes correct answers.** A distractor table is unreadable
without marking which option was right, so `/api/exams/*` hands out the answer
key. It is an instructor view and must sit behind authentication before launch —
today nothing stops a student calling it.
"""

import logging
from statistics import mean, median


logger = logging.getLogger(__name__)


# Anything at or above this is a pass. Instructors differ, so it is a parameter
# rather than a constant buried in a query.
DEFAULT_PASS_MARK = 60.0

# Below this many answers a percentage is a coin toss, not a finding. Used to
# mark a question or topic as unreliable rather than to hide it.
MIN_ANSWERS = 3

# A question labelled 'hard' that nearly everyone gets right — or 'easy' that
# nearly everyone misses — is a labelling problem worth surfacing.
EASY_IF_ABOVE = 85.0
HARD_IF_BELOW = 50.0


LECTURE_SQL = """
    SELECT l.id, l.title, l.course_id, c.title, d.name
    FROM lectures AS l
    LEFT JOIN courses AS c ON c.id = l.course_id
    LEFT JOIN users AS d ON d.id = l.doctor_id
    WHERE l.id = %s
"""

# The class: whoever is enrolled on the course the lecture belongs to. Attempts
# by anyone else are excluded from every figure and counted separately, so a
# stray row never silently moves an average.
COHORT_SQL = """
    SELECT e.student_id
    FROM enrollments AS e
    WHERE e.course_id = %s
"""

# One row per attempt, numbered per student per question so the first try can be
# told from the retries.
TRIES_CTE = """
    WITH tries AS (
        SELECT
            a.question_id,
            a.student_id,
            a.is_correct,
            row_number() OVER (
                PARTITION BY a.student_id, a.question_id
                ORDER BY a.answered_at, a.id
            ) AS try_number
        FROM question_attempts AS a
        JOIN questions AS q ON q.id = a.question_id
        WHERE q.lecture_id = %(lecture_id)s
          AND (%(cohort)s::int[] IS NULL OR a.student_id = ANY(%(cohort)s))
    )
"""

QUESTIONS_SQL = TRIES_CTE + """
    SELECT
        q.id,
        q.stem,
        q.difficulty,
        COALESCE(t.name, 'Uncategorised') AS topic,
        count(DISTINCT tr.student_id) AS students,
        count(tr.question_id) AS attempts,
        count(DISTINCT tr.student_id) FILTER (WHERE tr.is_correct) AS students_correct,
        count(*) FILTER (WHERE tr.try_number = 1) AS first_tries,
        count(*) FILTER (WHERE tr.try_number = 1 AND tr.is_correct) AS first_correct
    FROM questions AS q
    LEFT JOIN topics AS t ON t.id = q.topic_id
    LEFT JOIN tries AS tr ON tr.question_id = q.id
    WHERE q.lecture_id = %(lecture_id)s
    GROUP BY q.id, q.stem, q.difficulty, t.name
    ORDER BY q.id
"""

TOPICS_SQL = TRIES_CTE + """
    SELECT
        COALESCE(t.name, 'Uncategorised') AS topic,
        count(DISTINCT q.id) AS questions,
        count(DISTINCT tr.student_id) AS students,
        count(tr.question_id) AS attempts,
        count(*) FILTER (WHERE tr.is_correct) AS correct_attempts
    FROM questions AS q
    LEFT JOIN topics AS t ON t.id = q.topic_id
    LEFT JOIN tries AS tr ON tr.question_id = q.id
    WHERE q.lecture_id = %(lecture_id)s
    GROUP BY t.name
    ORDER BY t.name
"""

# What the class actually picked, per question. Restricted to attempts that have
# a recorded choice — the column is younger than some of the rows.
OPTIONS_SQL = TRIES_CTE.replace(
    "a.is_correct,", "a.is_correct, a.selected_option,"
) + """
    SELECT
        q.id,
        upper(trim(tr.selected_option)) AS chosen,
        count(*) AS picks,
        count(*) FILTER (WHERE tr.try_number = 1) AS first_picks
    FROM questions AS q
    JOIN tries AS tr ON tr.question_id = q.id
    WHERE q.lecture_id = %(lecture_id)s
      AND tr.selected_option IS NOT NULL
    GROUP BY q.id, upper(trim(tr.selected_option))
    ORDER BY q.id, picks DESC
"""

# The answer key, and the option text to label the distribution with.
ANSWERS_SQL = """
    SELECT id, correct_option, options
    FROM questions
    WHERE lecture_id = %s
"""

ROSTER_SQL = TRIES_CTE + """
    SELECT
        u.id,
        u.name,
        u.email,
        count(DISTINCT tr.question_id) AS answered,
        count(DISTINCT tr.question_id) FILTER (WHERE tr.is_correct) AS correct,
        count(tr.question_id) AS attempts
    FROM users AS u
    JOIN tries AS tr ON tr.student_id = u.id
    GROUP BY u.id, u.name, u.email
    ORDER BY u.name
"""

OUTSIDERS_SQL = """
    SELECT count(*)
    FROM question_attempts AS a
    JOIN questions AS q ON q.id = a.question_id
    WHERE q.lecture_id = %(lecture_id)s
      AND (%(cohort)s::int[] IS NOT NULL AND NOT (a.student_id = ANY(%(cohort)s)))
"""


def _percent(part, whole):
    """None when there is no denominator — different from zero, printed so."""

    if not whole:
        return None

    return round(part / whole * 100, 1)


def _calibration(difficulty, correct_percent):
    """Whether the label on a question matches how the class actually found it."""

    if correct_percent is None or not difficulty:
        return None

    label = difficulty.strip().lower()

    if label == "hard" and correct_percent >= EASY_IF_ABOVE:
        return "easier_than_labelled"

    if label == "easy" and correct_percent < HARD_IF_BELOW:
        return "harder_than_labelled"

    return "as_labelled"


def _distribution(scores):
    """Fifths of the mark range, so the shape of the cohort is visible."""

    edges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    buckets = []

    for low, high in edges:

        if high == 100:
            count = sum(1 for score in scores if low <= score <= high)
        else:
            count = sum(1 for score in scores if low <= score < high)

        buckets.append({"low": low, "high": high, "students": count})

    return buckets


# A wrong option this many percent of the class chose is not a spread of guesses;
# it is one specific misconception worth a minute of the next lecture.
DOMINANT_DISTRACTOR = 25.0


def _option_letter(text):
    """"C) Pneumatic bone" -> "C". Options are stored already lettered."""

    label = (text or "").strip()

    if len(label) >= 2 and label[0].isalnum() and label[1] in ").:-":
        return label[0].upper()

    return None


def _distractors(picks, correct_option, options):
    """One row per option offered, with how many chose it.

    Every option is listed even when nobody picked it: a distractor nobody
    touches is itself a finding — the question is really a three-way choice.
    """

    total = sum(count for _, count, _ in picks)
    by_letter = {letter: (count, first) for letter, count, first in picks}

    correct = (correct_option or "").strip().upper()
    rows = []

    for text in options or []:

        letter = _option_letter(text)

        if letter is None:
            continue

        count, first = by_letter.pop(letter, (0, 0))

        rows.append({
            "option": letter,
            "text": text,
            "is_correct": letter == correct,
            "picks": count,
            "first_picks": first,
            "percent": _percent(count, total),
        })

    # A recorded choice that matches no option on the question — a renumbered
    # question, or a client sending something unexpected. Shown, not dropped.
    for letter, (count, first) in sorted(by_letter.items()):
        rows.append({
            "option": letter,
            "text": None,
            "is_correct": letter == correct,
            "picks": count,
            "first_picks": first,
            "percent": _percent(count, total),
        })

    return rows, total


def _top_distractor(distribution):
    """The wrong option the class went for, if one of them dominates."""

    wrong = [row for row in distribution if not row["is_correct"] and row["picks"]]

    if not wrong:
        return None

    worst = max(wrong, key=lambda row: row["picks"])

    if (worst["percent"] or 0) < DOMINANT_DISTRACTOR:
        return None

    return {"option": worst["option"], "text": worst["text"],
            "percent": worst["percent"], "picks": worst["picks"]}


def fetch(conn, lecture_id, pass_mark=DEFAULT_PASS_MARK):
    """Everything the instructor view shows, or None if the lecture is unknown."""

    with conn.cursor() as cur:

        cur.execute(LECTURE_SQL, (lecture_id,))
        lecture = cur.fetchone()

        if lecture is None:
            return None

        _, title, course_id, course_title, doctor_name = lecture

        cohort = None

        if course_id is not None:
            cur.execute(COHORT_SQL, (course_id,))
            cohort = [row[0] for row in cur.fetchall()]

        params = {"lecture_id": lecture_id, "cohort": cohort}

        cur.execute(QUESTIONS_SQL, params)
        question_rows = cur.fetchall()

        cur.execute(TOPICS_SQL, params)
        topic_rows = cur.fetchall()

        cur.execute(ROSTER_SQL, params)
        roster_rows = cur.fetchall()

        cur.execute(OPTIONS_SQL, params)
        option_rows = cur.fetchall()

        cur.execute(ANSWERS_SQL, (lecture_id,))
        answer_rows = cur.fetchall()

        cur.execute(OUTSIDERS_SQL, params)
        outsiders = cur.fetchone()[0]

    total_questions = len(question_rows)

    picks_by_question = {}

    for question_id, chosen, count, first in option_rows:
        picks_by_question.setdefault(question_id, []).append((chosen, count, first))

    answers = {row[0]: (row[1], row[2]) for row in answer_rows}

    questions = []

    for (question_id, stem, difficulty, topic, students, attempts,
         students_correct, first_tries, first_correct) in question_rows:

        correct_percent = _percent(students_correct, students)

        correct_option, options = answers.get(question_id, (None, []))

        distribution, answered_with_choice = _distractors(
            picks_by_question.get(question_id, []), correct_option, options
        )

        questions.append({
            "question_id": question_id,
            "stem": stem,
            "topic": topic,
            "difficulty": difficulty,
            "students_answered": students,
            "attempts": attempts,
            "students_correct": students_correct,
            "correct_percent": correct_percent,
            "first_attempt_percent": _percent(first_correct, first_tries),
            # Above 1.0 means the class needed more than one go on average.
            "attempts_per_student": (
                round(attempts / students, 2) if students else None
            ),
            "reliable": students >= MIN_ANSWERS,
            "calibration": _calibration(difficulty, correct_percent),
            "correct_option": correct_option,
            "options": distribution,
            # How many attempts the distribution is built from. Lower than
            # `attempts` wherever answers predate the selected_option column.
            "answers_recorded": answered_with_choice,
            "top_distractor": _top_distractor(distribution),
        })

    topics = [
        {
            "topic": topic,
            "questions": question_count,
            "students_answered": students,
            "attempts": attempts,
            "correct_percent": _percent(correct_attempts, attempts),
            "reliable": attempts >= MIN_ANSWERS,
        }
        for topic, question_count, students, attempts, correct_attempts in topic_rows
    ]

    roster = []

    for student_id, name, email, answered, correct, attempts in roster_rows:

        roster.append({
            "student_id": student_id,
            "name": name,
            "email": email,
            "questions_answered": answered,
            "questions_correct": correct,
            "attempts": attempts,
            # Unanswered counts against the score, which is what a mark means.
            "score_percent": _percent(correct, total_questions),
            # Fair to a partial sitting, and the right lens on the questions.
            "accuracy_percent": _percent(correct, answered),
            "completed": answered >= total_questions and total_questions > 0,
        })

    roster.sort(key=lambda row: (row["score_percent"] or 0), reverse=True)

    scores = [row["score_percent"] for row in roster if row["score_percent"] is not None]
    completed = [row for row in roster if row["completed"]]
    completed_scores = [row["score_percent"] for row in completed]

    summary = {
        "total_questions": total_questions,
        "cohort_size": len(cohort) if cohort is not None else None,
        "students_attempted": len(roster),
        "students_completed": len(completed),
        "participation_percent": (
            _percent(len(roster), len(cohort)) if cohort else None
        ),
        "total_attempts": sum(row["attempts"] for row in roster),
        # Over everyone who sat any of it, unanswered counting as wrong.
        "average_score": round(mean(scores), 1) if scores else None,
        "median_score": round(median(scores), 1) if scores else None,
        # Over those who answered every question — the honest exam average.
        "average_score_completed": (
            round(mean(completed_scores), 1) if completed_scores else None
        ),
        "average_accuracy": (
            round(mean([row["accuracy_percent"] for row in roster
                        if row["accuracy_percent"] is not None]), 1)
            if roster else None
        ),
        "pass_mark": pass_mark,
        "pass_rate": _percent(
            sum(1 for score in scores if score >= pass_mark), len(scores)
        ),
        "attempts_from_non_enrolled": outsiders,
    }

    ranked = [q for q in questions if q["correct_percent"] is not None and q["reliable"]]

    return {
        "lecture_id": lecture_id,
        "lecture_title": title,
        "course_id": course_id,
        "course_title": course_title,
        "doctor_name": doctor_name,
        "pass_mark": pass_mark,
        "summary": summary,
        "score_distribution": _distribution(scores),
        "questions": questions,
        "topics": sorted(topics, key=lambda row: (row["correct_percent"] or 0)),
        "roster": roster,
        "hardest": min(ranked, key=lambda q: q["correct_percent"])["question_id"]
                   if ranked else None,
        "easiest": max(ranked, key=lambda q: q["correct_percent"])["question_id"]
                   if ranked else None,
    }


LIST_SQL = """
    SELECT
        l.id,
        l.title,
        c.id,
        c.title,
        count(DISTINCT q.id) AS questions,
        count(DISTINCT a.student_id) AS students_attempted,
        count(a.id) AS attempts,
        max(a.answered_at) AS last_answered
    FROM lectures AS l
    JOIN questions AS q ON q.lecture_id = l.id
    LEFT JOIN courses AS c ON c.id = l.course_id
    LEFT JOIN question_attempts AS a ON a.question_id = q.id
    WHERE (%(course_id)s::int IS NULL OR l.course_id = %(course_id)s)
      AND (%(doctor_id)s::int IS NULL OR l.doctor_id = %(doctor_id)s)
    GROUP BY l.id, l.title, c.id, c.title
    ORDER BY max(a.answered_at) DESC NULLS LAST, l.id
"""


def available(conn, course_id=None, doctor_id=None):
    """Every lecture that has questions, so the instructor can pick one."""

    with conn.cursor() as cur:

        cur.execute(LIST_SQL, {"course_id": course_id, "doctor_id": doctor_id})

        return [
            {
                "lecture_id": row[0],
                "lecture_title": row[1],
                "course_id": row[2],
                "course_title": row[3],
                "total_questions": row[4],
                "students_attempted": row[5],
                "attempts": row[6],
                "last_answered": row[7],
            }
            for row in cur.fetchall()
        ]
