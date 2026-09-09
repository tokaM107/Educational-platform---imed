"""Regression coverage for stale pooled PostgreSQL connections."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from app import db
from app.api import deps
from app.main import app
from app.services.security import VerifiedIdentity
from tests.fake_db import FakeConn


class DroppedConn(FakeConn):

    def cursor(self):
        raise OperationalError("SSL connection has been closed unexpectedly")


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    test_client.close()
    app.dependency_overrides.clear()


def test_pool_uses_checkout_validation_and_bounded_recycling(monkeypatch):
    captured = {}

    class FakePool:

        def __init__(self, **kwargs):
            captured.update(kwargs)

    settings = SimpleNamespace(
        require_database_url=lambda: "postgresql://db.example/app",
        db_pool_min_size=1,
        db_pool_max_size=10,
        db_pool_timeout_seconds=10,
        db_pool_max_lifetime_seconds=1800,
        db_pool_max_idle_seconds=300,
        db_pool_reconnect_timeout_seconds=60,
    )
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "ConnectionPool", FakePool)
    monkeypatch.setattr(db, "get_settings", lambda: settings)

    db.get_pool()

    assert captured["kwargs"] == {"sslmode": "require"}
    assert captured["min_size"] == 1
    assert captured["max_size"] == 10
    assert captured["timeout"] == 10
    assert captured["max_lifetime"] == 1800
    assert captured["max_idle"] == 300
    assert captured["reconnect_timeout"] == 60
    assert captured["check"] is db._check_connection
    assert captured["reconnect_failed"] is db._reconnect_failed
    app.dependency_overrides.clear()


def test_read_replaces_a_dropped_connection_and_retries_once(monkeypatch, caplog):
    dropped = DroppedConn()
    fresh = FakeConn(answer=lambda sql, params: [(7,)])
    available = iter((dropped, fresh))
    discarded = []

    @contextmanager
    def fake_connection():
        conn = next(available)
        try:
            yield conn
        except OperationalError:
            discarded.append(conn)
            raise

    monkeypatch.setattr(db, "connection", fake_connection)

    def read_id(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE id = %s", (7,))
            return cur.fetchone()[0]

    with caplog.at_level("WARNING"):
        assert db.run_read(read_id, operation_name="test lookup") == 7

    assert discarded == [dropped]
    assert "stale connection" in caplog.text


def test_read_stops_after_one_retry(monkeypatch):
    attempts = 0

    @contextmanager
    def always_dropped():
        nonlocal attempts
        attempts += 1
        yield DroppedConn()

    monkeypatch.setattr(db, "connection", always_dropped)

    with pytest.raises(OperationalError):
        db.run_read(lambda conn: conn.cursor(), operation_name="test lookup")

    assert attempts == 2


def test_pool_exhaustion_is_not_misclassified_or_retried(monkeypatch, caplog):
    attempts = 0

    @contextmanager
    def exhausted_pool():
        nonlocal attempts
        attempts += 1
        raise PoolTimeout("no connection available")
        yield

    monkeypatch.setattr(db, "connection", exhausted_pool)

    with caplog.at_level("ERROR"), pytest.raises(PoolTimeout):
        db.run_read(lambda conn: None, operation_name="test lookup")

    assert attempts == 1
    assert "pool exhaustion" in caplog.text
    assert "stale connection" not in caplog.text


def test_current_user_lookup_recovers_instead_of_returning_500(
    client, monkeypatch
):
    dropped = DroppedConn()
    fresh = FakeConn(
        answer=lambda sql, params: [
            (
                2,
                "Ahmed",
                "student@example.com",
                "student",
                "11111111-1111-1111-1111-111111111111",
            )
        ]
    )
    available = iter((dropped, fresh))

    @contextmanager
    def fake_connection():
        yield next(available)

    monkeypatch.setattr(db, "connection", fake_connection)
    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda token: VerifiedIdentity(
            source="supabase",
            subject="11111111-1111-1111-1111-111111111111",
        ),
    )

    response = client.get(
        "/api/auth/me", headers={"Authorization": "Bearer valid-token"}
    )

    assert response.status_code == 200
    assert response.json()["id"] == 2
