"""Idempotent, versioned weekly analytics document storage."""

import hashlib
import json

from psycopg.types.json import Jsonb


REPORT_VERSION = "learning-analytics-v1"


def _json(payload):
    return json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True)


def put(conn, payload):
    measured = dict(payload)
    measured.pop("narrative", None)
    measured.pop("notice", None)
    source = dict(measured)
    source.pop("generated_at", None)
    encoded = _json(source)
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO weekly_analytics_snapshots (
                student_id, course_id, week_start, report_version,
                source_fingerprint, payload
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (student_id, course_id, week_start, report_version)
            DO UPDATE SET source_fingerprint = EXCLUDED.source_fingerprint,
                          payload = EXCLUDED.payload, generated_at = now()
            RETURNING id
            """,
            (
                payload["student"]["id"], payload["course"]["id"],
                payload["week"]["start"], REPORT_VERSION, fingerprint,
                Jsonb(measured, dumps=_json),
            ),
        )
        row = cur.fetchone()
    conn.commit()
    return row[0]
