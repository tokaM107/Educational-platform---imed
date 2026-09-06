"""The SQL this service sends must be SQL PostgreSQL will accept.

Written after `python -m rag.worker --once` died in production on

    psycopg.errors.SyntaxError: syntax error at or near "$3"
    LINE 7: WHERE status IN $3

`WHERE status IN %(in_flight)s` with a tuple parameter. Under psycopg2 that was
rewritten client-side into `IN ('submitted', 'processing')`; psycopg3 sends
parameters to the server to be bound there, so the tuple became one placeholder
in a position PostgreSQL has no grammar for, and the statement failed before a
row was read. The fix is `= ANY(%(in_flight)s)` with a list.

Every test in this file is here because the existing suite could not have
caught that. FakeConn records the SQL it is handed and returns whatever the
test says; it never parses anything, so a query that PostgreSQL rejects outright
passes a mocked test perfectly. These three layers close that gap, strongest
first, and each skips rather than fails when its tooling is absent:

    a real PostgreSQL   TEST_DATABASE_URL — executes the statements
    libpg_query         pglast — PostgreSQL's own parser, no server needed
    a text guard        always runs, and catches the pattern coming back
"""

import ast
import os
import re
from pathlib import Path

import pytest

from app.services import transcription_jobs


ROOT = Path(__file__).resolve().parent.parent

DDL = ROOT / "db" / "proposals" / "20260905_transcription_jobs.sql"


# The queries whose parameters are collections, which is where this goes wrong.
PARAMETERISED = {
    "CLAIM_SQL": {
        "pending": transcription_jobs.PENDING,
        "submitted": transcription_jobs.SUBMITTED,
        "failed": transcription_jobs.FAILED,
    },
    "RECOVER_STALE_SQL": {
        "failed": transcription_jobs.FAILED,
        "in_flight": transcription_jobs.IN_FLIGHT_LIST,
        "stale_minutes": 30,
    },
    "IN_FLIGHT_SQL": {
        "in_flight": transcription_jobs.IN_FLIGHT_LIST,
        "limit": 50,
    },
}


# -------------------------
# Layer 1: a real database
# -------------------------


@pytest.fixture(scope="module")
def db():
    """A connection to a throwaway PostgreSQL, or skip.

    Set TEST_DATABASE_URL to run these. They create the transcription_jobs
    table from the migration itself, so what is exercised is the production
    schema rather than an approximation of it.
    """

    url = os.getenv("TEST_DATABASE_URL")

    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")

    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(url, autocommit=True) as conn:

        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS transcription_jobs CASCADE")
            cur.execute(DDL.read_text(encoding="utf-8"))

        yield conn

        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS transcription_jobs CASCADE")


