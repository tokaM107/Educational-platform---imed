-- Production learning analytics storage.
--
-- HANDOFF COPY: create the authoritative migration in educational-platform-db.
-- Do not execute this proposal a second time from the FastAPI repository.
--
-- The change is expand-only except for new constraints on new columns.  Its
-- practical rollback is to deploy readers that ignore the new objects, then
-- drop the five new tables and the four nullable columns added below.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.users') IS NULL
       OR to_regclass('public.courses') IS NULL
       OR to_regclass('public.lectures') IS NULL
       OR to_regclass('public.topics') IS NULL THEN
        RAISE EXCEPTION 'learning analytics requires the core learning schema';
    END IF;
END $$;

-- Event idempotency and playback rate are observations, not derived report
-- values.  Nullable client_event_id preserves every legacy event.
ALTER TABLE public.video_events
    ADD COLUMN IF NOT EXISTS video_id integer,
    ADD COLUMN IF NOT EXISTS client_event_id uuid,
    ADD COLUMN IF NOT EXISTS playback_rate numeric(4,2);

ALTER TABLE public.video_events ALTER COLUMN lecture_id DROP NOT NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.video_events'::regclass
          AND conname = 'video_events_video_id_fkey'
    ) THEN
        ALTER TABLE public.video_events ADD CONSTRAINT video_events_video_id_fkey
            FOREIGN KEY (video_id) REFERENCES public.course_items(id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.video_events'::regclass
          AND conname = 'video_events_one_content_source_check'
    ) THEN
        ALTER TABLE public.video_events ADD CONSTRAINT video_events_one_content_source_check
            CHECK (num_nonnulls(lecture_id, video_id) = 1) NOT VALID;
        ALTER TABLE public.video_events VALIDATE CONSTRAINT video_events_one_content_source_check;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_video_events_student_video_session
    ON public.video_events (student_id, video_id, session_id, created_at)
    WHERE video_id IS NOT NULL;

ALTER TABLE public.video_events
    DROP CONSTRAINT IF EXISTS video_events_playback_rate_check;
ALTER TABLE public.video_events
    ADD CONSTRAINT video_events_playback_rate_check
    CHECK (playback_rate IS NULL OR playback_rate BETWEEN 0.25 AND 4.00);

CREATE UNIQUE INDEX IF NOT EXISTS uq_video_events_student_client_event
    ON public.video_events (student_id, client_event_id)
    WHERE client_event_id IS NOT NULL;

