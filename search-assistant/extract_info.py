"""Read a student's sentence and hand the backend a search plan as JSON.

    from extract_info import extract
    extract("عايز محاضرات دكتور أحمد في الفسيولوجي")

The sentence reaches the model exactly as typed. No stripping, no folding, no
keyword lists, no regex pre-pass — the model is the part that understands
language, and chewing the input first only takes information away from it.

What the model *does* get is the schema: every table in this database and what
each column holds. That is what lets it answer in the backend's own vocabulary

    {"table": "users", "column": "name", "op": "ilike", "value": "أحمد"}

instead of inventing field names that some translation layer then has to guess
at. Give it the real column names and the mapping problem disappears.

It still does not write SQL. It fills a fixed structure; `validate()` throws out
anything naming a table, column or operator that does not exist; the backend
builds the query from what survives, binding every value as a parameter. A model
that hallucinates a column produces a dropped filter, never a broken query.
"""

import argparse
import json
import os
import sys
from typing import Literal

from pydantic import BaseModel, Field

# Runnable from anywhere. This directory is not an importable package — its name
# has a space in it — so the repo root goes on the path by hand.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.services.llm import ChatModel, LLMUnavailable  # noqa: E402


# ---------------------------------------------------------------------------
# The database, described for the model
# ---------------------------------------------------------------------------
#
# Every table, so the model knows what exists and what does not. `filter` lists
# the columns a search may filter on — the rest are shown for understanding
# only, and `validate()` refuses filters that name them. That keeps ids,
# embeddings and answer keys out of anything a student's sentence can reach.
#
# `--check` diffs this against information_schema, so a migration that adds a
# column cannot silently leave the model working from a stale picture.

