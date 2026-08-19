-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/003_courses_and_enrollments.sql

-- The weekly report has to say which course a student is on, who teaches it,
-- and how many of its lectures they were meant to watch. None of that is
-- derivable from `lectures.doctor_id` alone: "registered" has no meaning
-- without somewhere to record the registration. Hence the smallest thing that
-- carries it — a course, a lecture's place in one, and an enrolment.

CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY,
    doctor_id INTEGER NOT NULL REFERENCES users(id),
    title VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Nullable on purpose: lectures that predate courses stay valid, they just do
-- not appear in a course report.
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS course_id INTEGER REFERENCES courses(id);

-- One row per student per course. This is the denominator of
-- "watched 3 of the 5 lectures you are registered for".
CREATE TABLE IF NOT EXISTS enrollments (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, course_id)
);

CREATE INDEX IF NOT EXISTS idx_lectures_course ON lectures(course_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_student ON enrollments(student_id);

-- The report reads a week of one student's attempts and needs the question's
-- lecture and topic with them, so the lookup starts from the student and the
-- date.
CREATE INDEX IF NOT EXISTS idx_question_attempts_student_date
    ON question_attempts (student_id, answered_at);
