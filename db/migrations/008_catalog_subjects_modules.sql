-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/008_catalog_subjects_modules.sql
--
-- Catalog structure for conversational search. The hierarchy the student's
-- intent maps onto is:
--
--     doctor -> course (subject, academic_year) -> module -> lecture
--
-- Everything here is additive. No column is dropped or renamed, every new
-- column is nullable, and every existing row stays valid — which is why the
-- backfill is a separate, optional step rather than part of the migration.

-- 1. Subjects -------------------------------------------------------------
--
-- Normalised rather than a string on `courses`, so "every Physiology course"
-- is a join and not a LIKE over free text that a typo can hide a row from.
-- Nothing about a doctor lives here: a subject is taught by many doctors and
-- putting one inside the other is what makes a catalog un-queryable later.
CREATE TABLE IF NOT EXISTS subjects (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE
);

-- 2. Courses gain a subject and a year ------------------------------------
--
-- Both nullable, because courses already exist and have neither. Making them
-- NOT NULL would mean either rejecting those rows or inventing values for
-- them, and an invented academic year is worse than a missing one — the
-- search would filter on it and quietly return the wrong courses.
ALTER TABLE courses ADD COLUMN IF NOT EXISTS subject_id INTEGER REFERENCES subjects(id);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS academic_year SMALLINT;

-- The range is a typo guard more than a rule. The realistic data-entry error
-- is writing a calendar year — `academic_year = 2026` — which would silently
-- match nothing forever. Five years of MBBCh plus headroom rejects that while
-- accepting any plausible programme; widen it with one ALTER if a programme
-- needs more.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'courses'::regclass AND conname = 'courses_academic_year_check'
    ) THEN
        ALTER TABLE courses ADD CONSTRAINT courses_academic_year_check
            CHECK (academic_year IS NULL OR academic_year BETWEEN 1 AND 7);
    END IF;
END $$;

-- 3. Modules --------------------------------------------------------------
--
-- A module belongs to exactly one course, so it cascades with it: a module
-- without its course is not a thing anybody can navigate to.
--
-- `position` is the teaching order. A catalog listed in id order tells the
-- student nothing about which module comes first in the term.
CREATE TABLE IF NOT EXISTS modules (
    id SERIAL PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    position SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Two modules called "Cardiovascular" in one course is a data-entry
    -- mistake, not a real distinction.
    UNIQUE (course_id, title)
);

-- 4. Lectures gain an optional module -------------------------------------
--
-- `course_id` stays exactly as it is. Enrolment, subscriptions, reports and
-- the engagement replay all hang off course->lecture, and a lecture that has
-- not been filed under a module yet must still belong to its course.
--
-- ON DELETE SET NULL, not CASCADE: reorganising the modules of a course must
-- never delete the teaching material inside them.
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS module_id INTEGER
    REFERENCES modules(id) ON DELETE SET NULL;

-- 5. Indexes for the filters the catalog search actually uses --------------
--
-- courses.doctor_id had no index at all: Postgres does not index a foreign key
-- for you, and "which courses does this doctor teach" is the first question
-- the assistant asks.
CREATE INDEX IF NOT EXISTS idx_courses_doctor ON courses(doctor_id);
CREATE INDEX IF NOT EXISTS idx_courses_subject ON courses(subject_id);
CREATE INDEX IF NOT EXISTS idx_courses_academic_year ON courses(academic_year);
CREATE INDEX IF NOT EXISTS idx_modules_course ON modules(course_id, position);
CREATE INDEX IF NOT EXISTS idx_lectures_module ON lectures(module_id);

-- No composite index on (subject_id, academic_year) yet. It would serve
-- "Physiology, year 2", but `courses` holds single digits of rows and Postgres
-- will sequential-scan it whatever indexes exist. Add it when the catalog is
-- large enough for the planner to want it, not before.

-- NOT APPLIED, on purpose: enforcing that a lecture's module belongs to the
-- lecture's course. Postgres can do it without a trigger, with a composite
-- foreign key:
--
--     ALTER TABLE modules ADD CONSTRAINT modules_id_course_key UNIQUE (id, course_id);
--     ALTER TABLE lectures ADD CONSTRAINT lectures_module_matches_course
--         FOREIGN KEY (module_id, course_id) REFERENCES modules(id, course_id);
--
-- With the default MATCH SIMPLE, a NULL in either column skips the check, so
-- unfiled lectures stay legal. The cost is that moving a lecture between
-- courses now has to clear or update module_id in the same statement. Left to
-- the application for now, as agreed — the two lines above are the whole
-- upgrade whenever you want the database to guarantee it instead.
