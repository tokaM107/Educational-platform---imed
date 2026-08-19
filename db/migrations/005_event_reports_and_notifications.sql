-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/005_event_reports_and_notifications.sql

-- Reports that a moment produced rather than a calendar.
--
-- A weekly report covers a rolling window, so its numbers are recomputed on
-- every read and only the narrative is cached. A completion report is the
-- opposite: "you finished the module" describes an instant, and if it were
-- recomputed a week later — after the student had gone back and rewatched
-- half of it — it would no longer describe the moment it was issued. So these
-- are frozen whole at the moment they fire.
CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('module', 'exam')),
    -- set for an exam report: the lecture whose questions were just finished
    lecture_id INTEGER REFERENCES lectures(id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One module report per student per course, one exam report per lecture. The
-- trigger fires on an event that can arrive twice (a student can replay the end
-- of a lecture), so uniqueness is enforced here rather than trusted upstream.
CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_once
    ON reports (student_id, course_id, kind, COALESCE(lecture_id, 0));

CREATE INDEX IF NOT EXISTS idx_reports_student ON reports (student_id, generated_at DESC);

-- How a report reaches the person it is about, and their teacher.
--
-- Deliberately a table and not a message queue: the site polls it, a read is a
-- timestamp, and nothing is lost if the browser was closed when the report was
-- written.
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    -- who sees it: the student, or the doctor who teaches the course
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind VARCHAR(30) NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE,
    -- who the report is about, which is not always the recipient
    student_id INTEGER REFERENCES users(id),
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The unread badge is the hottest query on this table.
CREATE INDEX IF NOT EXISTS idx_notifications_inbox
    ON notifications (user_id, read_at, created_at DESC);

-- One notification per recipient per report.
CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_once
    ON notifications (user_id, report_id)
    WHERE report_id IS NOT NULL;
