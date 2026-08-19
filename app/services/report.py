"""The weekly engagement report: one student, one course, seven days.

Everything here is assembled from rows that already exist — `video_events`
replayed by `engagement.py`, `question_attempts` joined to their topics, and the
course a student is enrolled on. Nothing is precomputed or cached, so a report
regenerated an hour later includes the hour.

Two rules shape the whole module.

**Every number carries its meaning.** Watch time, session duration, time away
and coverage are four different quantities (see `engagement.py`), and a report
that prints them as one column of digits is worse than no report. Each one is
returned next to what it is measured against — watch time against the lecture's
length, time away against the session it happened in — so the page can always
say *why* a number is good or bad.

**Time away is time away.** `tab_hidden` means the lecture page stopped being
visible. It does not mean the student opened social media, and the narrative
prompt is explicit about that: a locked screen, a phone call and another tab are
the same row in the table.

The narrative at the end is the only generated part, and it is optional. If the
model is unreachable the report still returns every number with a notice
attached, the same way a chat answer still returns its video segment.
"""

import logging
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.genai import errors as genai_errors

from app.config import get_settings
from app.services import engagement, prompts, report_cache
from app.services.llm import ChatModel


logger = logging.getLogger(__name__)

# Measured on this report: ~1900 tokens of thinking plus ~800 of Arabic prose.
# The tutor's 2048 truncates it into unparsable JSON, which surfaces as no
# narrative at all rather than a short one.
NARRATIVE_TOKENS = 8192

# A topic needs at least this many questions before "strong" or "weak" means
# anything. One lucky answer is not a strength.
MIN_TOPIC_QUESTIONS = 2

STRONG_ACCURACY = 75.0
WEAK_ACCURACY = 60.0


# -------------------------
# Window
# -------------------------


class Window(NamedTuple):
    """A report's week: UTC instants to query with, local dates to print."""

    since: datetime
    until: datetime
    first_day: object
    last_day: object


def zone():
    """The timezone a week is measured in.

    An unknown name must not take the endpoint down — a mistyped
    REPORT_TIMEZONE degrades to UTC with a warning in the log.
    """

    name = get_settings().report_timezone

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown REPORT_TIMEZONE %r, falling back to UTC", name)
        return timezone.utc


FULL_SQL = """
    SELECT
        min(v.created_at),
        (SELECT min(e.enrolled_at) FROM enrollments AS e
          WHERE e.student_id = %(student_id)s AND e.course_id = %(course_id)s)
    FROM video_events AS v
    JOIN lectures AS l ON l.id = v.lecture_id
    WHERE v.student_id = %(student_id)s AND l.course_id = %(course_id)s
"""


def full_window(conn, student_id, course_id):
    """Everything so far, which is the scope of a completion report.

    A week is the right frame for "how did this week go". It is the wrong frame
    for "you have finished the module": the earlier lectures were watched weeks
    ago, and a seven-day window would leave them out of the report that is
    supposed to sum them up. So this one starts at the student's first event on
    the course — or at their enrolment, if they have somehow finished it without
    the player recording anything — and runs to now.
    """

    with conn.cursor() as cur:
        cur.execute(FULL_SQL, {"student_id": student_id, "course_id": course_id})
        first_event, enrolled = cur.fetchone()

    here = zone()
    now = datetime.now(timezone.utc)

    started = first_event or enrolled or now

    return Window(
        since=None,                       # unbounded: fetch_events takes None
        until=now,
        first_day=started.astimezone(here).date(),
        last_day=now.astimezone(here).date(),
    )


def week_window(week_start=None, days=None):
    """The half-open window [since, until) the report covers.

    Seven *local* days, not seven UTC days. Studying at 23:00 Cairo time is
    still Monday's work; measured in UTC it lands on Tuesday, moving it onto the
    wrong day of the report and, at the edges of the week, out of the report
    altogether.

    The arithmetic is done on local wall clock and converted afterwards, so a
    week containing a daylight-saving change is still seven days long.

    Given a `week_start`, that week. Given nothing, the seven days ending today —
    what a report generated at the end of the week should cover.
    """

    here = zone()
    days = days or get_settings().report_week_days

    if week_start is None:
        week_start = datetime.now(here).date() - timedelta(days=days - 1)

    start = datetime.combine(week_start, time.min, tzinfo=here)
    end = start + timedelta(days=days)

    return Window(
        since=start.astimezone(timezone.utc),
        until=end.astimezone(timezone.utc),
        first_day=week_start,
        last_day=week_start + timedelta(days=days - 1),
    )