SCHEMA = {
    "users": {
        "note": "students and doctors both live here. There is no doctors "
                "table: a doctor is a user with role='doctor'.",
        "columns": {
            "id": "primary key",
            "role": "'student' or 'doctor'",
            "name": "full name as registered, Arabic or English",
            "email": "unique login address",
            "created_at": "when the account was made",
        },
        "filter": ("role", "name"),
    },
    "subjects": {
        "note": "the academic subject a course teaches, e.g. Physiology, "
                "Anatomy. Normalised so 'every Physiology course' is a join.",
        "columns": {
            "id": "primary key",
            "name": "subject name, unique",
        },
        "filter": ("name",),
    },
    "courses": {
        "note": "a course one doctor teaches in one subject for one academic "
                "year.",
        "columns": {
            "id": "primary key",
            "doctor_id": "the teaching doctor, users.id",
            "subject_id": "subjects.id, may be null",
            "academic_year": "study year 1-7, may be null. Never a calendar year.",
            "title": "course title as the doctor wrote it",
            "created_at": "when it was created",
        },
        "filter": ("academic_year", "title"),
    },
    "modules": {
        "note": "a block of lectures inside a course, e.g. 'Cardiovascular' "
                "inside Physiology I.",
        "columns": {
            "id": "primary key",
            "course_id": "courses.id",
            "title": "module title, unique inside its course",
            "position": "teaching order inside the course",
            "created_at": "when it was created",
        },
        "filter": ("title",),
    },
    "lectures": {
        "note": "one lecture: a video plus its transcript. This is what most "
                "students are actually looking for.",
        "columns": {
            "id": "primary key",
            "doctor_id": "the teaching doctor, users.id",
            "course_id": "courses.id, may be null for a standalone lecture",
            "module_id": "modules.id, may be null if not filed yet",
            "title": "lecture title as the doctor wrote it",
            "video_url": "where the video is hosted",
            "created_at": "when it was published",
        },
        "filter": ("title", "created_at"),
    },
    "topics": {
        "note": "a tag on a quiz question, e.g. 'Cardiac output'. Attached to "
                "questions, not to lectures.",
        "columns": {
            "id": "primary key",
            "name": "topic name",
        },
        "filter": ("name",),
    },
    "questions": {
        "note": "quiz questions on a lecture. Never searchable by a student: "
                "the rows carry the answer key.",
        "columns": {
            "id": "primary key",
            "lecture_id": "lectures.id",
            "topic_id": "topics.id, may be null",
            "stem": "the question text",
            "options": "JSON array of choices",
            "correct_option": "the right answer",
            "difficulty": "easy / medium / hard, may be null",
        },
        "filter": (),
    },
    "question_attempts": {
        "note": "one row per answer a student gave. Analytics, not catalog.",
        "columns": {
            "id": "primary key",
            "student_id": "users.id",
            "question_id": "questions.id",
            "is_correct": "whether the answer was right",
            "selected_option": "what the student picked, may be null",
            "answered_at": "when they answered",
        },
        "filter": (),
    },
    "enrollments": {
        "note": "which student is registered on which course. Use it to answer "
                "'my courses' — filter on students, never expose other people's rows.",
        "columns": {
            "id": "primary key",
            "student_id": "users.id",
            "course_id": "courses.id",
            "enrolled_at": "when they registered",
        },
        "filter": (),
    },
    "subscriptions": {
        "note": "a student's paid access to one doctor's material. Separate "
                "from enrolment: it decides whether a video can be watched.",
        "columns": {
            "id": "primary key",
            "student_id": "users.id",
            "doctor_id": "users.id",
            "subscribed_at": "when they subscribed",
        },
        "filter": (),
    },
    "transcript_chunks": {
        "note": "the lecture transcript cut into timed pieces with embeddings. "
                "Searched by meaning, not by a filter — put anything the student "
                "said *about the content* in the `text` field instead.",
        "columns": {
            "id": "primary key",
            "lecture_id": "lectures.id",
            "text": "the spoken words",
            "start_ts": "seconds into the video",
            "end_ts": "seconds into the video",
            "embedding": "1536-dim vector",
        },
        "filter": (),
    },
    "video_events": {
        "note": "every play, pause, seek and heartbeat while watching. Feeds "
                "the engagement report.",
        "columns": {
            "id": "primary key",
            "student_id": "users.id",
            "lecture_id": "lectures.id",
            "event_type": "play, pause, seek, skip, complete, rewatch_segment, "
                          "heartbeat, tab_hidden, tab_visible",
            "video_ts": "seconds into the video",
            "session_id": "one sitting",
            "created_at": "wall clock time of the event",
        },
        "filter": (),
    },
    "reports": {
        "note": "a frozen report produced by finishing a module or an exam.",
        "columns": {
            "id": "primary key",
            "student_id": "users.id",
            "course_id": "courses.id",
            "kind": "'module' or 'exam'",
            "lecture_id": "lectures.id, may be null",
            "payload": "the whole report as JSON",
            "generated_at": "when it fired",
        },
        "filter": (),
    },
    "report_narratives": {
        "note": "the written commentary of a weekly report, cached per week.",
        "columns": {
            "id": "primary key",
            "student_id": "users.id",
            "course_id": "courses.id",
            "week_start": "Saturday the week starts on",
            "fingerprint": "hash of the figures it was written from",
            "narrative": "the commentary as JSON",
            "generated_at": "when it was written",
        },
        "filter": (),
    },
    "notifications": {
        "note": "how a report reaches a student or a doctor in the site.",
        "columns": {
            "id": "primary key",
            "user_id": "who it is for, users.id",
            "kind": "what kind of notice",
            "title": "one line",
            "body": "longer text, may be null",
            "report_id": "reports.id, may be null",
            "student_id": "who it is about, users.id, may be null",
            "read_at": "when it was opened, null while unread",
            "created_at": "when it was sent",
        },
        "filter": (),
    },
    "query_embeddings": {
        "note": "cache of question embeddings. Infrastructure, never searched.",
        "columns": {
            "id": "primary key",
            "query_hash": "sha256 of the question",
            "model": "embedding model name",
            "dim": "vector size",
            "query": "the original question",
            "embedding": "the cached vector",
            "created_at": "when it was cached",
        },
        "filter": (),
    },
}

