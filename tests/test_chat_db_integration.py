"""Opt-in Postgres checks against a database built from authoritative migrations."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import UniqueViolation


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def seeded():
    run_id = uuid4().hex
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (role, name, email)
                VALUES ('doctor', 'Integration Doctor', %s) RETURNING id
            """, (f"doctor-{run_id}@example.test",))
            doctor_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO users (role, name, email)
                VALUES ('student', 'Integration Student', %s) RETURNING id
            """, (f"student-{run_id}@example.test",))
            student_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO lectures (doctor_id, title)
                VALUES (%s, 'Integration Lecture') RETURNING id
            """, (doctor_id,))
            lecture_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO chat_sessions (student_id, lecture_id)
                VALUES (%s, %s) RETURNING id
            """, (student_id, lecture_id))
            session_id = cur.fetchone()[0]
        conn.commit()
    yield session_id
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM lectures WHERE id = %s", (lecture_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s, %s)",
                        (student_id, doctor_id))
        conn.commit()


def test_message_order_and_idempotency_are_database_enforced(seeded):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO chat_messages
                    (session_id, message_order, role, content, tokenizer_name,
                     idempotency_key)
                VALUES (%s, 1, 'user', 'Why?', 'test', 'retry-key')
            """, (seeded,))
        conn.commit()
        with pytest.raises(UniqueViolation):
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO chat_messages
                        (session_id, message_order, role, content, tokenizer_name,
                         idempotency_key)
                    VALUES (%s, 2, 'user', 'Why?', 'test', 'retry-key')
                """, (seeded,))
        conn.rollback()


def test_concurrent_order_reservations_do_not_collide(seeded):
    def reserve():
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))
                """, (str(seeded),))
                cur.execute("""
                    UPDATE chat_sessions
                    SET next_message_order = next_message_order + 2
                    WHERE id = %s
                    RETURNING next_message_order - 2
                """, (seeded,))
                value = cur.fetchone()[0]
            conn.commit()
            return value
    with ThreadPoolExecutor(max_workers=2) as pool:
        reserved = sorted(pool.map(lambda _: reserve(), range(2)))
    assert reserved == [1, 3]


def test_messages_load_in_message_order_not_timestamp_order(seeded):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO chat_messages
                    (session_id, message_order, role, content, tokenizer_name, created_at)
                VALUES (%s, 2, 'assistant', 'second', 'test', '2020-01-01'),
                       (%s, 1, 'user', 'first', 'test', '2021-01-01')
            """, (seeded, seeded))
            cur.execute("""
                SELECT content FROM chat_messages
                WHERE session_id = %s ORDER BY message_order
            """, (seeded,))
            assert [row[0] for row in cur.fetchall()] == ["first", "second"]
        conn.rollback()