# -------------------------
# Reads
# -------------------------


STUDENT_SQL = """
    SELECT id, name, email, role
    FROM users
    WHERE id = %s
"""

COURSE_SQL = """
    SELECT
        c.id,
        c.title,
        d.name,
        e.enrolled_at
    FROM enrollments AS e
    JOIN courses AS c ON c.id = e.course_id
    JOIN users AS d ON d.id = c.doctor_id
    WHERE e.student_id = %(student_id)s
      AND (%(course_id)s::int IS NULL OR c.id = %(course_id)s)
    ORDER BY e.enrolled_at DESC, c.id
    LIMIT 1
"""

LECTURES_SQL = """
    SELECT
        l.id,
        l.title,
        COALESCE(MAX(c.end_ts), 0) AS duration
    FROM lectures AS l
    LEFT JOIN transcript_chunks AS c ON c.lecture_id = l.id
    WHERE l.course_id = %s
    GROUP BY l.id, l.title
    ORDER BY l.id
"""

ATTEMPTS_SQL = """
    SELECT
        q.lecture_id,
        q.id,
        COALESCE(t.name, 'غير مصنّف') AS topic,
        a.is_correct,
        q.difficulty
    FROM question_attempts AS a
    JOIN questions AS q ON q.id = a.question_id
    LEFT JOIN topics AS t ON t.id = q.topic_id
    WHERE a.student_id = %(student_id)s
      AND (%(since)s::timestamptz IS NULL OR a.answered_at >= %(since)s)
      AND (%(until)s::timestamptz IS NULL OR a.answered_at < %(until)s)
      AND q.lecture_id = ANY(%(lecture_ids)s)
    ORDER BY a.answered_at
"""


def fetch_student(conn, student_id):

    with conn.cursor() as cur:
        cur.execute(STUDENT_SQL, (student_id,))
        row = cur.fetchone()

    if row is None:
        return None

    return {"id": row[0], "name": row[1], "email": row[2], "role": row[3]}


def fetch_course(conn, student_id, course_id=None):
    """The course this report is about.

    With no `course_id`, the most recent enrolment — the common case is a
    student on one course, and picking silently beats erroring.
    """

    with conn.cursor() as cur:

        cur.execute(COURSE_SQL, {"student_id": student_id, "course_id": course_id})
        row = cur.fetchone()

    if row is None:
        return None

    return {"id": row[0], "title": row[1], "doctor_name": row[2]}


def fetch_lectures(conn, course_id):
    """The course's lectures with their lengths, in teaching order."""

    with conn.cursor() as cur:
        cur.execute(LECTURES_SQL, (course_id,))
        rows = cur.fetchall()

    return [
        {"id": row[0], "title": row[1], "duration": float(row[2])} for row in rows
    ]


def fetch_attempts(conn, student_id, lecture_ids, since, until):
    """This week's question attempts on the course's lectures."""

    if not lecture_ids:
        return []

    with conn.cursor() as cur:

        cur.execute(
            ATTEMPTS_SQL,
            {
                "student_id": student_id,
                "lecture_ids": list(lecture_ids),
                "since": since,
                "until": until,
            },
        )

        return [
            {
                "lecture_id": row[0],
                "question_id": row[1],
                "topic": row[2],
                "is_correct": row[3],
                "difficulty": row[4],
            }
            for row in cur.fetchall()
        ]


# -------------------------
# Shaping
# -------------------------


def _rate(part, whole):
    """part/whole as a percentage, or None when the denominator is unknown.

    None rather than 0 on purpose: "we cannot work this out" and "it is zero"
    are different findings, and the page prints them differently.
    """

    if not whole or whole <= 0:
        return None

    return round(part / whole * 100, 1)