# Foreign keys, so the model can see how a filter on one table reaches another —
# "lectures by doctor أحمد" is a filter on users.name that arrives through
# lectures.doctor_id. The backend owns the actual JOIN.
RELATIONS = (
    ("courses.doctor_id", "users.id"),
    ("courses.subject_id", "subjects.id"),
    ("modules.course_id", "courses.id"),
    ("lectures.doctor_id", "users.id"),
    ("lectures.course_id", "courses.id"),
    ("lectures.module_id", "modules.id"),
    ("enrollments.student_id", "users.id"),
    ("enrollments.course_id", "courses.id"),
    ("subscriptions.student_id", "users.id"),
    ("subscriptions.doctor_id", "users.id"),
    ("questions.lecture_id", "lectures.id"),
    ("questions.topic_id", "topics.id"),
    ("question_attempts.student_id", "users.id"),
    ("question_attempts.question_id", "questions.id"),
    ("transcript_chunks.lecture_id", "lectures.id"),
    ("video_events.student_id", "users.id"),
    ("video_events.lecture_id", "lectures.id"),
)

# What a search may return rows from. Everything else in SCHEMA is context for
# understanding the question, not a place to send a student's results.
TARGETS = ("lectures", "courses", "modules", "subjects", "users")

# Comparisons the backend knows how to bind. `ilike` is a contains-match, which
# is what a half-remembered name or title needs. The list is enforced by the
# Literal on Filter.op, so an operator the backend cannot bind never parses.
OPS = ("eq", "ilike", "in", "gte", "lte")


