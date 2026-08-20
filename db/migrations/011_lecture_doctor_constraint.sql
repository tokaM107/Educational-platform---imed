-- Make the drift impossible instead of repairing it again.
--
-- Migration 010 realigned eight lectures whose doctor had come apart from their
-- course's doctor. That fixed the rows; it did not fix the hole. Two writers
-- could still reopen it:
--
--   scripts/seed_test_data.py  moves a course to a different doctor
--   scripts/enroll.py assign   attaches an existing lecture to a course
--
-- and neither one is wrong on its own — each is doing exactly what it was
-- asked. The invariant lives between them, which is why it belongs in the
-- schema rather than in every writer that touches either column.
--
-- A composite foreign key expresses it directly: the (course, doctor) pair on a
-- lecture must be a pair that exists on the course. Preferred over a trigger
-- because it is declarative — it shows up in \d, it cannot be skipped by a bulk
-- load, and nobody has to remember it.
--
-- ON UPDATE CASCADE is the half that actually retires the bug. Moving a course
-- to another doctor now moves that course's lectures with it, in the same
-- statement, which is precisely what the seed failed to do by hand.
--
-- The composite key needs its own unique constraint on the parent side, so
-- courses gains UNIQUE (id, doctor_id). It is redundant against the primary key
-- for uniqueness purposes — id alone is already unique — and exists only to give
-- the foreign key something to point at.
--
-- A lecture with no course is untouched: MATCH SIMPLE skips the check when any
-- column of the key is NULL, so a standalone lecture keeps whoever recorded it.
--
-- What this rules out: a lecture inside a course being credited to anyone other
-- than that course's doctor. If a course ever needs a guest lecturer, that is a
-- `lecture_teachers` table, not a column allowed to drift away from its parent.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'courses_id_doctor_key'
    ) THEN
        ALTER TABLE courses
            ADD CONSTRAINT courses_id_doctor_key UNIQUE (id, doctor_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'lectures_course_doctor_fkey'
    ) THEN
        ALTER TABLE lectures
            ADD CONSTRAINT lectures_course_doctor_fkey
            FOREIGN KEY (course_id, doctor_id)
            REFERENCES courses (id, doctor_id)
            ON UPDATE CASCADE;
    END IF;
END $$;

COMMIT;
