-- Review handoff only. The authoritative migration is:
-- way2APlus_db/supabase/migrations/20260909120000_assessment_analytics.sql
--
-- This FastAPI repository does not own shared database migration history.
-- See db/README.md. Keep this copy byte-for-byte aligned with the authoritative
-- file during review; apply only from educational-platform-db.

BEGIN;

ALTER TABLE public.exam_questions
    ADD COLUMN IF NOT EXISTS topic_id integer,
    ADD COLUMN IF NOT EXISTS learning_objective text,
    ADD COLUMN IF NOT EXISTS difficulty varchar(20);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.exam_questions'::regclass
          AND conname = 'exam_questions_topic_id_fkey'
    ) THEN
        ALTER TABLE public.exam_questions
            ADD CONSTRAINT exam_questions_topic_id_fkey
            FOREIGN KEY (topic_id) REFERENCES public.topics(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.exam_questions'::regclass
          AND conname = 'exam_questions_difficulty_check'
    ) THEN
        ALTER TABLE public.exam_questions
            ADD CONSTRAINT exam_questions_difficulty_check
            CHECK (difficulty IS NULL OR difficulty IN ('easy', 'moderate', 'difficult'))
            NOT VALID;
        ALTER TABLE public.exam_questions
            VALIDATE CONSTRAINT exam_questions_difficulty_check;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_exam_questions_topic
    ON public.exam_questions (topic_id) WHERE topic_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.assessment_question_attempts (
    id bigserial PRIMARY KEY,
    exam_attempt_id integer NOT NULL
        REFERENCES public.exam_attempts(id) ON DELETE CASCADE,
    exam_question_id integer NOT NULL
        REFERENCES public.exam_questions(id) ON DELETE CASCADE,
    student_id integer NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    attempt_number smallint NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
    selected_option_ids integer[],
    is_correct boolean,
    shown_at timestamptz NOT NULL,
    answered_at timestamptz,
    response_time_ms integer CHECK (response_time_ms IS NULL OR response_time_ms >= 0),
    skipped boolean NOT NULL DEFAULT false,
    timed_out boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT assessment_question_attempts_outcome CHECK (
        NOT (skipped AND timed_out)
        AND (
            (answered_at IS NOT NULL AND is_correct IS NOT NULL
             AND selected_option_ids IS NOT NULL AND NOT skipped AND NOT timed_out)
            OR
            (answered_at IS NULL AND is_correct IS NULL
             AND selected_option_ids IS NULL AND (skipped OR timed_out))
        )
    ),
    CONSTRAINT assessment_question_attempts_response_time CHECK (
        (answered_at IS NULL AND response_time_ms IS NULL)
        OR (answered_at IS NOT NULL AND response_time_ms IS NOT NULL)
    ),
    UNIQUE (exam_attempt_id, exam_question_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS idx_assessment_question_attempts_exam_student
    ON public.assessment_question_attempts (exam_attempt_id, student_id);
CREATE INDEX IF NOT EXISTS idx_assessment_question_attempts_question_time
    ON public.assessment_question_attempts (exam_question_id, answered_at);
CREATE INDEX IF NOT EXISTS idx_assessment_question_attempts_student_question_attempt
    ON public.assessment_question_attempts
       (student_id, exam_question_id, attempt_number, created_at);

CREATE OR REPLACE FUNCTION public.validate_assessment_question_attempt_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.exam_attempts attempt
        JOIN public.exam_questions question
          ON question.exam_id = attempt.exam_id
        WHERE attempt.id = NEW.exam_attempt_id
          AND question.id = NEW.exam_question_id
          AND attempt.user_id = NEW.student_id
    ) THEN
        RAISE EXCEPTION 'assessment question attempt scope mismatch';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_assessment_question_attempt_scope
    ON public.assessment_question_attempts;
CREATE TRIGGER trg_assessment_question_attempt_scope
    BEFORE INSERT OR UPDATE ON public.assessment_question_attempts
    FOR EACH ROW EXECUTE FUNCTION public.validate_assessment_question_attempt_scope();

ALTER TABLE public.assessment_question_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.assessment_question_attempts FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.assessment_question_attempts_id_seq FROM anon, authenticated;

COMMENT ON TABLE public.assessment_question_attempts IS
    'Raw per-question response evidence; teacher analytics are derived, never stored here.';
COMMENT ON COLUMN public.assessment_question_attempts.attempt_number IS
    'Retry sequence within one exam attempt and question; starts at 1.';
COMMENT ON COLUMN public.assessment_question_attempts.response_time_ms IS
    'Client-observed duration from question shown to answered, in milliseconds.';

COMMIT;