def _span_out(span):

    return {
        "start": round(span.start, 1),
        "end": round(span.end, 1),
        "seconds": round(span.seconds, 1),
        "start_label": prompts.to_stamp(span.start),
        "end_label": prompts.to_stamp(span.end),
    }


def _score(attempts):
    """Question stats over a set of attempts.

    Counted per question, not per row: answering the same question wrong then
    right is one question learned, not a 50% student. `attempts` keeps the
    retries visible so the two readings stay available.
    """

    by_question = defaultdict(list)

    for attempt in attempts:
        by_question[attempt["question_id"]].append(bool(attempt["is_correct"]))

    solved = sum(1 for results in by_question.values() if any(results))

    return {
        "questions_attempted": len(by_question),
        "questions_correct": solved,
        "attempts": len(attempts),
        "accuracy": _rate(solved, len(by_question)),
    }


def lecture_report(conn, student_id, lecture, window):
    """One lecture's line in the report."""

    events = engagement.fetch_events(
        conn,
        student_id,
        lecture["id"],
        since=window.since,
        until=window.until,
    )

    totals = engagement.replay_sessions(events)

    duration = lecture["duration"]
    covered = engagement.covered_seconds(totals.watched_spans)

    grouped = engagement.by_session(events)
    moments = [event.created_at for event in events]
    here = zone()

    # Watch time attributed to the local day each sitting began on. A sitting
    # that runs past midnight is credited to the evening it started, which is
    # how a student would describe it.
    daily = defaultdict(float)

    for session_id, totals_for_session in engagement.session_totals(events).items():
        day = min(event.created_at for event in grouped[session_id]).astimezone(here).date()
        daily[day] += totals_for_session.watch_time_seconds

    return {
        "lecture_id": lecture["id"],
        "title": lecture["title"],
        "duration_seconds": duration,
        # A lecture with no transcript has no known length, so it can be
        # watched but not scored. Saying so is better than reporting 0% of an
        # unknown total, and it keeps one un-ingested lecture from dragging the
        # course's coverage down.
        "duration_known": duration > 0,
        "opened": bool(events),
        "sessions": len(grouped),
        "completed": totals.completed,
        "first_opened": min(moments) if moments else None,
        "last_opened": max(moments) if moments else None,
        # Time spent with the video running. Can exceed the lecture length when
        # stretches were replayed.
        "watch_time_seconds": totals.watch_time_seconds,
        "watch_percentage": _rate(totals.watch_time_seconds, duration),
        # How much of the lecture was seen at least once. This is the one that
        # answers "did they get through the material".
        "covered_seconds": covered,
        "coverage_percentage": _rate(covered, duration),
        "session_duration_seconds": totals.session_duration_seconds,
        "time_away_seconds": totals.time_away_seconds,
        "time_away_rate": _rate(
            totals.time_away_seconds, totals.session_duration_seconds
        ),
        "pause_count": totals.pause_count,
        "seek_count": totals.seek_count,
        # A lecture that was never opened is entirely "unwatched", which is true
        # and useless: listing 00:00:00-01:05:00 as a gap would bury the real
        # gaps in the lectures they did watch. `opened` already says it.
        "skipped_spans": [
            _span_out(span)
            for span in engagement.missing_spans(totals.watched_spans, duration)
        ] if events else [],
        "rewatched_spans": [
            _span_out(span)
            for span in engagement.repeated_spans(totals.watched_spans)
        ],
        # Local dates, so a 23:30 session counts as that evening's study rather
        # than the small hours of the next day.
        "_days": {moment.astimezone(here).date() for moment in moments},
        "_daily": dict(daily),
    }


def topic_breakdown(attempts):
    """Per-topic accuracy, worst first, with the thin evidence marked."""

    grouped = defaultdict(list)

    for attempt in attempts:
        grouped[attempt["topic"]].append(attempt)

    rows = []

    for topic, topic_attempts in grouped.items():

        score = _score(topic_attempts)

        rows.append(
            {
                "topic": topic,
                "questions_attempted": score["questions_attempted"],
                "questions_correct": score["questions_correct"],
                "accuracy": score["accuracy"],
                # Below this, the accuracy is a coin toss rather than a signal,
                # so the page shows it without calling it a strength or a gap.
                "conclusive": score["questions_attempted"] >= MIN_TOPIC_QUESTIONS,
            }
        )

    return sorted(rows, key=lambda row: (row["accuracy"] or 0, -row["questions_attempted"]))


