"""Search assistant catalog boundary and safe SQL construction."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "search-assistant"))

import extract_info  # noqa: E402
import search  # noqa: E402


def plan(**over):
    base = {
        "intent": "search", "target": "courses", "filters": [], "text": "",
        "sort": "relevance", "limit": 20, "missing": [], "clarify": "",
        "reason": "", "confidence": 1.0,
    }
    base.update(over)
    return base


def filt(table, column, op="ilike", value="", values=None, means=""):
    return {
        "table": table, "column": column, "op": op, "value": value,
        "values": values or [], "means": means,
    }


def test_only_requested_catalog_result_kinds_are_allowed():
    assert set(extract_info.TARGETS) == {
        "courses", "users", "books", "categories", "educational_levels",
    }
    assert set(search.TARGETS) == set(extract_info.TARGETS)
    assert "lectures" not in extract_info.SCHEMA
    assert "students" not in extract_info.TARGETS


def test_every_user_value_is_a_bound_parameter():
    attack = "'; DROP TABLE books; --"
    sql, params, dropped = search.build(
        plan(filters=[filt("courses", "title", value=attack)])
    )
    assert not dropped
    assert "DROP TABLE" not in sql
    assert attack in params
    assert sql.count("%s") == len(params)


@pytest.mark.parametrize(
    "target,visibility",
    [
        ("courses", "c.status = 'published'"),
        ("books", "b.status = 'published'"),
        ("categories", "cat.is_active"),
    ],
)
def test_visibility_is_enforced_by_backend_not_model(target, visibility):
    sql, _, _ = search.build(plan(target=target))
    assert visibility in sql


def test_doctor_results_can_never_include_students():
    sql, _, _ = search.build(plan(target="users", text="Ahmed"))
    assert "u.role = 'doctor'" in sql
    assert "u.name ILIKE" in sql


def test_course_and_book_queries_join_only_public_catalog_relations():
    course_sql, _, _ = search.build(plan(target="courses"))
    book_sql, _, _ = search.build(plan(target="books"))

    for sql in (course_sql, book_sql):
        assert "JOIN users" in sql
        assert "LEFT JOIN categories" in sql
        assert "pre_college_stages" in sql
        assert "college_stages" in sql
        assert "transcript_chunks" not in sql
        assert "enrollments" not in sql
        assert "book_entitlements" not in sql


def test_free_text_searches_metadata_not_lecture_content():
    sql, params, _ = search.build(plan(target="books", text="cardiology"))
    assert "b.title ILIKE" in sql
    assert "b.subtitle ILIKE" in sql
    assert "b.description ILIKE" in sql
    assert "transcript" not in sql
    assert params.count("cardiology") == 3


def test_educational_levels_unify_school_and_college_tables():
    sql, params, dropped = search.build(
        plan(
            target="educational_levels",
            filters=[filt("educational_levels", "year_number", op="eq", value="2")],
        )
    )
    assert not dropped
    assert "FROM pre_college_stages p WHERE p.is_active" in sql
    assert "FROM college_stages cs WHERE cs.is_active" in sql
    assert "UNION ALL" in sql
    assert "e.year_number = %s::smallint" in sql
    assert params[0] == 2


def test_numeric_range_filters_are_supported_and_invalid_numbers_are_dropped():
    sql, params, dropped = search.build(
        plan(filters=[filt("courses", "academic_year", op="gte", value="3")])
    )
    assert not dropped
    assert "c.academic_year >= %s::smallint" in sql
    assert params[0] == 3

    _, _, dropped = search.build(
        plan(filters=[filt("courses", "academic_year", op="eq", value="second")])
    )
    assert dropped and "needs a number" in dropped[0]["why"]


def test_unreachable_filter_is_dropped_instead_of_guessing_a_join():
    _, _, dropped = search.build(
        plan(target="books", filters=[filt("courses", "title", value="anatomy")])
    )
    assert dropped[0]["filter"]["table"] == "courses"


def test_doctors_can_be_filtered_through_published_catalog_associations():
    sql, params, dropped = search.build(
        plan(target="users", filters=[
            filt("categories", "name_en", value="medicine"),
            filt("educational_levels", "year_number", op="eq", value="2"),
        ])
    )
    assert not dropped
    assert sql.count("EXISTS") >= 2
    assert "item.doctor_id = u.id" in sql
    assert "status = 'published'" in sql
    assert params[:2] == ["medicine", 2]


def test_exact_and_in_text_filters_are_case_insensitive():
    sql, _, _ = search.build(
        plan(target="categories", filters=[
            filt("categories", "name_en", op="eq", value="medicine")
        ])
    )
    assert "lower(cat.name_en) = lower(%s)" in sql

    sql, params, _ = search.build(
        plan(target="educational_levels", filters=[
            filt("educational_levels", "type", op="in", values=["college", "pre_college"])
        ])
    )
    assert "unnest(%s::text[])" in sql
    assert params[0] == ["college", "pre_college"]


def test_newest_course_sort_uses_publication_time():
    sql, _, _ = search.build(plan(target="courses", sort="newest"))
    assert "c.published_at DESC NULLS LAST" in sql


def test_unknown_old_target_returns_an_error_without_querying():
    out = search.search({"query": "find a lecture", "plan": plan(target="lectures")})
    assert out["ok"] is False
    assert out["outcome"] == "error"
    assert "unknown target" in out["notes"][0]


def test_clarify_and_unsupported_plans_never_reach_database():
    clarify = search.search({
        "query": "عايز كورس", "plan": plan(intent="clarify", target="",
                                              clarify="كورس عن إيه؟")
    })
    unsupported = search.search({
        "query": "عايز محاضرة", "plan": plan(intent="unsupported", target="",
                                                reason="البحث لا يدعم المحاضرات")
    })
    assert clarify["outcome"] == "clarify"
    assert unsupported["outcome"] == "unsupported"


def test_course_result_contains_doctor_category_and_level():
    row = (
        7, "Anatomy", "anatomy", "Basics", "Description", 1, "en", "beginner",
        100, None, 3, "Ahmed Hassan", 4, "Medicine", "طب", "medicine",
        "college", 2, "First year", "السنة الأولى", "Medicine", 1,
    )
    result = search._course(row)
    assert result["kind"] == "course"
    assert result["id"] == 7
    assert result["doctor"] == {"id": 3, "name": "Ahmed Hassan"}
    assert result["category"]["name_ar"] == "طب"
    assert result["educational_level"]["type"] == "college"
    assert result["url"] is None


def test_book_result_contains_safe_catalog_metadata():
    row = (
        8, "Physiology Notes", "physiology-notes", None, "Description", "en",
        50, 120, None, 3, "Ahmed Hassan", 4, "Medicine", "طب", "medicine",
        "college", 2, "First year", "السنة الأولى", "Medicine", 1,
    )
    result = search._book(row)
    assert result["kind"] == "book"
    assert result["id"] == 8
    assert result["page_count"] == 120
    assert "pdf_storage_key" not in result


def test_educational_level_result_reports_catalog_counts():
    result = search._educational_level(
        ("pre_college", 5, "Secondary 2", "الصف الثاني الثانوي", "secondary", 2, 3, 8, 4)
    )
    assert result["kind"] == "educational_level"
    assert result["id"] == 5
    assert result["categories"] == 3
    assert result["courses"] == 8
    assert result["books"] == 4


def test_doctor_and_category_results_include_their_database_ids():
    doctor = search._user((12, "Mona", 2, 3))
    category = search._category_row(
        (9, "Medicine", "طب", "medicine", None,
         "college", 4, "Year 1", "السنة الأولى", "Medicine", 1, 5, 6)
    )

    assert doctor["id"] == 12
    assert category["id"] == 9
