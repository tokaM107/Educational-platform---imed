from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "db" / "proposals" / "20260901_essay_grading_production.sql"
)


def test_production_essay_migration_contains_the_complete_audit_chain():
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "essay_question_versions",
        "essay_criteria",
        "essay_question_releases",
        "essay_submissions",
        "essay_grading_runs",
        "essay_criterion_results",
        "essay_grade_reviews",
    ):
        assert f"CREATE TABLE public.{table}" in sql
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in sql

    assert "'essay'" in sql
    assert "NUMERIC(20, 10)" in sql
    assert "status_factor = 0.5" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "prevent_essay_audit_mutation" in sql
    assert "DROP TABLE" not in sql.upper()
    assert sql.rstrip().endswith("COMMIT;")