def schema_text():
    """The schema as the model sees it."""

    lines = ["TABLES"]

    for table, spec in SCHEMA.items():

        columns = ", ".join(
            f"{name} ({note})" for name, note in spec["columns"].items()
        )

        allowed = ", ".join(spec["filter"]) or "nothing — this table is context only"

        lines.append(f"\n{table} — {spec['note']}")
        lines.append(f"  columns: {columns}")
        lines.append(f"  may be filtered on: {allowed}")

    lines.append("\nFOREIGN KEYS")

    for child, parent in RELATIONS:
        lines.append(f"  {child} -> {parent}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------


class Filter(BaseModel):
    """One comparison, named in the database's own words."""

    table: str = Field(description="table name exactly as listed in TABLES")
    column: str = Field(description="a column that table 'may be filtered on'")
    op: Literal["eq", "ilike", "in", "gte", "lte"] = Field(
        description="ilike for names and titles the student half-remembers"
    )
    value: str = Field(
        description="what to compare against, exactly as the student said it. "
                    "Empty when op is 'in'."
    )
    values: list[str] = Field(
        default_factory=list,
        description="the list, when op is 'in'. Empty otherwise.",
    )
    means: str = Field(
        description="the words in the question this came from, e.g. 'دكتور أحمد'"
    )


class Plan(BaseModel):
    """The search the student asked for."""

    intent: Literal["search", "clarify", "unsupported"] = Field(
        description="search when there is at least one filter or some text to "
                    "match; clarify when the question is too vague to filter on; "
                    "unsupported when the site holds no such thing"
    )
    target: str = Field(
        description="the table the rows come from: lectures, courses, modules, "
                    "subjects or users. Empty when intent is not 'search'."
    )
    filters: list[Filter] = Field(
        default_factory=list,
        description="one entry per thing the student named",
    )
    text: str = Field(
        default="",
        description="what the student said about the *content* — a topic, a "
                    "concept, something said in the lecture. Goes to the "
                    "transcript search, not to a filter. Empty if they only "
                    "named people, titles or years.",
    )
    sort: Literal["relevance", "newest", "position"] = Field(
        default="relevance",
        description="newest if they asked for the latest, position to keep "
                    "teaching order, relevance otherwise",
    )
    limit: int = Field(default=20, description="how many rows, 1 to 50")
    missing: list[str] = Field(
        default_factory=list,
        description="what the student did not say that you need. Only when it "
                    "blocks the search — never list a field the results can "
                    "narrow down on their own.",
    )
    clarify: str = Field(
        default="",
        description="the one question to ask back, in the student's own "
                    "language and dialect. Empty unless intent is 'clarify'.",
    )
    reason: str = Field(
        default="",
        description="why, when intent is 'unsupported'. Empty otherwise.",
    )
    confidence: float = Field(
        default=0.0, description="0 to 1, how sure you are you read it right"
    )



SYSTEM_INSTRUCTION = f"""\
You are the first-stage query planner for the search assistant of an Egyptian medical education platform.

Your job is to read the student's request exactly as written and convert it into a structured search plan.

You do NOT write SQL.
You do NOT query the database directly.
You may only use the tables, columns, relationships, and filterable fields defined in the schema below.

{schema_text()}

RULES

1. PRESERVE USER-WRITTEN NAMES AND TITLES

For free-text entity names and titles — such as:
- doctor names
- lecture titles
- course titles
- module titles

return the value exactly as the student wrote it.

Preserve:
- the original language
- spelling
- characters
- wording

Do NOT translate, normalize, correct spelling, expand abbreviations, or rewrite these values.

Example:
"cardio module" → value = "cardio"

IMPORTANT: All user names in the database are stored in English.

When the student writes a person's name in Arabic, transliterate it to English
before placing it in Filter.value.

Examples:
"احمد" -> "Ahmed"
"أحمد حسن" -> "Ahmed Hassan"
"محمد علي" -> "Mohamed Ali"

Keep the student's original Arabic wording only in Filter.means.

Never put an Arabic personal name in Filter.value when searching users.name.

The backend does a case-insensitive contains-match against the stored value and
nothing more — there is no fuzzy matching and no Arabic normalization behind you.
A name you leave in Arabic will match no row at all.


2. NORMALIZE FIELDS WITH CANONICAL DATABASE VALUES

If a field has a limited canonical representation in the database, convert the student's wording into that database representation.

Examples:

subjects.name is stored in English:
- "الفسيولوجي" → "Physiology"
- "physio" → "Physiology"
- "التشريح" → "Anatomy"
- "هستولوجي" → "Histology"

users.role must always be:
- "doctor"
- "student"

courses.academic_year must be an integer from 1 to 7:
- "سنة أولى" → 1
- "second year" → 2
- "تالتة" → 3

academic_year refers to the student's academic level, NOT a calendar year.


3. DOCTORS ARE USERS

There is no separate doctors table.

Doctors are rows in `users` where:

role = "doctor"

Example:

"محاضرات دكتور أحمد"

means the search plan should filter the doctor's name through `users.name`.

The backend is responsible for following the proper relationships from the doctor to courses or lectures.

If the student explicitly asks to find or list doctors themselves:

target = "users"

and include:

role = "doctor"


4. SUBJECT SEARCH MUST USE subjects.name

If the student refers to a medical subject such as:

- Anatomy
- Physiology
- Histology
- Biochemistry

filter using `subjects.name`.

Do NOT infer the subject by filtering the course title when a dedicated subject field exists.


5. DISTINGUISH CATALOG SEARCH FROM LECTURE-CONTENT SEARCH

Catalog metadata belongs in structured filters.

Examples:
- doctor name
- subject
- academic year
- course
- module
- lecture title

However, if the student asks about something discussed INSIDE a lecture — a concept, topic, explanation, anatomical structure, disease, definition, or something said in the video — put that information in `text`.

Do NOT convert lecture-content concepts into catalog filters.

Example:

"عايز المحاضرة اللي الدكتور شرح فيها sympathetic nervous system"

The concept:

"sympathetic nervous system"

belongs in `text` for semantic transcript search.

It is not automatically a lecture title, module, course, or subject filter.


6. CATALOG AMBIGUITY IS NOT YOUR RESPONSIBILITY

Do NOT try to resolve ambiguity caused by multiple matching database rows.

Example:

If the student says:

"عايز محاضرات دكتور أحمد"

and the database contains three doctors named Ahmed, that is NOT a missing field.

Return the doctor's name as provided.

The backend will execute the search, detect multiple matches, and ask the student which result they mean.

`missing` means:

the student did not provide information that is necessary to construct a meaningful search plan.

It does NOT mean:

the database may contain multiple matching results.

Do NOT ask clarification questions for ambiguity that can only be discovered after executing the search.


7. USE clarify ONLY FOR REQUESTS WITH NO USABLE SEARCH INFORMATION

If the request is too vague to produce any meaningful search plan, set:

intent = "clarify"

and ask exactly ONE short clarification question in the student's language and conversational style.

Example:

"عايز محاضرات"

There is no doctor, subject, year, course, module, title, or content query.

A suitable clarification would be:

"عايز محاضرات مادة إيه؟"

Do not ask several questions at once.


8. UNSUPPORTED REQUESTS

If the student asks for a resource or entity that the platform does not support and that does not exist in the provided schema, set:

intent = "unsupported"

and provide a short one-sentence explanation in `reason`.

Example:

"عايز كتب دكتور أحمد"

If the schema contains no books or book resources:

intent = "unsupported"

Do NOT invent a books table, resource type, or filter.


9. MULTI-TURN CONTEXT

When conversation history is provided, combine the student's new message with relevant information from previous turns.

A clarification answer continues the existing search request rather than starting a new search.

Example:

Previous request:
"عايز محاضرات دكتور أحمد في الفسيولوجي"

Assistant:
"سنة كام؟"

Student:
"سنة تانية"

The resulting search plan should preserve:

doctor = "أحمد"
subject = "Physiology"
academic_year = 2

Do not discard previously established constraints unless the student explicitly corrects or replaces them.

Example:

"لا قصدي سنة تالتة"

should update:

academic_year = 3


10. NEVER INVENT FILTERS

Only include filters explicitly supported by:

a) the student's current message,
b) relevant conversation history,
or
c) canonical normalization of something the student actually said.

Never infer extra constraints simply because they seem likely.

Do NOT add:
- a doctor
- academic year
- subject
- course
- module
- lecture
- role
- or any other filter

unless the student supplied enough information to support it.

A database column may only be used as a filter if the schema explicitly marks it as filterable.

If a column is not marked as "may be filtered on", do not use it.


11. DO NOT GUESS DATABASE RESULTS

You are planning a search, not executing one.

Do NOT claim:
- that a doctor exists
- that a course exists
- that a lecture exists
- that there is one result
- that there are multiple results
- that a student has access

Those facts can only be determined by the backend after executing the search.


12. KEEP THE PLAN MINIMAL

Return only the filters needed to represent the student's request.

Do not duplicate the same constraint across multiple fields.

Do not turn descriptive words into filters unless they clearly refer to a supported catalog field.

Your output must represent what the student asked for — not what you think they probably meant.
"""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract(query, chat_model=None, history=None):
    """One sentence in, one search plan out. Never raises.

    `history` is [(role, text), ...] oldest first — pass the previous turn when
    the student is answering a clarification, so the two get merged.

    The reply is always a dict with `ok`. When the model is unreachable `ok` is
    false and `error` says why; the backend decides whether to fall back to a
    plain text search or to tell the student to try again.
    """

    model = chat_model or ChatModel()

    try:
        plan = model.generate(
            SYSTEM_INSTRUCTION,
            f"سؤال الطالب:\n{query}",
            Plan,
            history=history,
            # The schema in the system instruction is long, and the model thinks
            # before it answers; the default 2048 truncates that into unparsable
            # JSON, which arrives as no reply at all.
            max_output_tokens=4096,
        )

    except LLMUnavailable as error:
        return {"ok": False, "query": query, "error": str(error), "plan": None}

    except Exception as error:  # a bad key, no network, a client-side failure
        return {
            "ok": False,
            "query": query,
            "error": f"{type(error).__name__}: {error}",
            "plan": None,
        }

    return envelope(query, plan)


def envelope(query, plan):
    """The model's answer, checked against the schema."""

    filters, dropped = validate(plan.filters)

    target = plan.target if plan.target in TARGETS else ""

    if plan.target and not target:
        dropped.append({"target": plan.target, "why": "not a searchable table"})

    intent = plan.intent

    # A search that lost everything it was going to search by is not a search.
    if intent == "search" and not (filters or plan.text.strip() or target):
        intent = "clarify"

    return {
        "ok": True,
        "query": query,
        "plan": {
            "intent": intent,
            "target": target,
            "filters": filters,
            "text": plan.text.strip(),
            "sort": plan.sort,
            "limit": max(1, min(plan.limit or 20, 50)),
            "missing": plan.missing,
            "clarify": plan.clarify.strip(),
            "reason": plan.reason.strip(),
            "confidence": round(plan.confidence, 2),
        },
        "dropped": dropped,
    }


def validate(filters):
    """Keep the filters that name something real; report the rest.

    This is the whole safety story. The model is free to hallucinate a column —
    it just never reaches the query, because the backend only ever builds SQL
    from what comes back from here.
    """

    kept = []
    dropped = []

    for item in filters:

        spec = SCHEMA.get(item.table)

        if spec is None:
            dropped.append({"filter": item.model_dump(), "why": "no such table"})
            continue

        if item.column not in spec["columns"]:
            dropped.append({"filter": item.model_dump(), "why": "no such column"})
            continue

        if item.column not in spec["filter"]:
            dropped.append(
                {"filter": item.model_dump(), "why": "column is not filterable"}
            )
            continue

        values = [v for v in item.values if v.strip()]
        value = item.value.strip()

        if item.op == "in":
            if not values:
                dropped.append({"filter": item.model_dump(), "why": "empty list"})
                continue
        elif not value:
            dropped.append({"filter": item.model_dump(), "why": "empty value"})
            continue

        kept.append(
            {
                "table": item.table,
                "column": item.column,
                "op": item.op,
                "value": value,
                "values": values,
                "means": item.means.strip(),
            }
        )

    return kept, dropped


# ---------------------------------------------------------------------------
# Drift check
# ---------------------------------------------------------------------------


def check_schema():
    """Compare SCHEMA against the live database.

    The model's whole picture of the data lives in this file, so a migration
    that lands without updating it leaves the assistant filtering on a column
    that moved. Run this after every migration.
    """

    from app.db import connection

    with connection() as conn:

        rows = conn.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()

    live = {}

    for table, column in rows:
        live.setdefault(table, set()).add(column)

    problems = []

    for table in sorted(set(live) - set(SCHEMA)):
        problems.append(f"table in the database but not in SCHEMA: {table}")

    for table in sorted(set(SCHEMA) - set(live)):
        problems.append(f"table in SCHEMA but not in the database: {table}")

    for table in sorted(set(SCHEMA) & set(live)):

        described = set(SCHEMA[table]["columns"])

        for column in sorted(live[table] - described):
            problems.append(f"{table}.{column} exists but is not described")

        for column in sorted(described - live[table]):
            problems.append(f"{table}.{column} is described but does not exist")

    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", nargs="*", help="what the student typed")
    parser.add_argument(
        "--check", action="store_true", help="diff SCHEMA against the database"
    )
    parser.add_argument(
        "--schema", action="store_true", help="print what the model is told"
    )

    args = parser.parse_args()

    if args.schema:
        print(schema_text())
        return 0

    if args.check:

        problems = check_schema()

        for problem in problems:
            print(problem)

        print("SCHEMA matches the database" if not problems else f"{len(problems)} problems")

        return 1 if problems else 0

    if not args.query:
        parser.error("give me a question, or --check / --schema")

    result = extract(" ".join(args.query))

    print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
