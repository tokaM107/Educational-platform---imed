"""Test process configuration required before application modules are imported."""

import os
from pathlib import Path

import pytest


os.environ.setdefault(
    "NEST_JWT_ACCESS_SECRET",
    "nest-test-access-secret-at-least-32-characters-and-long-enough",
)
os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")
os.environ.setdefault("SUPABASE_PUBLISHABLE_KEY", "test-publishable-key")


TRANSCRIPTION_JOBS_DDL = (
    Path(__file__).resolve().parent.parent
    / "db" / "proposals" / "20260905_transcription_jobs.sql"
)


@pytest.fixture
def jobs_db():
    """A real PostgreSQL with an empty transcription_jobs table, or skip.

    Set TEST_DATABASE_URL to run the tests that use this. They exist because
    FakeConn cannot catch what mocks cannot see: a statement PostgreSQL rejects,
    a WHERE that matches more rows than intended, a CHECK constraint that
    refuses a status. The table is built from the migration itself rather than
    a hand-written approximation, so those constraints are the real ones.

    Function-scoped and rebuilt per test: these assert on which rows changed,
    and rows surviving between tests would make that meaningless.
    """

    url = os.getenv("TEST_DATABASE_URL")

    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")

    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(url, autocommit=True) as conn:

        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS transcription_jobs CASCADE")
            cur.execute(TRANSCRIPTION_JOBS_DDL.read_text(encoding="utf-8"))

        # The service commits for itself, so the connection it is handed must
        # not also be in autocommit — that is how it runs in production.
        conn.autocommit = False

        yield conn

        conn.rollback()
        conn.autocommit = True

        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS transcription_jobs CASCADE")
