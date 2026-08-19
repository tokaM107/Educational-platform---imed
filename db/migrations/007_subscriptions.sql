-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/007_subscriptions.sql

-- A student's paid access to one teacher's material.
--
-- Enrolment already says which course a student is taking; it does not say
-- whether they are entitled to it. Those are different facts and they change
-- independently: a subscription lapses without un-enrolling anybody, and a
-- student subscribed to a teacher may be enrolled on none, one or several of
-- that teacher's courses. Hence a separate many-to-many between student and
-- doctor rather than a flag on enrollments.
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    doctor_id INTEGER NOT NULL REFERENCES users(id),
    subscribed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, doctor_id)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_student ON subscriptions(student_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_doctor ON subscriptions(doctor_id);
