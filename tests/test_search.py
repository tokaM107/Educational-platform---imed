"""Stage two of the search assistant: plan in, rows and a link out.

Two kinds of test here, deliberately separated.

The first kind builds SQL and never runs it. Those are the tests that matter for
safety — that a filter naming a table the target cannot reach is refused, and
that every value leaves as a bound parameter. They need nothing but the module.

The second kind runs against the real database and is skipped when it is not
up. Those assert against the catalog as it is seeded, because "the query is
syntactically fine" and "the query finds the lecture" are different claims and
only the second one is the product.
"""

import os
import sys
from contextlib import contextmanager

import psycopg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "search-assistant"))

import search  # noqa: E402


def plan(**over):
    """A minimal valid plan, overridden per test."""

    base = {
        "intent": "search", "target": "lectures", "filters": [], "text": "",
        "sort": "relevance", "limit": 20, "missing": [], "clarify": "",
        "reason": "", "confidence": 1.0,
    }
    base.update(over)
    return base


def filt(table, column, op="ilike", value="", values=None, means=""):
    return {"table": table, "column": column, "op": op, "value": value,
            "values": values or [], "means": means}


# ---------------------------------------------------------------------------
# Building the query
# ---------------------------------------------------------------------------


def test_every_value_leaves_as_a_bound_parameter():
    """The one property that keeps a model's output from being executable."""

    sql, params, _ = search.build(
        plan(filters=[filt("users", "name", value="'; DROP TABLE users; --")])
    )

    assert "DROP TABLE" not in sql
    assert "'; DROP TABLE users; --" in params
    assert sql.count("%s") == len(params)


def test_matching_is_plain_sql_with_no_normalisation_layer():
    """The catalog is English, so ILIKE is the whole matching story.

    A character table here would be a second, worse language model living in
    the backend — and the one place it is genuinely needed, the transcript, is
    going to the semantic search instead.
    """

    sql, _, _ = search.build(plan(filters=[filt("users", "name", value="Ahmed")]))

    assert "translate" not in sql
    assert "ILIKE '%%' || %s || '%%'" in sql


def test_an_exact_match_ignores_case_on_both_sides():

    sql, _, _ = search.build(
        plan(target="subjects",
             filters=[filt("subjects", "name", op="eq", value="physiology")])
    )

    assert sql.count("lower(") == 2


def test_a_filter_the_target_cannot_reach_is_dropped_not_guessed():

    _, _, dropped = search.build(
        plan(target="subjects", filters=[filt("lectures", "title", value="قلب"),
                                         filt("video_events", "event_type", value="play")])
    )

    assert len(dropped) == 1
    assert dropped[0]["filter"]["table"] == "video_events"


def test_a_fan_out_filter_becomes_exists_not_a_join():
    """A join would return the course once per matching lecture."""

    sql, _, _ = search.build(
        plan(target="courses", filters=[filt("lectures", "title", value="قلب")])
    )

    assert "EXISTS (SELECT 1 FROM lectures l" in sql
    assert "JOIN lectures" not in sql


def test_a_year_that_is_not_a_number_is_refused():
    """A calendar year is the realistic mistake; it must not match silently."""

    _, _, dropped = search.build(
        plan(filters=[filt("courses", "academic_year", op="eq", value="two")])
    )

    assert dropped and "needs a number" in dropped[0]["why"]

    sql, params, dropped = search.build(
        plan(filters=[filt("courses", "academic_year", op="eq", value="2")])
    )

    assert not dropped and params[0] == 2
    assert "translate" not in sql.split("ORDER BY")[0]


def test_content_words_search_the_transcript_not_only_the_title():
    """"the lecture where he explained X" is never a title."""

    sql, params, _ = search.build(plan(text="الدورة الدموية"))

    assert "transcript_chunks" in sql
    assert params.count("الدورة الدموية") == 2  # title and transcript


def test_an_in_filter_folds_every_element_of_the_list():

    sql, params, _ = search.build(
        plan(target="subjects",
             filters=[filt("subjects", "name", op="in", values=["Anatomy", "Histology"])])
    )

    assert "unnest(%s::text[])" in sql
    assert params[0] == ["Anatomy", "Histology"]