-- One durable row per browser lesson sitting.  Raw video_events remain the
-- source of truth for replay; this table supplies course scope and lifecycle
-- without repeatedly discovering sessions from millions of heartbeats.
CREATE TABLE IF NOT EXISTS public.student_study_sessions (
    id bigserial PRIMARY KEY,
    student_id integer NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    course_id integer NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    lecture_id integer REFERENCES public.lectures(id) ON DELETE CASCADE,
    video_id integer REFERENCES public.course_items(id) ON DELETE CASCADE,
    session_key varchar(64) NOT NULL,
    started_at timestamptz NOT NULL,
    last_event_at timestamptz NOT NULL,
    ended_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT student_study_sessions_one_source
        CHECK (num_nonnulls(lecture_id, video_id) = 1),
    CONSTRAINT student_study_sessions_time_order
        CHECK (last_event_at >= started_at AND
               (ended_at IS NULL OR ended_at >= started_at)),
    -- Source-specific uniqueness is enforced by the partial indexes below;
    -- a four-column UNIQUE would treat the unused NULL source as distinct.
    CONSTRAINT student_study_sessions_source_present
        CHECK (lecture_id IS NOT NULL OR video_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_study_sessions_lecture
    ON public.student_study_sessions (student_id, session_key, lecture_id)
    WHERE lecture_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_study_sessions_video
    ON public.student_study_sessions (student_id, session_key, video_id)
    WHERE video_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_study_sessions_student_course_date
    ON public.student_study_sessions (student_id, course_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_study_sessions_lecture
    ON public.student_study_sessions (lecture_id, started_at DESC)
    WHERE lecture_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_study_sessions_video
    ON public.student_study_sessions (video_id, started_at DESC)
    WHERE video_id IS NOT NULL;

-- Preserve already-recorded production sittings.  This is idempotent and does
-- not rewrite raw events.
INSERT INTO public.student_study_sessions (
    student_id, course_id, lecture_id, session_key, started_at, last_event_at,
    ended_at
)
SELECT e.student_id, l.course_id, e.lecture_id, e.session_id,
       min(e.created_at), max(e.created_at),
       max(e.created_at) FILTER (WHERE e.event_type IN ('complete', 'tab_hidden'))
FROM public.video_events e
JOIN public.lectures l ON l.id = e.lecture_id
WHERE e.lecture_id IS NOT NULL AND e.session_id IS NOT NULL AND l.course_id IS NOT NULL
GROUP BY e.student_id, l.course_id, e.lecture_id, e.session_id
ON CONFLICT DO NOTHING;

INSERT INTO public.student_study_sessions (
    student_id, course_id, video_id, session_key, started_at, last_event_at,
    ended_at
)
SELECT e.student_id, item.course_id, e.video_id, e.session_id,
       min(e.created_at), max(e.created_at),
       max(e.created_at) FILTER (WHERE e.event_type IN ('complete', 'tab_hidden'))
FROM public.video_events e
JOIN public.course_items item ON item.id = e.video_id
WHERE e.video_id IS NOT NULL AND e.session_id IS NOT NULL
GROUP BY e.student_id, item.course_id, e.video_id, e.session_id
ON CONFLICT DO NOTHING;

-- The answer key lives once on the question.  Attempts use it through a join;
-- duplicating it onto every attempt would let corrected content disagree with
-- historical rows.  source_key is the stable identifier used by the video
-- authoring/checkpoint system during idempotent ingest.
CREATE TABLE IF NOT EXISTS public.checkpoint_questions (
    id bigserial PRIMARY KEY,
    course_id integer NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    lecture_id integer REFERENCES public.lectures(id) ON DELETE CASCADE,
    video_id integer REFERENCES public.course_items(id) ON DELETE CASCADE,
    topic_id integer REFERENCES public.topics(id) ON DELETE SET NULL,
    source_key text NOT NULL,
    prompt text NOT NULL,
    options jsonb NOT NULL CHECK (jsonb_typeof(options) = 'array'),
    correct_answer text NOT NULL,
    checkpoint_timestamp_seconds numeric(10,3) NOT NULL
        CHECK (checkpoint_timestamp_seconds >= 0),
    allowed_time_seconds numeric(8,3) NOT NULL CHECK (allowed_time_seconds > 0),
    position smallint NOT NULL CHECK (position > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT checkpoint_questions_one_source
        CHECK (num_nonnulls(lecture_id, video_id) = 1),
    UNIQUE (course_id, source_key)
);

CREATE INDEX IF NOT EXISTS idx_checkpoint_questions_course
    ON public.checkpoint_questions (course_id, lecture_id, video_id, position);
CREATE INDEX IF NOT EXISTS idx_checkpoint_questions_topic
    ON public.checkpoint_questions (topic_id)
    WHERE topic_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_checkpoint_questions_lecture
    ON public.checkpoint_questions (lecture_id, checkpoint_timestamp_seconds)
    WHERE lecture_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_checkpoint_questions_video
    ON public.checkpoint_questions (video_id, checkpoint_timestamp_seconds)
    WHERE video_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.checkpoint_attempts (
    id bigserial PRIMARY KEY,
    student_id integer NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    course_id integer NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    checkpoint_question_id bigint NOT NULL
        REFERENCES public.checkpoint_questions(id) ON DELETE CASCADE,
    session_id varchar(64) NOT NULL,
    shown_at timestamptz NOT NULL,
    answered_at timestamptz,
    response_time_ms integer CHECK (response_time_ms IS NULL OR response_time_ms >= 0),
    selected_answer text,
    is_correct boolean,
    skipped boolean NOT NULL DEFAULT false,
    timed_out boolean NOT NULL DEFAULT false,
    attempt_number smallint NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT checkpoint_attempts_outcome CHECK (
        NOT (skipped AND timed_out) AND
        ((answered_at IS NOT NULL AND selected_answer IS NOT NULL
          AND is_correct IS NOT NULL AND NOT skipped AND NOT timed_out)
         OR
         (answered_at IS NULL AND selected_answer IS NULL
          AND is_correct IS NULL AND (skipped OR timed_out)))
    ),
    CONSTRAINT checkpoint_attempts_response_time CHECK (
        (answered_at IS NULL AND response_time_ms IS NULL) OR
        (answered_at IS NOT NULL AND response_time_ms IS NOT NULL)
    ),
    UNIQUE (student_id, checkpoint_question_id, session_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS idx_checkpoint_attempts_student_course_date
    ON public.checkpoint_attempts (student_id, course_id, shown_at DESC);
CREATE INDEX IF NOT EXISTS idx_checkpoint_attempts_question
    ON public.checkpoint_attempts (checkpoint_question_id, shown_at DESC);

-- Per-question exam evidence is written when the Nest grader finalises an
-- exam attempt.  exam_attempts.score alone cannot support topic/error analysis.
CREATE TABLE IF NOT EXISTS public.assessment_question_results (
    id bigserial PRIMARY KEY,
    student_id integer NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    course_id integer NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    exam_attempt_id integer NOT NULL REFERENCES public.exam_attempts(id) ON DELETE CASCADE,
    exam_question_id integer NOT NULL REFERENCES public.exam_questions(id) ON DELETE CASCADE,
    selected_answer jsonb,
    is_correct boolean NOT NULL,
    awarded_points numeric(10,3),
    response_time_ms integer CHECK (response_time_ms IS NULL OR response_time_ms >= 0),
    assessed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (exam_attempt_id, exam_question_id)
);

CREATE INDEX IF NOT EXISTS idx_assessment_results_student_course_date
    ON public.assessment_question_results (student_id, course_id, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_assessment_results_question
    ON public.assessment_question_results (exam_question_id, assessed_at DESC);

-- Assignments define the denominator for "expected vs actual".  In their
-- absence the report returns NOT_ENOUGH_DATA instead of treating the full
-- course catalog as this week's required work.
CREATE TABLE IF NOT EXISTS public.course_weekly_assignments (
    id bigserial PRIMARY KEY,
    course_id integer NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    lecture_id integer REFERENCES public.lectures(id) ON DELETE CASCADE,
    video_id integer REFERENCES public.course_items(id) ON DELETE CASCADE,
    week_start date NOT NULL,
    available_from timestamptz NOT NULL,
    due_at timestamptz NOT NULL,
    required boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT course_weekly_assignments_one_source
        CHECK (num_nonnulls(lecture_id, video_id) = 1),
    CONSTRAINT course_weekly_assignments_dates CHECK (due_at > available_from),
    CONSTRAINT course_weekly_assignments_source_present
        CHECK (lecture_id IS NOT NULL OR video_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_weekly_assignments_lecture
    ON public.course_weekly_assignments (course_id, week_start, lecture_id)
    WHERE lecture_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_weekly_assignments_video
    ON public.course_weekly_assignments (course_id, week_start, video_id)
    WHERE video_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_weekly_assignments_course_week
    ON public.course_weekly_assignments (course_id, week_start, due_at);

-- Explicit delayed reassessment evidence.  Retention is unavailable until
-- rows exist at least three days after the immediate observation; the report
-- never manufactures it from watch behavior.
CREATE TABLE IF NOT EXISTS public.retention_assessment_results (
    id bigserial PRIMARY KEY,
    student_id integer NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    course_id integer NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    topic_id integer NOT NULL REFERENCES public.topics(id) ON DELETE CASCADE,
    source_type varchar(30) NOT NULL CHECK (
        source_type IN ('checkpoint', 'quiz', 'exam', 'practice')
    ),
    source_result_id text NOT NULL,
    is_correct boolean NOT NULL,
    assessed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (student_id, source_type, source_result_id)
);

CREATE INDEX IF NOT EXISTS idx_retention_student_course_topic_date
    ON public.retention_assessment_results
       (student_id, course_id, topic_id, assessed_at);

-- A versioned snapshot makes a closed weekly report reproducible after source
-- metadata changes.  The raw tables remain canonical; this is a document/cache
-- with a source fingerprint, not a second event store.
CREATE TABLE IF NOT EXISTS public.weekly_analytics_snapshots (
    id bigserial PRIMARY KEY,
    student_id integer NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    course_id integer NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    week_start date NOT NULL,
    report_version varchar(40) NOT NULL,
    source_fingerprint char(64) NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    generated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (student_id, course_id, week_start, report_version)
);

CREATE INDEX IF NOT EXISTS idx_weekly_snapshots_student_course_week
    ON public.weekly_analytics_snapshots
       (student_id, course_id, week_start DESC, generated_at DESC);

-- Redundant course/student columns make every analytics query explicitly
-- scoped and indexable. Triggers below prevent those denormalised scope values
-- from ever disagreeing with their authoritative resource/attempt rows.
CREATE OR REPLACE FUNCTION public.validate_learning_resource_course()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual_course integer;
BEGIN
    IF NEW.lecture_id IS NOT NULL THEN
        SELECT course_id INTO actual_course FROM public.lectures WHERE id = NEW.lecture_id;
    ELSE
        SELECT course_id INTO actual_course FROM public.course_items WHERE id = NEW.video_id;
    END IF;
    IF actual_course IS NULL OR actual_course IS DISTINCT FROM NEW.course_id THEN
        RAISE EXCEPTION 'learning resource does not belong to analytics course';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_checkpoint_question_course ON public.checkpoint_questions;
CREATE TRIGGER trg_checkpoint_question_course
    BEFORE INSERT OR UPDATE ON public.checkpoint_questions
    FOR EACH ROW EXECUTE FUNCTION public.validate_learning_resource_course();
DROP TRIGGER IF EXISTS trg_study_session_course ON public.student_study_sessions;
CREATE TRIGGER trg_study_session_course
    BEFORE INSERT OR UPDATE ON public.student_study_sessions
    FOR EACH ROW EXECUTE FUNCTION public.validate_learning_resource_course();
DROP TRIGGER IF EXISTS trg_weekly_assignment_course ON public.course_weekly_assignments;
CREATE TRIGGER trg_weekly_assignment_course
    BEFORE INSERT OR UPDATE ON public.course_weekly_assignments
    FOR EACH ROW EXECUTE FUNCTION public.validate_learning_resource_course();

CREATE OR REPLACE FUNCTION public.validate_checkpoint_attempt_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.checkpoint_questions q
        WHERE q.id = NEW.checkpoint_question_id AND q.course_id = NEW.course_id
    ) THEN
        RAISE EXCEPTION 'checkpoint attempt course does not match question';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_checkpoint_attempt_scope ON public.checkpoint_attempts;
CREATE TRIGGER trg_checkpoint_attempt_scope
    BEFORE INSERT OR UPDATE ON public.checkpoint_attempts
    FOR EACH ROW EXECUTE FUNCTION public.validate_checkpoint_attempt_scope();

CREATE OR REPLACE FUNCTION public.validate_assessment_result_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.exam_attempts a
        JOIN public.exams e ON e.id = a.exam_id
        JOIN public.exam_questions q ON q.exam_id = e.id
        WHERE a.id = NEW.exam_attempt_id
          AND q.id = NEW.exam_question_id
          AND a.user_id = NEW.student_id
          AND e.course_id = NEW.course_id
    ) THEN
        RAISE EXCEPTION 'assessment result student/course/question scope mismatch';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_assessment_result_scope ON public.assessment_question_results;
CREATE TRIGGER trg_assessment_result_scope
    BEFORE INSERT OR UPDATE ON public.assessment_question_results
    FOR EACH ROW EXECUTE FUNCTION public.validate_assessment_result_scope();

-- Deterministic metadata only.  The report does not ask an LLM to invent a
-- cognitive taxonomy or topic for assessment questions.
ALTER TABLE public.questions
    ADD COLUMN IF NOT EXISTS learning_objective text,
    ADD COLUMN IF NOT EXISTS cognitive_level varchar(20);
ALTER TABLE public.questions
    DROP CONSTRAINT IF EXISTS questions_cognitive_level_check;
ALTER TABLE public.questions
    ADD CONSTRAINT questions_cognitive_level_check CHECK (
        cognitive_level IS NULL OR cognitive_level IN
            ('recall', 'understanding', 'application')
    );

ALTER TABLE public.exam_questions
    ADD COLUMN IF NOT EXISTS topic text,
    ADD COLUMN IF NOT EXISTS learning_objective text,
    ADD COLUMN IF NOT EXISTS cognitive_level varchar(20),
    ADD COLUMN IF NOT EXISTS difficulty varchar(20);
ALTER TABLE public.exam_questions
    DROP CONSTRAINT IF EXISTS exam_questions_cognitive_level_check;
ALTER TABLE public.exam_questions
    ADD CONSTRAINT exam_questions_cognitive_level_check CHECK (
        cognitive_level IS NULL OR cognitive_level IN
            ('recall', 'understanding', 'application')
    );

ALTER TABLE public.chat_messages
    ADD COLUMN IF NOT EXISTS topic_id integer REFERENCES public.topics(id)
        ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS trigger_checkpoint_attempt_id bigint
        REFERENCES public.checkpoint_attempts(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_chat_messages_topic
    ON public.chat_messages (topic_id, created_at DESC)
    WHERE topic_id IS NOT NULL;

-- These are private learning records.  Backend roles own access; the public
-- Data API receives no policies.
ALTER TABLE public.student_study_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.checkpoint_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.checkpoint_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.assessment_question_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.course_weekly_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.retention_assessment_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.weekly_analytics_snapshots ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'student_study_sessions', 'checkpoint_questions', 'checkpoint_attempts',
        'assessment_question_results',
        'course_weekly_assignments', 'retention_assessment_results',
        'weekly_analytics_snapshots'
    ] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon', table_name);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM authenticated', table_name);
        END IF;
    END LOOP;
END $$;

COMMIT;
