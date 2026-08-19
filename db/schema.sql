-- Enable the pgvector extension (needed for the embedding column below).
-- Requires a Postgres image that includes pgvector, e.g. pgvector/pgvector:pg16
-- CREATE EXTENSION IF NOT EXISTS vector;

-- People: students and doctors
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    role VARCHAR(20) NOT NULL CHECK (role IN ('student', 'doctor')),
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Subject tags, e.g. "Cardiology basics"
CREATE TABLE topics (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL
);

-- What a course is about. Normalised so "every Physiology course" is a join
-- rather than a LIKE over free text that a typo can hide a row from. Nothing
-- about a doctor lives here: a subject is taught by many doctors.
CREATE TABLE subjects (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE
);

-- A course a doctor teaches. The weekly report needs this: "registered for 5
-- lectures, watched 3" has no denominator without somewhere to say which
-- lectures a student was supposed to watch.
--
-- `subject_id` and `academic_year` are the structured fields the catalog
-- search filters on — the year is data, not something parsed out of the title.
-- Both are nullable because courses can exist before either is known.
--
-- The year range is a typo guard: the realistic mistake is writing a calendar
-- year (2026), which would silently match nothing forever.
CREATE TABLE courses (
    id SERIAL PRIMARY KEY,
    doctor_id INTEGER NOT NULL REFERENCES users(id),
    subject_id INTEGER REFERENCES subjects(id),
    academic_year SMALLINT CONSTRAINT courses_academic_year_check
        CHECK (academic_year IS NULL OR academic_year BETWEEN 1 AND 7),
    title VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A block of lectures inside a course, e.g. "Cardiovascular" in Physiology I.
-- Belongs to exactly one course and cascades with it. `position` is the
-- teaching order — a catalog listed by id tells the student nothing about
-- which module comes first.
CREATE TABLE modules (
    id SERIAL PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    position SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (course_id, title)
);

-- One row per lecture. course_id is nullable so a standalone lecture is still
-- a valid row; it just cannot appear in a course report.
--
-- `module_id` is where the lecture sits in the catalog, and is optional: a
-- lecture that has not been filed under a module yet still belongs to its
-- course. ON DELETE SET NULL, not CASCADE — reorganising a course's modules
-- must never delete the teaching material inside them.
--
-- course_id is kept alongside module_id on purpose. Enrolment, subscriptions,
-- reports and the engagement replay all hang off course->lecture, and none of
-- them should have to go through a module that may not exist.
CREATE TABLE lectures (
    id SERIAL PRIMARY KEY,
    doctor_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER REFERENCES courses(id),
    module_id INTEGER REFERENCES modules(id) ON DELETE SET NULL,
    title VARCHAR(255) NOT NULL,
    video_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Who is registered for what
CREATE TABLE enrollments (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, course_id)
);

-- Transcript, cut into small searchable pieces with timestamps
CREATE TABLE transcript_chunks (
    id SERIAL PRIMARY KEY,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    start_ts INTEGER NOT NULL,   -- seconds into the video
    end_ts INTEGER NOT NULL,
    embedding vector(1536)       -- match this to your embedding model's output size
);

-- Cached question embeddings.
-- The transcript is embedded once by rag/ingest.py; this table does the same
-- for the query side, so a question that was asked before costs a lookup
-- instead of an API call. Keyed by model + dimension so changing either does
-- not silently reuse vectors from a different space.
CREATE TABLE query_embeddings (
    id SERIAL PRIMARY KEY,
    query_hash CHAR(64) NOT NULL,      -- sha256 of the normalised question
    model VARCHAR(64) NOT NULL,
    dim INTEGER NOT NULL,
    query TEXT NOT NULL,
    embedding vector(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (query_hash, model, dim)
);

-- Every pause / skip / completion a student does while watching.
--
-- `heartbeat` is emitted every 30 seconds while the video is actually
-- playing; without it a play/pause pair cannot tell "watched for half an
-- hour" apart from "pressed play and walked away".
--
-- `tab_hidden` / `tab_visible` record the lecture page losing and regaining
-- visibility. That is all they record: another tab, a locked screen and a
-- minimised window are indistinguishable here, so these mean "time away from
-- the lecture page", never a claim about what the student did instead.
CREATE TABLE video_events (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    event_type VARCHAR(20) NOT NULL CHECK (
        event_type IN (
            'play', 'pause', 'seek', 'skip', 'complete', 'rewatch_segment',
            'heartbeat', 'tab_hidden', 'tab_visible'
        )
    ),
    video_ts FLOAT NOT NULL,  -- seconds into the video
    session_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One stored weekly-report narrative per student, course and week.
--
-- The numbers in a report are always recomputed (a replay of that week's
-- events costs milliseconds). The written commentary is stored because a
-- report is a document: regenerated on every page load it would give the
-- student different advice each time they opened the same week. `fingerprint`
-- hashes the figures it was written from, so it is rewritten while the week is
-- still moving and settles once the week closes.
CREATE TABLE report_narratives (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    week_start DATE NOT NULL,
    fingerprint CHAR(64) NOT NULL,
    narrative JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, course_id, week_start)
);

-- Reports a moment produced rather than a calendar: "you finished the module",
-- "you finished this lecture's questions". A weekly report covers a rolling
-- window and is recomputed on every read; a completion report describes an
-- instant, so it is frozen whole when it fires.
CREATE TABLE reports (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('module', 'exam')),
    lecture_id INTEGER REFERENCES lectures(id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- How a report reaches the student it is about and the doctor who teaches them.
CREATE TABLE notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind VARCHAR(30) NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE,
    student_id INTEGER REFERENCES users(id),
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quiz questions
CREATE TABLE questions (
    id SERIAL PRIMARY KEY,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    topic_id INTEGER REFERENCES topics(id),
    stem TEXT NOT NULL,
    options JSONB NOT NULL,        -- e.g. ["A) ...", "B) ...", "C) ...", "D) ..."]
    correct_option VARCHAR(5) NOT NULL,
    difficulty VARCHAR(20)
);

-- Every time a student answers a question.
--
-- `selected_option` is what makes a post-exam review actionable: "38% got it
-- wrong" does not say whether the class split evenly across the distractors
-- (a hard question) or piled onto one of them (a distractor that is teaching
-- something false, or an ambiguous stem). Nullable, because attempts recorded
-- before the column existed have no answer to give.
CREATE TABLE question_attempts (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    is_correct BOOLEAN NOT NULL,
    selected_option VARCHAR(5),
    answered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A student's paid access to one teacher's material.
--
-- Separate from enrolment because the two facts change independently: a
-- subscription lapses without un-enrolling anybody, and a student subscribed to
-- a teacher may be enrolled on none, one or several of that teacher's courses.
CREATE TABLE subscriptions (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    doctor_id INTEGER NOT NULL REFERENCES users(id),
    subscribed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, doctor_id)
);

-- Indexes for the lookups you'll run most (by student, by lecture)
CREATE INDEX idx_video_events_student ON video_events(student_id);
CREATE INDEX idx_video_events_lecture ON video_events(lecture_id);
CREATE INDEX idx_lectures_course ON lectures(course_id);

-- Catalog search filters. courses.doctor_id in particular: Postgres does not
-- index a foreign key for you, and "which courses does this doctor teach" is
-- the first question the search asks.
CREATE INDEX idx_courses_doctor ON courses(doctor_id);
CREATE INDEX idx_courses_subject ON courses(subject_id);
CREATE INDEX idx_courses_academic_year ON courses(academic_year);
CREATE INDEX idx_modules_course ON modules(course_id, position);
CREATE INDEX idx_lectures_module ON lectures(module_id);
CREATE INDEX idx_enrollments_student ON enrollments(student_id);
CREATE INDEX idx_question_attempts_student_date
    ON question_attempts (student_id, answered_at);
CREATE INDEX idx_report_narratives_week ON report_narratives (week_start);
CREATE INDEX idx_subscriptions_student ON subscriptions(student_id);
CREATE INDEX idx_subscriptions_doctor ON subscriptions(doctor_id);
CREATE INDEX idx_question_attempts_option
    ON question_attempts (question_id, selected_option)
    WHERE selected_option IS NOT NULL;

-- A completion fires at most one report, however many times the event repeats.
CREATE UNIQUE INDEX idx_reports_once
    ON reports (student_id, course_id, kind, COALESCE(lecture_id, 0));
CREATE INDEX idx_reports_student ON reports (student_id, generated_at DESC);

-- The unread badge is the hottest query on notifications.
CREATE INDEX idx_notifications_inbox
    ON notifications (user_id, read_at, created_at DESC);
CREATE UNIQUE INDEX idx_notifications_once
    ON notifications (user_id, report_id) WHERE report_id IS NOT NULL;

-- Engagement analytics always reads one student's one session in event order.
-- The composite index answers that as a single ordered range scan with no
-- sort step, and its leading columns still serve student-only lookups.
-- Heartbeats make this the fastest-growing table in the schema, so the read
-- side is worth the extra write.
CREATE INDEX idx_video_events_session
    ON video_events (student_id, lecture_id, session_id, created_at);
CREATE INDEX idx_question_attempts_student ON question_attempts(student_id);
CREATE INDEX idx_transcript_chunks_lecture ON transcript_chunks(lecture_id);

-- Approximate nearest-neighbour index for the similarity search.
-- Without it every question scans the whole table; cosine ops must match the
-- `<=>` operator used in app/services/retrieval.py.
CREATE INDEX idx_transcript_chunks_embedding
    ON transcript_chunks
    USING hnsw (embedding vector_cosine_ops);