-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/004_report_narratives.sql

-- One stored narrative per student, course and week.
--
-- The measured half of a weekly report is cheap (a replay of that week's
-- events, ~25 ms) and is always recomputed. The written half costs a model call
-- of half a minute, and storing it buys two things that matter more than the
-- latency:
--
--   * a weekly report is a document. If it were regenerated on every page load
--     the student would read different advice each time they opened the same
--     week, which makes it impossible to refer to or act on.
--   * once a week is over its numbers never change again, so regenerating is
--     pure waste.
--
-- `fingerprint` is a hash of the exact figures the narrative was written from,
-- the same trick `query_embeddings` uses for questions: while the student keeps
-- watching, the fingerprint moves and the narrative is rewritten; once the week
-- closes it settles and the document is final.
CREATE TABLE IF NOT EXISTS report_narratives (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    week_start DATE NOT NULL,
    fingerprint CHAR(64) NOT NULL,
    narrative JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, course_id, week_start)
);

CREATE INDEX IF NOT EXISTS idx_report_narratives_week
    ON report_narratives (week_start);