def test_sort_newest_orders_by_date_not_by_id():

    sql, _, _ = search.build(plan(sort="newest"))

    assert "l.created_at DESC" in sql


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def test_a_clarify_plan_never_reaches_the_database():
    """Passing conn=None would raise if it tried to connect."""

    out = search.search({"query": "عايز محاضرات",
                         "plan": plan(intent="clarify", target="",
                                      clarify="مادة إيه؟", missing=["subject"])})

    assert out["outcome"] == "clarify"
    assert out["clarify"] == "مادة إيه؟"
    assert out["url"] is None


def test_an_unsupported_plan_carries_the_reason_through():

    out = search.search({"query": "عايز كتاب",
                         "plan": plan(intent="unsupported", target="",
                                      reason="المنصة مفيهاش كتب")})

    assert out["outcome"] == "unsupported"
    assert out["reason"] == "المنصة مفيهاش كتب"


def test_a_failed_extraction_becomes_an_error_not_a_crash():

    out = search.search({"query": "x", "plan": None, "error": "model unreachable"})

    assert out["ok"] is False
    assert out["outcome"] == "error"
    assert "model unreachable" in out["notes"]


def test_one_filter_gets_no_diagnostic():
    """With a single condition there is nothing to attribute the miss to."""

    assert search.diagnose(plan(filters=[filt("users", "name", value="x")])) == []


# ---------------------------------------------------------------------------
# Against the real catalog
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def conn():

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    try:
        from app.db import connection

        with connection() as live:
            yield live

    except Exception as error:  # no database, no credentials, not running
        pytest.skip(f"database not reachable: {error}")


def run(conn, **over):
    return search.search({"query": "", "plan": plan(**over)}, conn=conn)


def test_a_lowercase_name_finds_the_doctor(conn):
    """The model writes what the student said; the row is Title Case."""

    out = run(conn, target="users",
              filters=[filt("users", "role", op="eq", value="doctor"),
                       filt("users", "name", value="ahmed hassan")])

    assert out["outcome"] == "go"
    assert out["results"][0]["name"] == "Ahmed Hassan"


def test_a_doctor_and_a_subject_together_narrow_the_result(conn):

    both = run(conn, filters=[filt("users", "name", value="Ahmed Hassan"),
                              filt("subjects", "name", op="eq", value="Physiology")])

    subject_only = run(conn, filters=[filt("subjects", "name", op="eq",
                                           value="Physiology")])

    assert both["total"] < subject_only["total"]
    assert every(both, lambda r: r["subject"] == "Physiology")
    assert every(both, lambda r: r["doctor"]["name"] == "Ahmed Hassan")


def test_the_academic_year_filter_is_the_study_year(conn):

    out = run(conn, filters=[filt("courses", "academic_year", op="eq", value="2")])

    assert out["total"] > 0
    assert every(out, lambda r: r["course"]["academic_year"] == 2)


def test_a_transcript_word_finds_the_lecture_that_says_it(conn):
    """Lecture 1 is the only one with a real transcript."""

    out = run(conn, text="العظام")

    assert out["outcome"] == "go"
    assert out["results"][0]["id"] == 1
    assert out["url"] == "/?lecture_id=1"


def test_a_course_links_to_its_first_lecture_and_says_so(conn):
    """There is no course page; the link must not pretend there is."""

    out = run(conn, target="courses",
              filters=[filt("courses", "title", value="Biochemistry")])

    assert out["outcome"] == "go"
    row = out["results"][0]
    assert row["url"] == "/?lecture_id=66"
    assert "no course page" in row["url_opens"]


def test_several_matches_ask_rather_than_pick(conn):
    """Catalog ambiguity is settled here, after the query, never before it."""

    out = run(conn, target="users",
              filters=[filt("users", "role", op="eq", value="doctor"),
                       filt("users", "name", value="Ahmed")])

    assert out["outcome"] == "choose"
    assert out["total"] == 3
    assert out["url"] is None


def test_nothing_found_says_which_filter_emptied_it(conn):
    """"Nothing found" is useless; "this filter is the one that missed" is not."""

    out = run(conn, filters=[filt("users", "name", value="Zaghloul"),
                             filt("subjects", "name", op="eq", value="Physiology")])

    assert out["outcome"] == "none"
    assert any("users.name" in note and "matches 0" in note for note in out["notes"])
    assert any("subjects.name" in note and "matches 13" in note for note in out["notes"])


def test_the_limit_is_honoured(conn):

    out = run(conn, limit=3, filters=[filt("subjects", "name", op="eq",
                                           value="Physiology")])

    assert out["total"] == 3


