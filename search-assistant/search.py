"""Run a validated plan against the public catalog and return safe result rows.

    from search import answer
    answer("عايز كورسات دكتور أحمد للسنة التانية")

Stage two. `extract_info` turned the sentence into a plan; this file is the only
place that talks to Postgres. The model's output is data here, never code: every filter is looked up in
the target's join map and bound as a parameter, so a value the model invented
can change what is matched but never what is executed.

The outcome is one of five words, and they are the whole product:

    go           exactly one row matched
    choose       several matched — the frontend lists them and asks which
    none         the plan was valid and the catalog has nothing like it
    clarify      the sentence never had enough in it to search (from stage one)
    unsupported  the platform has no such thing (from stage one)

`choose` is where catalog ambiguity gets settled. Stage one deliberately does
not know that three doctors are called أحمد; it says "doctor name = أحمد", this
file returns three rows, and the student picks. That is why the question is
asked *after* the query rather than before it.

Matching is parameterized SQL over public catalog metadata. Course/book drafts,
archived rows, inactive categories/levels, students, private access records,
lectures, and transcripts are outside this query layer.
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
# The API returns catalog rows. This repository has no public course/book/catalog
# detail routes, so the assistant deliberately returns no invented links.


# ---------------------------------------------------------------------------
# How each target is queried
# ---------------------------------------------------------------------------
#
# `direct` — tables reachable by a many-to-one join, already in the FROM clause.
#            A filter on them is a plain WHERE.
# `exists` — tables that fan out from the target. Filtering on them through the
#            join could return the same target once per matching child, so
#            they become EXISTS subqueries instead.
#
# A filter naming a table in neither is dropped: there is no path from the rows
# the student asked for to the thing they tried to filter on.

LEVEL_JOIN = """
LEFT JOIN LATERAL (
    SELECT 'pre_college'::text AS type, p.id, p.name_en, p.name_ar,
           p.stage AS group_name, p.year_number
    FROM pre_college_stages p
    WHERE p.id = cat.pre_college_stage_id AND p.is_active
    UNION ALL
    SELECT 'college'::text, cs.id, cs.name_en, cs.name_ar,
           cs.faculty, cs.year_number
    FROM college_stages cs
    WHERE cs.id = cat.college_stage_id AND cs.is_active
    ORDER BY type DESC
    LIMIT 1
) e ON TRUE
"""

LEVELS_FROM = """
FROM (
    SELECT 'pre_college'::text AS type, p.id, p.name_en, p.name_ar,
           p.stage AS group_name, p.year_number, p.display_order
    FROM pre_college_stages p WHERE p.is_active
    UNION ALL
    SELECT 'college'::text, cs.id, cs.name_en, cs.name_ar,
           cs.faculty, cs.year_number, cs.display_order
    FROM college_stages cs WHERE cs.is_active
) e
"""


TARGETS = {
    "courses": {
        "select": """
            c.id, c.title, c.slug, c.subtitle, c.description, c.academic_year,
            c.language, c.course_level, c.price, c.published_at,
            u.id, u.name, cat.id, cat.name_en, cat.name_ar, cat.slug,
            e.type, e.id, e.name_en, e.name_ar, e.group_name, e.year_number
        """,
        "from": """
            FROM courses c
            JOIN users u ON u.id = c.doctor_id AND u.role = 'doctor'
            LEFT JOIN categories cat ON cat.id = c.category_id AND cat.is_active
        """ + LEVEL_JOIN,
        "base": ["c.status = 'published'"],
        "direct": {"courses": "c", "users": "u", "categories": "cat",
                   "educational_levels": "e"},
        "exists": {},
        "text": "(c.title ILIKE '%%' || %s || '%%' OR c.subtitle ILIKE '%%' || %s || '%%' OR c.description ILIKE '%%' || %s || '%%')",
        "order": {"relevance": "c.title, c.id", "newest": "c.published_at DESC NULLS LAST, c.id DESC",
                  "position": "c.title, c.id"},
    },
    "books": {
        "select": """
            b.id, b.title, b.slug, b.subtitle, b.description, b.language,
            b.price, b.pdf_page_count, b.published_at,
            u.id, u.name, cat.id, cat.name_en, cat.name_ar, cat.slug,
            e.type, e.id, e.name_en, e.name_ar, e.group_name, e.year_number
        """,
        "from": """
            FROM books b
            JOIN users u ON u.id = b.doctor_id AND u.role = 'doctor'
            LEFT JOIN categories cat ON cat.id = b.category_id AND cat.is_active
        """ + LEVEL_JOIN,
        "base": ["b.status = 'published'"],
        "direct": {"books": "b", "users": "u", "categories": "cat",
                   "educational_levels": "e"},
        "exists": {},
        "text": "(b.title ILIKE '%%' || %s || '%%' OR b.subtitle ILIKE '%%' || %s || '%%' OR b.description ILIKE '%%' || %s || '%%')",
        "order": {"relevance": "b.title, b.id", "newest": "b.published_at DESC NULLS LAST, b.id DESC",
                  "position": "b.title, b.id"},
    },
    "users": {
        "select": """
            u.id, u.name,
            (SELECT count(*) FROM courses c WHERE c.doctor_id = u.id AND c.status = 'published'),
            (SELECT count(*) FROM books b WHERE b.doctor_id = u.id AND b.status = 'published')
        """,
        "from": "FROM users u",
        "base": ["u.role = 'doctor'"],
        "direct": {"users": "u"},
        "exists": {
            "courses": "EXISTS (SELECT 1 FROM courses c WHERE c.doctor_id = u.id AND c.status = 'published' AND {cond})",
            "books": "EXISTS (SELECT 1 FROM books b WHERE b.doctor_id = u.id AND b.status = 'published' AND {cond})",
            "categories": "EXISTS (SELECT 1 FROM (SELECT doctor_id, category_id FROM courses WHERE status = 'published' UNION ALL SELECT doctor_id, category_id FROM books WHERE status = 'published') item JOIN categories cat ON cat.id = item.category_id AND cat.is_active WHERE item.doctor_id = u.id AND {cond})",
            "educational_levels": "EXISTS (SELECT 1 FROM (SELECT doctor_id, category_id FROM courses WHERE status = 'published' UNION ALL SELECT doctor_id, category_id FROM books WHERE status = 'published') item JOIN categories cat ON cat.id = item.category_id AND cat.is_active " + LEVEL_JOIN + " WHERE item.doctor_id = u.id AND {cond})",
        },
        "text": "u.name ILIKE '%%' || %s || '%%'",
        "order": {"relevance": "u.name, u.id", "newest": "u.created_at DESC, u.id DESC",
                  "position": "u.name, u.id"},
    },
    "categories": {
        "select": """
            cat.id, cat.name_en, cat.name_ar, cat.slug, cat.parent_id,
            e.type, e.id, e.name_en, e.name_ar, e.group_name, e.year_number,
            (SELECT count(*) FROM courses c WHERE c.category_id = cat.id AND c.status = 'published'),
            (SELECT count(*) FROM books b WHERE b.category_id = cat.id AND b.status = 'published')
        """,
        "from": "FROM categories cat\n" + LEVEL_JOIN,
        "base": ["cat.is_active"],
        "direct": {"categories": "cat", "educational_levels": "e"},
        "exists": {
            "courses": "EXISTS (SELECT 1 FROM courses c WHERE c.category_id = cat.id AND c.status = 'published' AND {cond})",
            "books": "EXISTS (SELECT 1 FROM books b WHERE b.category_id = cat.id AND b.status = 'published' AND {cond})",
            "users": "EXISTS (SELECT 1 FROM (SELECT doctor_id, category_id FROM courses WHERE status = 'published' UNION ALL SELECT doctor_id, category_id FROM books WHERE status = 'published') item JOIN users u ON u.id = item.doctor_id AND u.role = 'doctor' WHERE item.category_id = cat.id AND {cond})",
        },
        "text": "(cat.name_en ILIKE '%%' || %s || '%%' OR cat.name_ar ILIKE '%%' || %s || '%%')",
        "order": {"relevance": "cat.display_order, cat.name_en, cat.id",
                  "newest": "cat.created_at DESC, cat.id DESC",
                  "position": "cat.display_order, cat.name_en, cat.id"},
    },
    "educational_levels": {
        "select": """
            e.type, e.id, e.name_en, e.name_ar, e.group_name, e.year_number,
            (SELECT count(*) FROM categories cat WHERE cat.is_active AND
                ((e.type = 'pre_college' AND cat.pre_college_stage_id = e.id) OR
                 (e.type = 'college' AND cat.college_stage_id = e.id))),
            (SELECT count(*) FROM courses c JOIN categories cat ON cat.id = c.category_id
             WHERE c.status = 'published' AND cat.is_active AND
                ((e.type = 'pre_college' AND cat.pre_college_stage_id = e.id) OR
                 (e.type = 'college' AND cat.college_stage_id = e.id))),
            (SELECT count(*) FROM books b JOIN categories cat ON cat.id = b.category_id
             WHERE b.status = 'published' AND cat.is_active AND
                ((e.type = 'pre_college' AND cat.pre_college_stage_id = e.id) OR
                 (e.type = 'college' AND cat.college_stage_id = e.id)))
        """,
        "from": LEVELS_FROM,
        "base": [],
        "direct": {"educational_levels": "e"},
        "exists": {
            "categories": "EXISTS (SELECT 1 FROM categories cat WHERE cat.is_active AND ((e.type = 'pre_college' AND cat.pre_college_stage_id = e.id) OR (e.type = 'college' AND cat.college_stage_id = e.id)) AND {cond})",
            "courses": "EXISTS (SELECT 1 FROM categories cat JOIN courses c ON c.category_id = cat.id WHERE cat.is_active AND c.status = 'published' AND ((e.type = 'pre_college' AND cat.pre_college_stage_id = e.id) OR (e.type = 'college' AND cat.college_stage_id = e.id)) AND {cond})",
            "books": "EXISTS (SELECT 1 FROM categories cat JOIN books b ON b.category_id = cat.id WHERE cat.is_active AND b.status = 'published' AND ((e.type = 'pre_college' AND cat.pre_college_stage_id = e.id) OR (e.type = 'college' AND cat.college_stage_id = e.id)) AND {cond})",
            "users": "EXISTS (SELECT 1 FROM categories cat JOIN (SELECT doctor_id, category_id FROM courses WHERE status = 'published' UNION ALL SELECT doctor_id, category_id FROM books WHERE status = 'published') item ON item.category_id = cat.id JOIN users u ON u.id = item.doctor_id AND u.role = 'doctor' WHERE cat.is_active AND ((e.type = 'pre_college' AND cat.pre_college_stage_id = e.id) OR (e.type = 'college' AND cat.college_stage_id = e.id)) AND {cond})",
        },
        "text": "(e.name_en ILIKE '%%' || %s || '%%' OR e.name_ar ILIKE '%%' || %s || '%%' OR e.group_name ILIKE '%%' || %s || '%%')",
        "order": {"relevance": "e.type, e.display_order, e.year_number, e.id",
                  "newest": "e.type, e.id DESC", "position": "e.type, e.display_order, e.year_number, e.id"},
    },
}

# Columns that are not text. Folding a smallint is a type error, and a year the
# model wrote as "2026" has to fail loudly rather than match nothing quietly.
NUMERIC = {("courses", "academic_year"), ("educational_levels", "year_number")}
TEMPORAL = set()


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
        alias = {"users": "u", "courses": "c", "books": "b",
                 "categories": "cat", "educational_levels": "e"}[table]
        wrapper = spec["exists"][table]

    col = f"{alias}.{column}"
    key = (table, column)

    if key in NUMERIC:

        value = item["value"]

        if not value.lstrip("-").isdigit():
            return None, f"{column} needs a number, got {value!r}"

        compare = {"eq": "=", "gte": ">=", "lte": "<="}.get(op)
        if compare is None:
            return None, f"{op} is not supported on a number"
        sql, params = f"{col} {compare} %s::smallint", [int(value)]

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

    where = list(spec.get("base", []))
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
        metadata_search = spec["text"]
        where.append(metadata_search)
        params.extend([text] * metadata_search.count("%s"))

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


def _level(values):
    level_type, level_id, name_en, name_ar, group_name, year_number = values
    if level_id is None:
        return None
    return {
        "kind": "educational_level", "type": level_type, "id": level_id,
        "name_en": name_en, "name_ar": name_ar, "group_name": group_name,
        "year_number": year_number,
    }


def _category(values):
    category_id, name_en, name_ar, slug = values
    if category_id is None:
        return None
    return {"id": category_id, "name_en": name_en, "name_ar": name_ar, "slug": slug}


def _course(row):
    (course_id, title, slug, subtitle, description, year, language, course_level,
     price, published_at, doctor_id, doctor, category_id, category_en, category_ar,
     category_slug, *level) = row

    return {
        "kind": "course",
        "id": course_id,
        "title": title,
        "slug": slug,
        "subtitle": subtitle,
        "description": description,
        "academic_year": year,
        "language": language,
        "course_level": course_level,
        "price": price,
        "published_at": published_at,
        "doctor": _person(doctor_id, doctor),
        "category": _category((category_id, category_en, category_ar, category_slug)),
        "educational_level": _level(level),
        "url": None,
        "url_opens": "no public course route is defined in this API",
    }


def _book(row):
    (book_id, title, slug, subtitle, description, language, price, page_count,
     published_at, doctor_id, doctor, category_id, category_en, category_ar,
     category_slug, *level) = row

    return {
        "kind": "book",
        "id": book_id,
        "title": title,
        "slug": slug,
        "subtitle": subtitle,
        "description": description,
        "language": language,
        "price": price,
        "page_count": page_count,
        "published_at": published_at,
        "doctor": _person(doctor_id, doctor),
        "category": _category((category_id, category_en, category_ar, category_slug)),
        "educational_level": _level(level),
        "url": None,
        "url_opens": "no public book route is defined in this API",
    }


def _category_row(row):
    (category_id, name_en, name_ar, slug, parent_id, *rest) = row
    level, courses, books = rest[:6], rest[6], rest[7]

    return {
        "kind": "category",
        "id": category_id,
        "name": name_ar or name_en,
        "name_en": name_en,
        "name_ar": name_ar,
        "slug": slug,
        "parent_id": parent_id,
        "educational_level": _level(level),
        "courses": courses,
        "books": books,
        "url": None,
        "url_opens": "no public category route is defined in this API",
    }


def _user(row):
    user_id, name, courses, books = row

    return {
        "kind": "doctor",
        "id": user_id,
        "name": name,
        "courses": courses,
        "books": books,
        "url": None,
        "url_opens": "no public doctor route is defined in this API",
    }


def _educational_level(row):
    level_type, level_id, name_en, name_ar, group_name, year, categories, courses, books = row
    return {
        **_level((level_type, level_id, name_en, name_ar, group_name, year)),
        "name": name_ar or name_en,
        "categories": categories,
        "courses": courses,
        "books": books,
        "url": None,
        "url_opens": "no public educational-level route is defined in this API",
    }


ROW = {
    "courses": _course, "users": _user, "books": _book,
    "categories": _category_row, "educational_levels": _educational_level,
}


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def search(envelope, conn=None):
    """Run a stage-one envelope (or a bare plan) and return what to show."""

    plan = envelope.get("plan", envelope) if isinstance(envelope, dict) else envelope
    query = envelope.get("query", "") if isinstance(envelope, dict) else ""
    validation_drops = envelope.get("dropped", []) if isinstance(envelope, dict) else []

    if plan is None:
        return _out(query, "error", notes=[envelope.get("error", "no plan")])

    if plan["intent"] == "clarify":
        return _out(query, "clarify", plan=plan, clarify=plan.get("clarify", ""),
                    missing=plan.get("missing", []), dropped=validation_drops)

    if plan["intent"] == "unsupported":
        return _out(query, "unsupported", plan=plan, reason=plan.get("reason", ""),
                    dropped=validation_drops)

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
        dropped=validation_drops + dropped,
        notes=notes,
        sql=sql,
        params=[list(p) if isinstance(p, list) else p for p in params],
    )


def diagnose(plan, conn=None):
    """Which filter emptied the result.

    "Nothing found" is not an answer anyone can act on. Every filter is re-run
    on its own, so the reply can say *which* condition matched nothing — a
    misspelled doctor, an empty category, a year with no courses. The
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
