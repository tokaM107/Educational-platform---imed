"""Atomic, cross-worker daily quotas for authenticated LLM API requests."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QuotaUsage:
    limit: int
    used: int
    retry_after: int

    @property
    def remaining(self):
        return max(0, self.limit - self.used)


class QuotaExceeded(Exception):
    def __init__(self, usage):
        super().__init__("Daily LLM question limit reached")
        self.usage = usage


def consume(conn, user_id, feature, *, limit=10, units=1):
    """Atomically reserve quota for one authenticated user.

    The database clock defines the UTC day so every API worker agrees on the
    same boundary. Attempts are charged before provider work starts, including
    failed provider calls, because otherwise repeated failures become a free
    abuse path.
    """

    if limit <= 0 or units <= 0 or units > limit:
        raise ValueError("quota limit and units must satisfy 0 < units <= limit")
    if not feature or len(feature) > 50:
        raise ValueError("quota feature must be between 1 and 50 characters")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO llm_daily_usage AS usage
                (user_id, usage_date, query_count, feature_counts)
            VALUES
                (
                    %s,
                    (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date,
                    %s,
                    jsonb_build_object(%s, %s)
                )
            ON CONFLICT (user_id, usage_date) DO UPDATE
            SET
                query_count = usage.query_count + EXCLUDED.query_count,
                feature_counts = jsonb_set(
                    usage.feature_counts,
                    ARRAY[%s],
                    to_jsonb(
                        COALESCE((usage.feature_counts ->> %s)::integer, 0) + %s
                    ),
                    true
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE usage.query_count + EXCLUDED.query_count <= %s
            RETURNING
                query_count,
                GREATEST(
                    1,
                    CEIL(EXTRACT(EPOCH FROM (
                        ((usage_date + 1)::timestamp AT TIME ZONE 'UTC')
                        - CURRENT_TIMESTAMP
                    )))::integer
                ) AS retry_after
            """,
            (user_id, units, feature, units, feature, feature, units, limit),
        )
        row = cur.fetchone()

        if row is None:
            cur.execute(
                """
                SELECT
                    query_count,
                    GREATEST(
                        1,
                        CEIL(EXTRACT(EPOCH FROM (
                            ((usage_date + 1)::timestamp AT TIME ZONE 'UTC')
                            - CURRENT_TIMESTAMP
                        )))::integer
                    ) AS retry_after
                FROM llm_daily_usage
                WHERE user_id = %s
                  AND usage_date =
                      (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date
                """,
                (user_id,),
            )
            blocked = cur.fetchone()
            conn.commit()
            used, retry_after = blocked if blocked is not None else (limit, 1)
            raise QuotaExceeded(QuotaUsage(limit, used, retry_after))

    conn.commit()
    return QuotaUsage(limit, row[0], row[1])