def build(conn, student_id, course_id=None, week_start=None, chat_model=None,
          with_narrative=True, refresh=False, window=None, kind="weekly",
          lecture_id=None):
    """The whole report as a plain dict, ready to be validated by the schema.

    The measured half is always recomputed from the events. The narrative is
    reused from `report_narratives` when it was written from these exact
    figures, and only regenerated when they have moved — see report_cache.py.
    `refresh=True` regenerates it regardless.
    """

    if window is None:
        window = week_window(week_start)

    student = fetch_student(conn, student_id)

    if student is None:
        return None

    course = fetch_course(conn, student_id, course_id)

    if course is None:
        # Enrolled on nothing: an honest empty report beats a 404, because the
        # answer "you are not registered for a course" is itself the finding.
        return _empty(student, window)

    lectures = [
        lecture_report(conn, student_id, lecture, window)
        for lecture in fetch_lectures(conn, course["id"])
    ]

    attempts = fetch_attempts(
        conn,
        student_id,
        [lecture["lecture_id"] for lecture in lectures],
        window.since,
        window.until,
    )

    by_lecture = defaultdict(list)

    for attempt in attempts:
        by_lecture[attempt["lecture_id"]].append(attempt)

    active_days = set()
    watch_by_day = defaultdict(float)

    for lecture in lectures:

        active_days |= lecture.pop("_days")

        for day, seconds in lecture.pop("_daily").items():
            watch_by_day[day] += seconds

        lecture_attempts = by_lecture[lecture["lecture_id"]]
        lecture["questions"] = _score(lecture_attempts)
        lecture["weak_topics"] = sorted(
            {
                attempt["topic"]
                for attempt in lecture_attempts
                if not attempt["is_correct"]
            }
        )

    topics = topic_breakdown(attempts)

    # Only lectures whose length is known can be a denominator. Counting an
    # un-ingested lecture as 0 seconds of material would flatter the coverage;
    # counting it as material the student failed to watch would slander them.
    scorable = [lecture for lecture in lectures if lecture["duration_known"]]

    material = sum(lecture["duration_seconds"] for lecture in scorable)
    watch = sum(lecture["watch_time_seconds"] for lecture in lectures)
    covered = sum(lecture["covered_seconds"] for lecture in scorable)
    session = sum(lecture["session_duration_seconds"] for lecture in lectures)
    away = sum(lecture["time_away_seconds"] for lecture in lectures)

    score = _score(attempts)

    totals = {
        "lectures_registered": len(lectures),
        "lectures_opened": sum(1 for lecture in lectures if lecture["opened"]),
        "lectures_completed": sum(1 for lecture in lectures if lecture["completed"]),
        "lectures_untouched": sum(1 for lecture in lectures if not lecture["opened"]),
        "lectures_without_length": len(lectures) - len(scorable),
        "lecture_material_seconds": round(material, 1),
        "watch_time_seconds": round(watch, 1),
        "covered_seconds": round(covered, 1),
        "coverage_percentage": _rate(covered, material),
        "session_duration_seconds": round(session, 1),
        "time_away_seconds": round(away, 1),
        "time_away_rate": _rate(away, session),
        "pause_count": sum(lecture["pause_count"] for lecture in lectures),
        "seek_count": sum(lecture["seek_count"] for lecture in lectures),
        "active_days": len(active_days),
        "week_days": window.last_day.toordinal() - window.first_day.toordinal() + 1,
        # Every day of the week, zero-filled, so the page can draw the week as a
        # shape instead of printing "6 active days" and leaving the reader to
        # imagine which six.
        "daily": _daily_out(window, watch_by_day, active_days),
        **score,
    }

    report = {
        "generated_at": datetime.now(timezone.utc),
        "week": _week_out(window),
        "student": {"id": student["id"], "name": student["name"], "email": student["email"]},
        "course": course,
        "totals": totals,
        "lectures": lectures,
        "topics": topics,
        "strengths": [
            row for row in topics
            if row["conclusive"] and (row["accuracy"] or 0) >= STRONG_ACCURACY
        ],
        "weaknesses": [
            row for row in topics
            if row["conclusive"] and (row["accuracy"] or 0) < WEAK_ACCURACY
        ],
        "narrative": None,
        "notice": None,
    }

    report["kind"] = kind

    if lecture_id is not None:
        report["lecture_id"] = lecture_id
        report["lecture_title"] = next(
            (line["title"] for line in lectures if line["lecture_id"] == lecture_id),
            None,
        )

    if with_narrative:

        report["narrative"], report["notice"] = _narrative(
            conn, report, window, chat_model=chat_model, refresh=refresh,
            # Only a weekly narrative is cached against its figures. A completion
            # report is stored whole by whoever triggered it, so caching the
            # narrative separately would keep two copies of the same text.
            cache=(kind == "weekly"),
        )

    return report


