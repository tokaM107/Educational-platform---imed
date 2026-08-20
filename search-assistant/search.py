"""Run the plan from extract_info against the database and return a link.

    from search import answer
    answer("عايز محاضرة الدورة الدموية لدكتور أحمد")

Stage two. `extract_info` turned the sentence into a plan; this file is the only
place that talks to Postgres, and the only place that knows what a URL looks
like. The model's output is data here, never code: every filter is looked up in
the target's join map and bound as a parameter, so a value the model invented
can change what is matched but never what is executed.

The outcome is one of five words, and they are the whole product:

    go           exactly one row matched — `url` is where to send the student
    choose       several matched — the frontend lists them and asks which
    none         the plan was valid and the catalog has nothing like it
    clarify      the sentence never had enough in it to search (from stage one)
    unsupported  the platform has no such thing (from stage one)

`choose` is where catalog ambiguity gets settled. Stage one deliberately does
not know that three doctors are called أحمد; it says "doctor name = أحمد", this
file returns three rows, and the student picks. That is why the question is
asked *after* the query rather than before it.

Matching is plain SQL. The catalog is stored in English — names, course, module
and lecture titles — so `ILIKE` is already case-insensitive and a contains-match
handles a half-remembered name on its own. There is no normalisation layer here
and there should not be one: the moment matching needs a character table, the
answer is to fix the data or move to the semantic search, not to grow a second
language model out of string rules.

The transcript is the exception, and stays in the language it was spoken in.
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

for path in (_ROOT, _HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

from extract_info import extract  # noqa: E402


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
#
# Every URL the site actually serves. A course, a module, a subject and a doctor
# have no page of their own, so their rows link to a lecture inside them and say
# so rather than inventing a route that would 404.

PLAYER = "/?lecture_id={id}"


# ---------------------------------------------------------------------------
# How each target is queried
# ---------------------------------------------------------------------------
#
# `direct` — tables reachable by a many-to-one join, already in the FROM clause.
#            A filter on them is a plain WHERE.
# `exists` — tables that fan out from the target. Filtering on them through the
#            join would return the same course once per matching lecture, so
#            they become EXISTS subqueries instead.
#
# A filter naming a table in neither is dropped: there is no path from the rows
# the student asked for to the thing they tried to filter on.

TARGETS = {
    "lectures": {
        "select": """
            l.id, l.title,
            u.id, u.name,
            c.id, c.title, c.academic_year,
            s.name,
            m.id, m.title, m.position,
            (SELECT count(*) FROM transcript_chunks t WHERE t.lecture_id = l.id)
        """,
        "from": """
            FROM lectures l
            LEFT JOIN users u    ON u.id = l.doctor_id
            LEFT JOIN courses c  ON c.id = l.course_id
            LEFT JOIN subjects s ON s.id = c.subject_id
            LEFT JOIN modules m  ON m.id = l.module_id
        """,
        "direct": {
            "lectures": "l", "users": "u", "courses": "c",
            "subjects": "s", "modules": "m",
        },
        "exists": {},
        "lecture_scope": "{cond}",
        "order": {
            "relevance": "c.id NULLS FIRST, m.position NULLS FIRST, l.id",
            "newest": "l.created_at DESC, l.id DESC",
            "position": "c.id NULLS FIRST, m.position NULLS FIRST, l.id",
        },
    },
    "courses": {
        "select": """
            c.id, c.title, c.academic_year,
            u.id, u.name,
            s.name,
            (SELECT count(*) FROM modules m WHERE m.course_id = c.id),
            (SELECT count(*) FROM lectures l WHERE l.course_id = c.id),
            (SELECT min(l.id) FROM lectures l WHERE l.course_id = c.id)
        """,
        "from": """
            FROM courses c
            LEFT JOIN users u    ON u.id = c.doctor_id
            LEFT JOIN subjects s ON s.id = c.subject_id
        """,
        "direct": {"courses": "c", "users": "u", "subjects": "s"},
        "exists": {
            "modules": "EXISTS (SELECT 1 FROM modules m "
                       "WHERE m.course_id = c.id AND {cond})",
            "lectures": "EXISTS (SELECT 1 FROM lectures l "
                        "WHERE l.course_id = c.id AND {cond})",
        },
        "lecture_scope": "EXISTS (SELECT 1 FROM lectures l "
                         "WHERE l.course_id = c.id AND {cond})",
        "order": {
            "relevance": "c.academic_year NULLS LAST, c.id",
            "newest": "c.created_at DESC, c.id DESC",
            "position": "c.academic_year NULLS LAST, c.id",
        },
    },
    "modules": {
        "select": """
            m.id, m.title, m.position,
            c.id, c.title, c.academic_year,
            u.id, u.name,
            s.name,
            (SELECT count(*) FROM lectures l WHERE l.module_id = m.id),
            (SELECT min(l.id) FROM lectures l WHERE l.module_id = m.id)
        """,
        "from": """
            FROM modules m
            JOIN courses c       ON c.id = m.course_id
            LEFT JOIN users u    ON u.id = c.doctor_id
            LEFT JOIN subjects s ON s.id = c.subject_id
        """,
        "direct": {"modules": "m", "courses": "c", "users": "u", "subjects": "s"},
        "exists": {
            "lectures": "EXISTS (SELECT 1 FROM lectures l "
                        "WHERE l.module_id = m.id AND {cond})",
        },
        "lecture_scope": "EXISTS (SELECT 1 FROM lectures l "
                         "WHERE l.module_id = m.id AND {cond})",
        "order": {
            "relevance": "c.id, m.position, m.id",
            "newest": "m.created_at DESC, m.id DESC",
            "position": "c.id, m.position, m.id",
        },
    },
    "subjects": {
        "select": """
            s.id, s.name,
            (SELECT count(*) FROM courses c WHERE c.subject_id = s.id),
            (SELECT count(*) FROM lectures l JOIN courses c ON c.id = l.course_id
             WHERE c.subject_id = s.id),
            (SELECT min(l.id) FROM lectures l JOIN courses c ON c.id = l.course_id
             WHERE c.subject_id = s.id)
        """,
        "from": "FROM subjects s",
        "direct": {"subjects": "s"},
        "exists": {
            "courses": "EXISTS (SELECT 1 FROM courses c "
                       "WHERE c.subject_id = s.id AND {cond})",
            "users": "EXISTS (SELECT 1 FROM courses c JOIN users u ON u.id = c.doctor_id "
                     "WHERE c.subject_id = s.id AND {cond})",
            "modules": "EXISTS (SELECT 1 FROM courses c JOIN modules m ON m.course_id = c.id "
                       "WHERE c.subject_id = s.id AND {cond})",
            "lectures": "EXISTS (SELECT 1 FROM courses c JOIN lectures l ON l.course_id = c.id "
                        "WHERE c.subject_id = s.id AND {cond})",
        },
        "lecture_scope": "EXISTS (SELECT 1 FROM courses c JOIN lectures l ON l.course_id = c.id "
                         "WHERE c.subject_id = s.id AND {cond})",
        "order": {"relevance": "s.name", "newest": "s.id DESC", "position": "s.name"},
    },
    "users": {
        "select": """
            u.id, u.name, u.role,
            (SELECT count(*) FROM courses c WHERE c.doctor_id = u.id),
            (SELECT count(*) FROM lectures l WHERE l.doctor_id = u.id),
            (SELECT min(l.id) FROM lectures l WHERE l.doctor_id = u.id)
        """,
        "from": "FROM users u",
        "direct": {"users": "u"},
        "exists": {
            "courses": "EXISTS (SELECT 1 FROM courses c "
                       "WHERE c.doctor_id = u.id AND {cond})",
            "subjects": "EXISTS (SELECT 1 FROM courses c JOIN subjects s ON s.id = c.subject_id "
                        "WHERE c.doctor_id = u.id AND {cond})",
            "modules": "EXISTS (SELECT 1 FROM courses c JOIN modules m ON m.course_id = c.id "
                       "WHERE c.doctor_id = u.id AND {cond})",
            "lectures": "EXISTS (SELECT 1 FROM lectures l "
                        "WHERE l.doctor_id = u.id AND {cond})",
        },
        "lecture_scope": "EXISTS (SELECT 1 FROM lectures l "
                         "WHERE l.doctor_id = u.id AND {cond})",
        "order": {"relevance": "u.name", "newest": "u.created_at DESC, u.id DESC",
                  "position": "u.name"},
    },
}

# Columns that are not text. Folding a smallint is a type error, and a year the
# model wrote as "2026" has to fail loudly rather than match nothing quietly.
NUMERIC = {("courses", "academic_year"), ("modules", "position")}
TEMPORAL = {("lectures", "created_at")}


# ---------------------------------------------------------------------------
# Building the query
# ---------------------------------------------------------------------------


def condition(spec, item):
    """One filter as (sql, params), or (None, why) if it cannot be expressed."""

    table, column, op = item["table"], item["column"], item["op"]

    alias = spec["direct"].get(table)
    wrapper = None

    if alias is None:

        if table not in spec["exists"]:
            return None, f"no path from this search to {table}"

        # Inside an EXISTS the subquery uses the table's own name as its alias.
        alias = {"users": "u", "courses": "c", "modules": "m",
                 "lectures": "l", "subjects": "s"}[table]
        wrapper = spec["exists"][table]

    col = f"{alias}.{column}"
    key = (table, column)

    if key in NUMERIC:

        value = item["value"]

        if not value.lstrip("-").isdigit():
            return None, f"{column} needs a number, got {value!r}"

        sql, params = f"{col} = %s::smallint", [int(value)]

    elif key in TEMPORAL:
        cast = {"gte": ">=", "lte": "<=", "eq": "::date ="}.get(op, ">=")
        sql, params = f"{col} {cast} %s::timestamptz", [item["value"]]

    elif op == "ilike":
        sql = f"{col} ILIKE '%%' || %s || '%%'"
        params = [item["value"]]

    elif op == "in":
        sql = (f"lower({col}) IN "
               "(SELECT lower(x.v) FROM unnest(%s::text[]) AS x(v))")
        params = [item["values"]]

    elif op == "eq":
        sql, params = f"lower({col}) = lower(%s)", [item["value"]]

    else:
        return None, f"{op} is not supported on text"

    if wrapper:
        sql = wrapper.format(cond=sql)

    return sql, params


def build(plan):
    """The plan as (sql, params, dropped)."""

    target = plan["target"]
    spec = TARGETS[target]

    where = []
    params = []
    dropped = []

    for item in plan["filters"]:

        sql, extra = condition(spec, item)

        if sql is None:
            dropped.append({"filter": item, "why": extra})
            continue

        where.append(sql)
        params.extend(extra)

    text = plan.get("text", "").strip()

    if text:
        # Content words match the lecture title or anything said in it. The
        # transcript is the point: "the lecture where he explained the cardiac
        # cycle" is not a title, it is something in the middle of a video.
        inner = (
            "(l.title ILIKE '%%' || %s || '%%'"
            " OR EXISTS (SELECT 1 FROM transcript_chunks t"
            " WHERE t.lecture_id = l.id AND t.text ILIKE '%%' || %s || '%%'))"
        )
        where.append(spec["lecture_scope"].format(cond=inner))
        params.extend([text, text])

    clause = "\nWHERE " + "\n  AND ".join(where) if where else ""
    order = spec["order"].get(plan.get("sort", "relevance"), spec["order"]["relevance"])

    sql = (
        f"SELECT{spec['select'].rstrip()}\n{spec['from'].strip()}"
        f"{clause}\nORDER BY {order}\nLIMIT %s"
    )

    return sql, params + [plan.get("limit", 20)], dropped


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _person(user_id, name):
    return None if user_id is None else {"id": user_id, "name": name}


def _lecture(row):

    (lecture_id, title, doctor_id, doctor, course_id, course, year,
     subject, module_id, module, position, chunks) = row

    return {
        "kind": "lecture",
        "id": lecture_id,
        "title": title,
        "doctor": _person(doctor_id, doctor),
        "course": None if course_id is None else {
            "id": course_id, "title": course, "academic_year": year,
        },
        "subject": subject,
        "module": None if module_id is None else {
            "id": module_id, "title": module, "position": position,
        },
        "transcript_chunks": chunks,
        "url": PLAYER.format(id=lecture_id),
        "url_opens": "the lecture page",
    }


def _course(row):

    course_id, title, year, doctor_id, doctor, subject, modules, lectures, first = row

    return {
        "kind": "course",
        "id": course_id,
        "title": title,
        "academic_year": year,
        "subject": subject,
        "doctor": _person(doctor_id, doctor),
        "modules": modules,
        "lectures": lectures,
        "url": None if first is None else PLAYER.format(id=first),
        "url_opens": "the first lecture — the site has no course page",
    }


def _module(row):

    (module_id, title, position, course_id, course, year,
     doctor_id, doctor, subject, lectures, first) = row

    return {
        "kind": "module",
        "id": module_id,
        "title": title,
        "position": position,
        "course": {"id": course_id, "title": course, "academic_year": year},
        "subject": subject,
        "doctor": _person(doctor_id, doctor),
        "lectures": lectures,
        "url": None if first is None else PLAYER.format(id=first),
        "url_opens": "the first lecture — the site has no module page",
    }


def _subject(row):

    subject_id, name, courses, lectures, first = row

    return {
        "kind": "subject",
        "id": subject_id,
        "name": name,
        "courses": courses,
        "lectures": lectures,
        "url": None if first is None else PLAYER.format(id=first),
        "url_opens": "a lecture in the subject — the site has no subject page",
    }


def _user(row):

    user_id, name, role, courses, lectures, first = row

    return {
        "kind": role,
        "id": user_id,
        "name": name,
        "courses": courses,
        "lectures": lectures,
        "url": None if first is None else PLAYER.format(id=first),
        "url_opens": "one of their lectures — the site has no doctor page",
    }


ROW = {
    "lectures": _lecture, "courses": _course, "modules": _module,
    "subjects": _subject, "users": _user,
}


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def search(envelope, conn=None):
    """Run a stage-one envelope (or a bare plan) and return what to show."""

    plan = envelope.get("plan", envelope) if isinstance(envelope, dict) else envelope
    query = envelope.get("query", "") if isinstance(envelope, dict) else ""

    if plan is None:
        return _out(query, "error", notes=[envelope.get("error", "no plan")])

    if plan["intent"] == "clarify":
        return _out(query, "clarify", plan=plan, clarify=plan.get("clarify", ""),
                    missing=plan.get("missing", []))

    if plan["intent"] == "unsupported":
        return _out(query, "unsupported", plan=plan, reason=plan.get("reason", ""))

    if plan["target"] not in TARGETS:
        return _out(query, "error", plan=plan,
                    notes=[f"unknown target {plan['target']!r}"])

    sql, params, dropped = build(plan)

    if conn is None:
        from app.db import connection

        with connection() as owned:
            rows = owned.execute(sql, params).fetchall()

    else:
        rows = conn.execute(sql, params).fetchall()

    results = [ROW[plan["target"]](row) for row in rows]

    outcome = "go" if len(results) == 1 else "choose" if results else "none"

    notes = [] if results else diagnose(plan, conn)

    return _out(
        query, outcome,
        plan=plan,
        results=results,
        url=results[0]["url"] if outcome == "go" else None,
        dropped=dropped,
        notes=notes,
        sql=sql,
        params=[list(p) if isinstance(p, list) else p for p in params],
    )


def diagnose(plan, conn=None):
    """Which filter emptied the result.

    "Nothing found" is not an answer anyone can act on. Every filter is re-run
    on its own, so the reply can say *which* condition matched nothing — a
    misspelled doctor, a subject nobody teaches, a year with no courses. The
    frontend can drop that one filter and offer the rest; whoever is reading the
    logs can see a value that never matches anything and go fix the data.

    Only runs when the search came back empty, so the extra queries cost
    nothing in the normal case.
    """

    parts = [{"filters": [item], "text": ""} for item in plan["filters"]]

    if plan.get("text", "").strip():
        parts.append({"filters": [], "text": plan["text"]})

    if len(parts) < 2:
        return []

    notes = []

    for part in parts:

        alone = dict(plan, filters=part["filters"], text=part["text"], limit=1000)
        sql, params, _ = build(alone)
        sql = sql.replace("SELECT" + TARGETS[plan["target"]]["select"].rstrip(),
                          "SELECT count(*) OVER ()", 1)

        if conn is None:
            from app.db import connection

            with connection() as owned:
                rows = owned.execute(sql, params).fetchall()
        else:
            rows = conn.execute(sql, params).fetchall()

        count = rows[0][0] if rows else 0

        if part["filters"]:
            item = part["filters"][0]
            what = (f"{item['table']}.{item['column']} {item['op']} "
                    f"{item['value'] or item['values']!r}")
        else:
            what = f"text {part['text']!r}"

        notes.append(f"{what} alone matches {count}")

    return notes


def _out(query, outcome, **extra):

    out = {
        "ok": outcome != "error",
        "query": query,
        "outcome": outcome,
        # What the model understood. Carried back with the rows so a caller can
        # tell a misread sentence from an empty catalog — the two look identical
        # from a result list alone.
        "plan": None,
        "url": None,
        "results": [],
        "total": 0,
        "clarify": "",
        "reason": "",
        "missing": [],
        "dropped": [],
        "notes": [],
        "sql": "",
        "params": [],
    }

    out.update(extra)
    out["total"] = len(out["results"])

    return out


def answer(query, conn=None, history=None, chat_model=None):
    """Sentence in, link out. The whole assistant in one call."""

    return search(extract(query, chat_model=chat_model, history=history), conn=conn)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", nargs="*", help="what the student typed")
    parser.add_argument("--plan", help="a plan as JSON, to search without the model")
    parser.add_argument("--sql", action="store_true", help="print the SQL too")

    args = parser.parse_args()

    if args.plan:
        result = search({"query": "", "plan": json.loads(args.plan)})

    elif args.query:
        result = answer(" ".join(args.query))

    else:
        parser.error("give me a question, or --plan '<json>'")

    if not args.sql:
        result.pop("sql"), result.pop("params")

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