def _insert(conn, guid, status, minutes_ago=0, runpod_job_id=None):

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO transcription_jobs
                (bunny_guid, video_id, status, runpod_job_id, attempt_count,
                 max_attempts, updated_at, submitted_at)
            VALUES (%s, 1, %s, %s, 1, 3,
                    now() - make_interval(mins => %s),
                    now() - make_interval(mins => %s))
            RETURNING id
            """,
            (guid, status, runpod_job_id, minutes_ago, minutes_ago),
        )
        return cur.fetchone()[0]


def test_recover_stale_runs_against_postgres(db):
    """The statement that failed in production, executed as production runs it."""

    _insert(db, "stale-guid", transcription_jobs.SUBMITTED,
            minutes_ago=999, runpod_job_id="runpod-1")

    released = transcription_jobs.recover_stale(db)

    assert [job["bunny_guid"] for job in released] == ["stale-guid"]


def test_recover_stale_leaves_a_job_that_is_still_being_watched(db):
    """Lifecycle unchanged: only jobs past the window are reclaimed."""

    _insert(db, "fresh-guid", transcription_jobs.PROCESSING,
            minutes_ago=0, runpod_job_id="runpod-2")

    released = transcription_jobs.recover_stale(db)

    assert "fresh-guid" not in [job["bunny_guid"] for job in released]


def test_in_flight_runs_against_postgres(db):
    """The other query that bound the same tuple."""

    _insert(db, "flying-guid", transcription_jobs.SUBMITTED,
            minutes_ago=1, runpod_job_id="runpod-3")

    guids = [job["bunny_guid"] for job in transcription_jobs.in_flight(db)]

    assert "flying-guid" in guids


def test_a_job_with_no_runpod_id_is_not_in_flight(db):
    """Claimed but never submitted: nothing to poll RunPod about."""

    _insert(db, "unsubmitted-guid", transcription_jobs.SUBMITTED, minutes_ago=1)

    guids = [job["bunny_guid"] for job in transcription_jobs.in_flight(db)]

    assert "unsubmitted-guid" not in guids


def test_claiming_runs_against_postgres(db):
    """The claim query binds three scalars, but shares the status vocabulary."""

    _insert(db, "claimable-guid", transcription_jobs.PENDING)

    claimed = transcription_jobs.claim_for_submission(db)

    assert claimed is not None and claimed["bunny_guid"] == "claimable-guid"


# ----------------------------------
# Layer 2: PostgreSQL's own parser
# ----------------------------------


def _as_postgres_sees_it(sql):
    """Render psycopg placeholders as the $n form psycopg3 sends to the server."""

    names = {}

    def named(match):
        names.setdefault(match.group(1), len(names) + 1)
        return f"${names[match.group(1)]}"

    sql = re.sub(r"%\((\w+)\)s", named, sql)

    counter = [len(names)]

    def positional(_):
        counter[0] += 1
        return f"${counter[0]}"

    return re.sub(r"%s", positional, sql)


@pytest.mark.parametrize("name", sorted(PARAMETERISED))
def test_the_queries_parse_as_postgresql(name):
    """libpg_query is PostgreSQL's grammar, so this is the real check.

    `IN $3` is rejected here exactly as the server rejected it, and no database
    has to be running for the test to say so.
    """

    pglast = pytest.importorskip("pglast")

    pglast.parse_sql(_as_postgres_sees_it(getattr(transcription_jobs, name)))


def test_the_broken_shape_would_still_be_caught():
    """Guard the guard: the parser must actually reject what production hit."""

    pglast = pytest.importorskip("pglast")

    broken = "UPDATE transcription_jobs SET status = %(failed)s " \
             "WHERE status IN %(in_flight)s"

    with pytest.raises(Exception) as caught:
        pglast.parse_sql(_as_postgres_sees_it(broken))

    assert "syntax error" in str(caught.value)


# ------------------------------------------
# Layer 3: the pattern must not come back
# ------------------------------------------


# `IN %s` / `IN %(name)s`: a collection parameter in a position that only works
# under client-side binding. `IN (%s)` is a different thing — one scalar — and
# is left alone.
FORBIDDEN = re.compile(r"\bIN\s+%(?:s\b|\(\w+\)s)", re.IGNORECASE)


def _sql_literals(path):
    """Every string constant in one file that looks like SQL."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if FORBIDDEN.search(node.value):
                yield node.lineno, " ".join(node.value.split())


def _project_files():

    for path in sorted(ROOT.rglob("*.py")):
        parts = path.relative_to(ROOT).parts
        if ".venv" in parts or "__pycache__" in parts:
            continue
        yield path


def test_no_query_in_the_project_binds_a_collection_with_in():
    """The regression guard, and the one test here that needs no tooling."""

    offenders = [
        f"{path.relative_to(ROOT)}:{line}  {text[:90]}"
        for path in _project_files()
        for line, text in _sql_literals(path)
        # This file names the broken shape on purpose, to test for it.
        if path.name != "test_sql_placeholders.py"
    ]

    assert not offenders, (
        "psycopg3 binds parameters server-side, so a collection in an IN "
        "becomes one placeholder and PostgreSQL rejects the statement. Use "
        "`= ANY(%s)` with a list:\n  " + "\n  ".join(offenders)
    )