def _daily_out(window, watch_by_day, active_days):
    """One row per day of the week, in order, including the empty ones."""

    rows = []
    day = window.first_day

    while day <= window.last_day:

        rows.append(
            {
                "date": day,
                "watch_time_seconds": round(watch_by_day.get(day, 0.0), 1),
                "active": day in active_days,
            }
        )

        day += timedelta(days=1)

    return rows


def _week_out(window):

    return {
        "start": window.first_day,
        "end": window.last_day,
        "days": window.last_day.toordinal() - window.first_day.toordinal() + 1,
    }


def _empty(student, window):

    return {
        "generated_at": datetime.now(timezone.utc),
        "week": _week_out(window),
        "student": {"id": student["id"], "name": student["name"], "email": student["email"]},
        "course": None,
        "totals": None,
        "lectures": [],
        "topics": [],
        "strengths": [],
        "weaknesses": [],
        "narrative": None,
        "notice": prompts.REPORT_NO_COURSE,
    }


def _narrative(conn, report, window, chat_model=None, refresh=False, cache=True):
    """The doctor's read on the week, as (narrative, notice).

    Three outcomes, in order of preference:

      1. a stored narrative written from these exact figures — the normal case
         once a week has closed, and free;
      2. a freshly generated one, stored for next time;
      3. the model is unreachable, so whatever narrative is stored for this week
         even if the figures have moved since, flagged as such — and if there is
         nothing stored, no narrative at all.

    Case 3 is the same bargain as a chat answer that keeps its video segment
    when the model is down: the numbers are true without the commentary, so a
    missing commentary must never cost the student their report.
    """

    prompt = prompts.build_report_prompt(report, kind=report.get("kind", "weekly"))
    expected = report_cache.fingerprint(prompt)

    student_id = report["student"]["id"]
    course_id = report["course"]["id"]

    if cache and not refresh:

        stored = report_cache.get(
            conn, student_id, course_id, window.first_day, expected=expected
        )

        if stored is not None:
            return stored["narrative"], None

    try:
        model = chat_model or ChatModel()

        reply = model.generate(
            system_instruction=prompts.REPORT_SYSTEM_INSTRUCTION,
            user_prompt=prompt,
            response_schema=prompts.ReportNarrative,
            # A narrative is several times a chat answer, and the budget covers
            # the model's thinking too — at the default it truncates mid-JSON
            # and arrives as no narrative at all.
            max_output_tokens=NARRATIVE_TOKENS,
        )

        narrative = reply.model_dump()

        if cache:
            report_cache.put(
                conn, student_id, course_id, window.first_day, expected, narrative
            )

        return narrative, None

    except (RuntimeError, genai_errors.APIError) as error:

        # RuntimeError covers both LLMUnavailable and a missing GEMINI_API_KEY.
        logger.error("report narrative unavailable: %s", error)

        fallback = (
            report_cache.get(conn, student_id, course_id, window.first_day)
            if cache else None
        )

        if fallback is not None:
            return fallback["narrative"], prompts.REPORT_NARRATIVE_STALE

        return None, prompts.REPORT_LLM_DOWN