def test_a_lecture_row_carries_everything_the_result_list_shows(conn):

    out = run(conn, filters=[filt("lectures", "title", value="Cardiac Cycle")])

    row = out["results"][0]

    assert row["title"] == "[TEST] The Cardiac Cycle"
    assert row["doctor"]["name"] == "Ahmed Hassan"  # the doctor of its course
    assert row["course"]["title"] == "[TEST] Physiology 1"
    assert row["module"]["title"] == "Cardiovascular"
    assert row["subject"] == "Physiology"
    assert row["url"] == f"/?lecture_id={row['id']}"


def every(out, check):
    return all(check(row) for row in out["results"])


def test_every_lecture_is_taught_by_its_own_course_doctor(conn):
    """The search reads a lecture's doctor from lectures.doctor_id.

    When that drifted from the course, a doctor's own lectures became
    unreachable by her name — see db/migrations/010.
    """

    drifted = conn.execute(
        """
        SELECT count(*) FROM lectures l JOIN courses c ON c.id = l.course_id
        WHERE l.doctor_id <> c.doctor_id
        """
    ).fetchone()[0]

    assert drifted == 0


@contextmanager
def rolled_back(conn):
    """A transaction that never lands, so these can write to the real catalog.

    Raising Rollback inside the block is how psycopg unwinds a transaction
    without an error: the innermost block recognises the sentinel, rolls back,
    and swallows it. Bare, with no argument — `conn.transaction()` hands back a
    contextmanager wrapper rather than the Transaction itself, so passing it in
    names something the block does not recognise as its own.
    """

    with conn.transaction():
        yield
        raise psycopg.Rollback()


def test_the_database_refuses_to_credit_a_lecture_to_the_wrong_doctor(conn):
    """The repair in migration 010 held; this is what stops it recurring.

    A count of zero only says nobody has broken it *yet*. This says they can't.
    """

    course, other_doctor = conn.execute(
        """
        SELECT l.course_id, u.id
        FROM lectures l, users u
        WHERE l.course_id IS NOT NULL AND u.role = 'doctor'
          AND u.id <> l.doctor_id
        LIMIT 1
        """
    ).fetchone()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with rolled_back(conn):
            conn.execute(
                "UPDATE lectures SET doctor_id = %s WHERE course_id = %s",
                (other_doctor, course),
            )


def test_attaching_a_lecture_to_another_doctors_course_is_refused(conn):
    """The scripts/enroll.py path — set course_id, forget doctor_id."""

    lecture, elsewhere = conn.execute(
        """
        SELECT l.id, c.id
        FROM lectures l, courses c
        WHERE l.course_id IS NOT NULL AND c.doctor_id <> l.doctor_id
        LIMIT 1
        """
    ).fetchone()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with rolled_back(conn):
            conn.execute(
                "UPDATE lectures SET course_id = %s WHERE id = %s",
                (elsewhere, lecture),
            )


def test_moving_a_course_to_another_doctor_carries_its_lectures(conn):
    """ON UPDATE CASCADE: the half that retires the bug rather than blocking it.

    This is the exact thing the seed did by hand and got wrong.
    """

    course, doctor = conn.execute(
        """
        SELECT c.id, c.doctor_id FROM courses c
        WHERE EXISTS (SELECT 1 FROM lectures l WHERE l.course_id = c.id)
        LIMIT 1
        """
    ).fetchone()

    replacement = conn.execute(
        "SELECT id FROM users WHERE role = 'doctor' AND id <> %s LIMIT 1", (doctor,)
    ).fetchone()[0]

    with rolled_back(conn):

        conn.execute(
            "UPDATE courses SET doctor_id = %s WHERE id = %s", (replacement, course)
        )

        stale = conn.execute(
            "SELECT count(*) FROM lectures WHERE course_id = %s AND doctor_id <> %s",
            (course, replacement),
        ).fetchone()[0]

        assert stale == 0

    # and the rollback really rolled back
    assert conn.execute(
        "SELECT doctor_id FROM courses WHERE id = %s", (course,)
    ).fetchone()[0] == doctor


def test_a_standalone_lecture_still_keeps_its_own_doctor(conn):
    """MATCH SIMPLE: no course, no check. A lecture can exist before a course."""

    doctor = conn.execute(
        "SELECT id FROM users WHERE role = 'doctor' LIMIT 1"
    ).fetchone()[0]

    with rolled_back(conn):
        conn.execute(
            "INSERT INTO lectures (doctor_id, course_id, title) VALUES (%s, NULL, %s)",
            (doctor, "standalone"),
        )
