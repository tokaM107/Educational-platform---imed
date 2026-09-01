-- Production storage for versioned, auditable LLM-assisted essay grading.
--
-- HANDOFF ONLY: the shared schema is owned by educational-platform-db. Create a
-- Supabase migration there, copy this SQL into it, and validate both consumers
-- before applying it. Do not run this file from the FastAPI repository.
--
-- Design summary:
--   * exam_questions remains the exam's ordered question list; `essay` becomes
--     one more question type.
--   * question/model-answer/criteria versions are immutable. Publishing a new
--     version never rewrites a grade already issued from an earlier version.
--   * submissions preserve exactly what the student submitted and the exact
--     released question version used.
--   * grading runs and criterion results are append-only. A retry or model
--     upgrade creates another run instead of overwriting the first result.
--   * doctor reviews are append-only. A submission points at the review that
--     finalized its academic score; older reviews remain available for audit.
--   * exact scoring inputs use NUMERIC, never floating point.
--   * RLS is enabled with no public policies. Only trusted backend connections
--     should read model answers, student answers, evidence, or raw responses.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.users') IS NULL
       OR to_regclass('public.courses') IS NULL
       OR to_regclass('public.exams') IS NULL
       OR to_regclass('public.exam_questions') IS NULL
       OR to_regclass('public.exam_attempts') IS NULL
    THEN
        RAISE EXCEPTION
            'essay grading migration requires users, courses, exams, '
            'exam_questions, and exam_attempts';
    END IF;
END $$;

-- The existing question table remains authoritative for exam membership,
-- position, display text and nominal points. Essay-only answer-key data lives
-- in immutable version rows below and must never be serialized to students.
ALTER TABLE public.exam_questions
    DROP CONSTRAINT IF EXISTS exam_questions_type_check;

ALTER TABLE public.exam_questions
    ADD CONSTRAINT exam_questions_type_check
    CHECK (type IN ('single_choice', 'multi_choice', 'true_false', 'essay'))
    NOT VALID;

ALTER TABLE public.exam_questions
    VALIDATE CONSTRAINT exam_questions_type_check;


-- ---------------------------------------------------------------------------
-- Immutable answer-key versions and criteria
-- ---------------------------------------------------------------------------

CREATE TABLE public.essay_question_versions (
    id BIGSERIAL PRIMARY KEY,
    exam_question_id INTEGER NOT NULL
        REFERENCES public.exam_questions(id) ON DELETE RESTRICT,
    version_number INTEGER NOT NULL CHECK (version_number > 0),

    -- Snapshot both, even though exam_questions.text also exists. The visible
    -- text can be edited for a later release; an old submission must retain the
    -- exact question it answered.
    question_text TEXT NOT NULL
        CHECK (char_length(btrim(question_text)) BETWEEN 1 AND 10000),
    model_answer TEXT NOT NULL
        CHECK (char_length(btrim(model_answer)) BETWEEN 1 AND 50000),
    max_points NUMERIC(10, 2) NOT NULL CHECK (max_points > 0),

    criteria_status VARCHAR(20) NOT NULL CHECK (
        criteria_status IN ('ready', 'needs_review', 'failed')
    ),
    criteria_needs_review BOOLEAN NOT NULL DEFAULT false,
    criteria_review_reason TEXT,

    criteria_model_identifier TEXT,
    criteria_prompt_version TEXT,
    criteria_latency_ms INTEGER CHECK (
        criteria_latency_ms IS NULL OR criteria_latency_ms >= 0
    ),
    criteria_input_tokens INTEGER CHECK (
        criteria_input_tokens IS NULL OR criteria_input_tokens >= 0
    ),
    criteria_output_tokens INTEGER CHECK (
        criteria_output_tokens IS NULL OR criteria_output_tokens >= 0
    ),
    criteria_raw_response TEXT,
    criteria_parsed_response JSONB,
    criteria_retry_count INTEGER NOT NULL DEFAULT 0
        CHECK (criteria_retry_count >= 0),
    criteria_retry_errors JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(criteria_retry_errors) = 'array'),
    criteria_error_code VARCHAR(100),
    criteria_error_detail TEXT,

    created_by INTEGER NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT essay_question_versions_number_key
        UNIQUE (exam_question_id, version_number),
    CONSTRAINT essay_question_versions_id_question_key
        UNIQUE (id, exam_question_id),
    CONSTRAINT essay_question_versions_review_reason_check CHECK (
        criteria_review_reason IS NULL
        OR char_length(criteria_review_reason) <= 1000
    ),
    CONSTRAINT essay_question_versions_state_check CHECK (
        (
            criteria_status = 'ready'
            AND criteria_needs_review = false
            AND criteria_model_identifier IS NOT NULL
            AND criteria_prompt_version IS NOT NULL
            AND criteria_raw_response IS NOT NULL
            AND criteria_parsed_response IS NOT NULL
            AND criteria_error_code IS NULL
            AND criteria_error_detail IS NULL
        )
        OR (
            criteria_status = 'needs_review'
            AND criteria_needs_review = true
            AND criteria_review_reason IS NOT NULL
            AND criteria_model_identifier IS NOT NULL
            AND criteria_prompt_version IS NOT NULL
            AND criteria_raw_response IS NOT NULL
            AND criteria_parsed_response IS NOT NULL
            AND criteria_error_code IS NULL
            AND criteria_error_detail IS NULL
        )
        OR (
            criteria_status = 'failed'
            AND criteria_parsed_response IS NULL
            AND criteria_error_code IS NOT NULL
            AND criteria_error_detail IS NOT NULL
        )
    )
);

COMMENT ON TABLE public.essay_question_versions IS
    'FastAPI-owned immutable essay question/model-answer and criteria-generation snapshots. RLS enabled with no policies.';
COMMENT ON COLUMN public.essay_question_versions.model_answer IS
    'ANSWER KEY. Trusted backend and owning doctor only; never serialize to a student.';
COMMENT ON COLUMN public.essay_question_versions.criteria_raw_response IS
    'Provider structured response only; never hidden reasoning or chain-of-thought.';

CREATE TABLE public.essay_criteria (
    id BIGSERIAL PRIMARY KEY,
    question_version_id BIGINT NOT NULL
        REFERENCES public.essay_question_versions(id) ON DELETE RESTRICT,
    criterion_key VARCHAR(20) NOT NULL CHECK (
        criterion_key ~ '^C[1-9][0-9]*$'
    ),
    position SMALLINT NOT NULL CHECK (position > 0 AND position <= 50),
    claim TEXT NOT NULL CHECK (char_length(btrim(claim)) BETWEEN 1 AND 2000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT essay_criteria_version_key UNIQUE (
        question_version_id, criterion_key
    ),
    CONSTRAINT essay_criteria_version_position_key UNIQUE (
        question_version_id, position
    ),
    CONSTRAINT essay_criteria_id_version_key UNIQUE (id, question_version_id)
);

COMMENT ON TABLE public.essay_criteria IS
    'Atomic criteria extracted once for an immutable essay question version.';

-- A release is append-only. The latest release id for a question is the active
-- version for new attempts. Existing submissions keep their stored version id.
CREATE TABLE public.essay_question_releases (
    id BIGSERIAL PRIMARY KEY,
    exam_question_id INTEGER NOT NULL
        REFERENCES public.exam_questions(id) ON DELETE RESTRICT,
    question_version_id BIGINT NOT NULL,
    released_by INTEGER NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    release_note TEXT CHECK (release_note IS NULL OR char_length(release_note) <= 2000),
    released_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT essay_question_releases_version_fkey
        FOREIGN KEY (question_version_id, exam_question_id)
        REFERENCES public.essay_question_versions(id, exam_question_id)
        ON DELETE RESTRICT
);

COMMENT ON TABLE public.essay_question_releases IS
    'Append-only publication history. Highest id per exam_question_id is active for new submissions.';


-- ---------------------------------------------------------------------------
-- Student evidence and append-only model runs
-- ---------------------------------------------------------------------------

CREATE TABLE public.essay_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    exam_attempt_id INTEGER NOT NULL
        REFERENCES public.exam_attempts(id) ON DELETE RESTRICT,
    exam_question_id INTEGER NOT NULL
        REFERENCES public.exam_questions(id) ON DELETE RESTRICT,
    question_version_id BIGINT NOT NULL,
    answer_text TEXT NOT NULL
        CHECK (char_length(btrim(answer_text)) BETWEEN 1 AND 50000),
    status VARCHAR(20) NOT NULL DEFAULT 'submitted' CHECK (
        status IN (
            'submitted', 'grading', 'graded', 'needs_review',
            'grading_failed', 'finalized'
        )
    ),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    grading_started_at TIMESTAMPTZ,
    graded_at TIMESTAMPTZ,
    finalized_at TIMESTAMPTZ,
    final_score NUMERIC(10, 2),
    final_review_id BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT essay_submissions_version_question_fkey
        FOREIGN KEY (question_version_id, exam_question_id)
        REFERENCES public.essay_question_versions(id, exam_question_id)
        ON DELETE RESTRICT,
    CONSTRAINT essay_submissions_attempt_question_key
        UNIQUE (exam_attempt_id, exam_question_id),
    CONSTRAINT essay_submissions_final_state_check CHECK (
        (
            status = 'finalized'
            AND final_score IS NOT NULL
            AND final_score >= 0
            AND final_review_id IS NOT NULL
            AND finalized_at IS NOT NULL
        )
        OR (
            status <> 'finalized'
            AND final_score IS NULL
            AND final_review_id IS NULL
            AND finalized_at IS NULL
        )
    )
);

COMMENT ON TABLE public.essay_submissions IS
    'Student essay evidence for one exam attempt and exact released question version. Answer text is immutable.';

CREATE TABLE public.essay_grading_runs (
    id BIGSERIAL PRIMARY KEY,
    submission_id UUID NOT NULL
        REFERENCES public.essay_submissions(id) ON DELETE RESTRICT,
    run_number INTEGER NOT NULL CHECK (run_number > 0),
    run_status VARCHAR(20) NOT NULL CHECK (
        run_status IN ('completed', 'needs_review', 'failed')
    ),

    evaluator_model_identifier TEXT,
    evaluator_prompt_version TEXT,
    evaluator_latency_ms INTEGER CHECK (
        evaluator_latency_ms IS NULL OR evaluator_latency_ms >= 0
    ),
    evaluator_input_tokens INTEGER CHECK (
        evaluator_input_tokens IS NULL OR evaluator_input_tokens >= 0
    ),
    evaluator_output_tokens INTEGER CHECK (
        evaluator_output_tokens IS NULL OR evaluator_output_tokens >= 0
    ),
    evaluator_raw_response TEXT,
    evaluator_parsed_response JSONB,
    evaluator_retry_count INTEGER NOT NULL DEFAULT 0
        CHECK (evaluator_retry_count >= 0),
    evaluator_retry_errors JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(evaluator_retry_errors) = 'array'),

    scoring_version VARCHAR(100) NOT NULL DEFAULT 'equal-weight-decimal-v1',
    max_points_snapshot NUMERIC(10, 2) NOT NULL
        CHECK (max_points_snapshot > 0),
    provisional_score NUMERIC(10, 2),
    needs_review BOOLEAN NOT NULL DEFAULT false,
    review_reason TEXT CHECK (review_reason IS NULL OR char_length(review_reason) <= 1000),
    error_code VARCHAR(100),
    error_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT essay_grading_runs_submission_number_key
        UNIQUE (submission_id, run_number),
    CONSTRAINT essay_grading_runs_state_check CHECK (
        (
            run_status = 'completed'
            AND needs_review = false
            AND evaluator_model_identifier IS NOT NULL
            AND evaluator_prompt_version IS NOT NULL
            AND evaluator_raw_response IS NOT NULL
            AND evaluator_parsed_response IS NOT NULL
            AND provisional_score IS NOT NULL
            AND provisional_score BETWEEN 0 AND max_points_snapshot
            AND error_code IS NULL
            AND error_detail IS NULL
        )
        OR (
            run_status = 'needs_review'
            AND needs_review = true
            AND review_reason IS NOT NULL
            AND evaluator_model_identifier IS NOT NULL
            AND evaluator_prompt_version IS NOT NULL
            AND evaluator_raw_response IS NOT NULL
            AND evaluator_parsed_response IS NOT NULL
            AND provisional_score IS NOT NULL
            AND provisional_score BETWEEN 0 AND max_points_snapshot
            AND error_code IS NULL
            AND error_detail IS NULL
        )
        OR (
            run_status = 'failed'
            AND provisional_score IS NULL
            AND evaluator_parsed_response IS NULL
            AND error_code IS NOT NULL
            AND error_detail IS NOT NULL
        )
    )
);

COMMENT ON TABLE public.essay_grading_runs IS
    'Append-only evaluator/scorer executions. A retry or regrade creates another run.';
COMMENT ON COLUMN public.essay_grading_runs.evaluator_raw_response IS
    'Provider structured response only; never hidden reasoning or chain-of-thought.';

CREATE TABLE public.essay_criterion_results (
    id BIGSERIAL PRIMARY KEY,
    grading_run_id BIGINT NOT NULL
        REFERENCES public.essay_grading_runs(id) ON DELETE RESTRICT,
    criterion_id BIGINT NOT NULL
        REFERENCES public.essay_criteria(id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL CHECK (
        status IN ('yes', 'partial', 'no', 'contradicted')
    ),
    evidence TEXT CHECK (evidence IS NULL OR char_length(evidence) <= 5000),
    reason TEXT NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 1 AND 1000),

    -- Store high-precision fixed-decimal arithmetic for audit. API presentation
    -- can round weight and awarded_points to two decimals; the run score is
    -- round(sum, 2). Ten decimal places makes the division rule explicit for
    -- denominators such as three that have no finite decimal representation.
    weight NUMERIC(20, 10) NOT NULL CHECK (weight > 0),
    status_factor NUMERIC(2, 1) NOT NULL,
    awarded_points NUMERIC(20, 10) NOT NULL CHECK (awarded_points >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT essay_criterion_results_run_criterion_key
        UNIQUE (grading_run_id, criterion_id),
    CONSTRAINT essay_criterion_results_factor_check CHECK (
        (status = 'yes' AND status_factor = 1.0)
        OR (status = 'partial' AND status_factor = 0.5)
        OR (status IN ('no', 'contradicted') AND status_factor = 0.0)
    ),
    CONSTRAINT essay_criterion_results_points_check CHECK (
        awarded_points = round(weight * status_factor, 10)
    )
);

COMMENT ON TABLE public.essay_criterion_results IS
    'One deterministic contribution for every criterion in a successful grading run.';


-- ---------------------------------------------------------------------------
-- Append-only faculty decisions
-- ---------------------------------------------------------------------------

CREATE TABLE public.essay_grade_reviews (
    id BIGSERIAL PRIMARY KEY,
    submission_id UUID NOT NULL
        REFERENCES public.essay_submissions(id) ON DELETE RESTRICT,
    grading_run_id BIGINT NOT NULL
        REFERENCES public.essay_grading_runs(id) ON DELETE RESTRICT,
    reviewer_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    decision VARCHAR(30) NOT NULL CHECK (
        decision IN ('approve_provisional', 'override_score', 'return_for_regrade')
    ),
    final_score NUMERIC(10, 2),
    notes TEXT CHECK (notes IS NULL OR char_length(notes) <= 10000),
    supersedes_review_id BIGINT
        REFERENCES public.essay_grade_reviews(id) ON DELETE RESTRICT,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT essay_grade_reviews_decision_check CHECK (
        (
            decision IN ('approve_provisional', 'override_score')
            AND final_score IS NOT NULL
            AND final_score >= 0
        )
        OR (
            decision = 'return_for_regrade'
            AND final_score IS NULL
        )
    )
);

COMMENT ON TABLE public.essay_grade_reviews IS
    'Append-only doctor decisions. Superseding a decision inserts another row; it never edits history.';

ALTER TABLE public.essay_submissions
    ADD CONSTRAINT essay_submissions_final_review_fkey
    FOREIGN KEY (final_review_id)
    REFERENCES public.essay_grade_reviews(id)
    ON DELETE RESTRICT;


-- ---------------------------------------------------------------------------
-- Cross-table invariants that CHECK constraints cannot express
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.validate_essay_question_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    question_type TEXT;
    nominal_points NUMERIC;
    owner_id INTEGER;
    creator_role TEXT;
BEGIN
    SELECT q.type, q.points, c.doctor_id, u.role
      INTO question_type, nominal_points, owner_id, creator_role
      FROM public.exam_questions q
      JOIN public.exams e ON e.id = q.exam_id
      JOIN public.courses c ON c.id = e.course_id
      JOIN public.users u ON u.id = NEW.created_by
     WHERE q.id = NEW.exam_question_id;

    IF question_type IS DISTINCT FROM 'essay' THEN
        RAISE EXCEPTION 'exam question % is not type essay', NEW.exam_question_id;
    END IF;
    IF NEW.max_points <> nominal_points THEN
        RAISE EXCEPTION
            'essay max_points % must equal exam_questions.points %',
            NEW.max_points, nominal_points;
    END IF;
    IF creator_role IS DISTINCT FROM 'doctor' OR NEW.created_by <> owner_id THEN
        RAISE EXCEPTION 'only the doctor owning the exam course may version an essay question';
    END IF;

    RETURN NEW;
END $$;

CREATE TRIGGER trg_validate_essay_question_version
BEFORE INSERT ON public.essay_question_versions
FOR EACH ROW EXECUTE FUNCTION public.validate_essay_question_version();


CREATE OR REPLACE FUNCTION public.validate_essay_question_release()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    version_status TEXT;
    criteria_count INTEGER;
    owner_id INTEGER;
    releaser_role TEXT;
    current_question_text TEXT;
    current_points NUMERIC;
    version_question_text TEXT;
    version_points NUMERIC;
BEGIN
    SELECT v.criteria_status, c.doctor_id, u.role, q.text, q.points,
           v.question_text, v.max_points
      INTO version_status, owner_id, releaser_role, current_question_text,
           current_points, version_question_text, version_points
      FROM public.essay_question_versions v
      JOIN public.exam_questions q ON q.id = v.exam_question_id
      JOIN public.exams e ON e.id = q.exam_id
      JOIN public.courses c ON c.id = e.course_id
      JOIN public.users u ON u.id = NEW.released_by
     WHERE v.id = NEW.question_version_id;

    SELECT count(*) INTO criteria_count
      FROM public.essay_criteria
     WHERE question_version_id = NEW.question_version_id;

    IF version_status NOT IN ('ready', 'needs_review') OR criteria_count = 0 THEN
        RAISE EXCEPTION 'only a successful criteria version may be released';
    END IF;
    IF version_status = 'needs_review'
       AND COALESCE(char_length(btrim(NEW.release_note)), 0) = 0
    THEN
        RAISE EXCEPTION 'releasing review-required criteria needs a doctor note';
    END IF;
    IF current_question_text IS DISTINCT FROM version_question_text
       OR current_points IS DISTINCT FROM version_points
    THEN
        RAISE EXCEPTION 'exam question text/points must match the version being released';
    END IF;
    IF releaser_role IS DISTINCT FROM 'doctor' OR NEW.released_by <> owner_id THEN
        RAISE EXCEPTION 'only the doctor owning the exam course may release this version';
    END IF;

    RETURN NEW;
END $$;

CREATE TRIGGER trg_validate_essay_question_release
BEFORE INSERT ON public.essay_question_releases
FOR EACH ROW EXECUTE FUNCTION public.validate_essay_question_release();


CREATE OR REPLACE FUNCTION public.validate_essay_submission()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    attempt_exam_id INTEGER;
    question_exam_id INTEGER;
    attempt_user_role TEXT;
    version_max NUMERIC(10, 2);
    review_submission UUID;
    review_score NUMERIC(10, 2);
BEGIN
    SELECT a.exam_id, u.role
      INTO attempt_exam_id, attempt_user_role
      FROM public.exam_attempts a
      JOIN public.users u ON u.id = a.user_id
     WHERE a.id = NEW.exam_attempt_id;

    SELECT q.exam_id, v.max_points
      INTO question_exam_id, version_max
      FROM public.exam_questions q
      JOIN public.essay_question_versions v
        ON v.exam_question_id = q.id
       AND v.id = NEW.question_version_id
     WHERE q.id = NEW.exam_question_id;

    IF attempt_exam_id IS DISTINCT FROM question_exam_id THEN
        RAISE EXCEPTION 'essay question does not belong to the attempted exam';
    END IF;
    IF attempt_user_role IS DISTINCT FROM 'student' THEN
        RAISE EXCEPTION 'essay submissions must belong to a student exam attempt';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.essay_question_releases
         WHERE question_version_id = NEW.question_version_id
    ) THEN
        RAISE EXCEPTION 'essay question version has not been released';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.answer_text IS DISTINCT FROM OLD.answer_text
           OR NEW.exam_attempt_id IS DISTINCT FROM OLD.exam_attempt_id
           OR NEW.exam_question_id IS DISTINCT FROM OLD.exam_question_id
           OR NEW.question_version_id IS DISTINCT FROM OLD.question_version_id
           OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
        THEN
            RAISE EXCEPTION 'submitted essay evidence and references are immutable';
        END IF;
    END IF;

    IF NEW.status = 'finalized' THEN
        SELECT r.submission_id, r.final_score
          INTO review_submission, review_score
          FROM public.essay_grade_reviews r
         WHERE r.id = NEW.final_review_id;

        IF review_submission IS DISTINCT FROM NEW.id
           OR review_score IS DISTINCT FROM NEW.final_score
           OR NEW.final_score > version_max
        THEN
            RAISE EXCEPTION 'final essay score must match a review for this submission and stay within max_points';
        END IF;
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END $$;

CREATE TRIGGER trg_validate_essay_submission
BEFORE INSERT OR UPDATE ON public.essay_submissions
FOR EACH ROW EXECUTE FUNCTION public.validate_essay_submission();

CREATE OR REPLACE FUNCTION public.validate_essay_criterion_result()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    run_version_id BIGINT;
    criterion_version_id BIGINT;
BEGIN
    SELECT s.question_version_id
      INTO run_version_id
      FROM public.essay_grading_runs r
      JOIN public.essay_submissions s ON s.id = r.submission_id
     WHERE r.id = NEW.grading_run_id;

    SELECT question_version_id
      INTO criterion_version_id
      FROM public.essay_criteria
     WHERE id = NEW.criterion_id;

    IF run_version_id IS DISTINCT FROM criterion_version_id THEN
        RAISE EXCEPTION 'criterion does not belong to the grading run question version';
    END IF;

    RETURN NEW;
END $$;

CREATE TRIGGER trg_validate_essay_criterion_result
BEFORE INSERT ON public.essay_criterion_results
FOR EACH ROW EXECUTE FUNCTION public.validate_essay_criterion_result();


CREATE OR REPLACE FUNCTION public.validate_complete_essay_grading_run()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    version_id BIGINT;
    expected_count INTEGER;
    result_count INTEGER;
    calculated_score NUMERIC(10, 2);
    version_max NUMERIC(10, 2);
    expected_weight NUMERIC(20, 10);
    wrong_weight_count INTEGER;
BEGIN
    SELECT s.question_version_id, v.max_points
      INTO version_id, version_max
      FROM public.essay_submissions s
      JOIN public.essay_question_versions v ON v.id = s.question_version_id
     WHERE s.id = NEW.submission_id;

    IF NEW.max_points_snapshot IS DISTINCT FROM version_max THEN
        RAISE EXCEPTION 'grading run max_points does not match its question version';
    END IF;

    SELECT count(*) INTO expected_count
      FROM public.essay_criteria
     WHERE question_version_id = version_id;

    IF expected_count > 0 THEN
        expected_weight := round(version_max / expected_count, 10);
    END IF;

    SELECT count(*), round(COALESCE(sum(awarded_points), 0), 2),
           count(*) FILTER (WHERE weight IS DISTINCT FROM expected_weight)
      INTO result_count, calculated_score, wrong_weight_count
      FROM public.essay_criterion_results
     WHERE grading_run_id = NEW.id;

    IF NEW.run_status = 'failed' THEN
        IF result_count <> 0 THEN
            RAISE EXCEPTION 'failed grading runs cannot contain criterion results';
        END IF;
    ELSIF expected_count = 0
       OR result_count <> expected_count
       OR wrong_weight_count <> 0
       OR calculated_score IS DISTINCT FROM NEW.provisional_score
    THEN
        RAISE EXCEPTION
            'grading run incomplete or score mismatch: expected %, got %, calculated %, stored %',
            expected_count, result_count, calculated_score, NEW.provisional_score;
    END IF;

    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_validate_complete_essay_grading_run
AFTER INSERT ON public.essay_grading_runs
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.validate_complete_essay_grading_run();


CREATE OR REPLACE FUNCTION public.validate_essay_submission_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'grading' AND NEW.grading_started_at IS NULL THEN
        RAISE EXCEPTION 'grading submission requires grading_started_at';
    ELSIF NEW.status = 'graded' THEN
        IF NEW.graded_at IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.essay_grading_runs
             WHERE submission_id = NEW.id AND run_status = 'completed'
        ) THEN
            RAISE EXCEPTION 'graded submission requires a completed run and graded_at';
        END IF;
    ELSIF NEW.status = 'needs_review' THEN
        IF NEW.graded_at IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.essay_grading_runs
             WHERE submission_id = NEW.id AND run_status = 'needs_review'
        ) THEN
            RAISE EXCEPTION 'review-required submission needs a review-required run and graded_at';
        END IF;
    ELSIF NEW.status = 'grading_failed' THEN
        IF NEW.graded_at IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.essay_grading_runs
             WHERE submission_id = NEW.id AND run_status = 'failed'
        ) THEN
            RAISE EXCEPTION 'failed submission requires a failed run and graded_at';
        END IF;
    END IF;

    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_validate_essay_submission_state
AFTER INSERT OR UPDATE ON public.essay_submissions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.validate_essay_submission_state();


CREATE OR REPLACE FUNCTION public.validate_essay_grade_review()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    run_submission UUID;
    run_score NUMERIC(10, 2);
    version_max NUMERIC(10, 2);
    owner_id INTEGER;
    reviewer_role TEXT;
    superseded_submission UUID;
BEGIN
    SELECT r.submission_id, r.provisional_score, v.max_points, c.doctor_id, u.role
      INTO run_submission, run_score, version_max, owner_id, reviewer_role
      FROM public.essay_grading_runs r
      JOIN public.essay_submissions s ON s.id = r.submission_id
      JOIN public.essay_question_versions v ON v.id = s.question_version_id
      JOIN public.exam_questions q ON q.id = s.exam_question_id
      JOIN public.exams e ON e.id = q.exam_id
      JOIN public.courses c ON c.id = e.course_id
      JOIN public.users u ON u.id = NEW.reviewer_id
     WHERE r.id = NEW.grading_run_id;

    IF run_submission IS DISTINCT FROM NEW.submission_id THEN
        RAISE EXCEPTION 'review grading run does not belong to this submission';
    END IF;
    IF reviewer_role IS DISTINCT FROM 'doctor' OR NEW.reviewer_id <> owner_id THEN
        RAISE EXCEPTION 'only the doctor owning the exam course may review this grade';
    END IF;
    IF NEW.final_score IS NOT NULL AND NEW.final_score > version_max THEN
        RAISE EXCEPTION 'review final_score exceeds question max_points';
    END IF;
    IF NEW.decision = 'approve_provisional'
       AND NEW.final_score IS DISTINCT FROM run_score
    THEN
        RAISE EXCEPTION 'approved score must equal the selected run provisional score';
    END IF;
    IF NEW.supersedes_review_id IS NOT NULL THEN
        SELECT submission_id INTO superseded_submission
          FROM public.essay_grade_reviews
         WHERE id = NEW.supersedes_review_id;
        IF superseded_submission IS DISTINCT FROM NEW.submission_id THEN
            RAISE EXCEPTION 'a review may supersede only a review of the same submission';
        END IF;
    END IF;

    RETURN NEW;
END $$;

CREATE TRIGGER trg_validate_essay_grade_review
BEFORE INSERT ON public.essay_grade_reviews
FOR EACH ROW EXECUTE FUNCTION public.validate_essay_grade_review();


CREATE OR REPLACE FUNCTION public.prevent_essay_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% rows are append-only; create a new version/run/review instead', TG_TABLE_NAME;
END $$;

CREATE TRIGGER trg_essay_question_versions_immutable
BEFORE UPDATE OR DELETE ON public.essay_question_versions
FOR EACH ROW EXECUTE FUNCTION public.prevent_essay_audit_mutation();
CREATE TRIGGER trg_essay_criteria_immutable
BEFORE UPDATE OR DELETE ON public.essay_criteria
FOR EACH ROW EXECUTE FUNCTION public.prevent_essay_audit_mutation();
CREATE TRIGGER trg_essay_question_releases_immutable
BEFORE UPDATE OR DELETE ON public.essay_question_releases
FOR EACH ROW EXECUTE FUNCTION public.prevent_essay_audit_mutation();
CREATE TRIGGER trg_essay_submissions_no_delete
BEFORE DELETE ON public.essay_submissions
FOR EACH ROW EXECUTE FUNCTION public.prevent_essay_audit_mutation();
CREATE TRIGGER trg_essay_grading_runs_immutable
BEFORE UPDATE OR DELETE ON public.essay_grading_runs
FOR EACH ROW EXECUTE FUNCTION public.prevent_essay_audit_mutation();
CREATE TRIGGER trg_essay_criterion_results_immutable
BEFORE UPDATE OR DELETE ON public.essay_criterion_results
FOR EACH ROW EXECUTE FUNCTION public.prevent_essay_audit_mutation();
CREATE TRIGGER trg_essay_grade_reviews_immutable
BEFORE UPDATE OR DELETE ON public.essay_grade_reviews
FOR EACH ROW EXECUTE FUNCTION public.prevent_essay_audit_mutation();


-- ---------------------------------------------------------------------------
-- Query paths and access boundary
-- ---------------------------------------------------------------------------

CREATE INDEX idx_essay_question_versions_question
    ON public.essay_question_versions (exam_question_id, version_number DESC);
CREATE INDEX idx_essay_criteria_version
    ON public.essay_criteria (question_version_id, position);
CREATE INDEX idx_essay_question_releases_active
    ON public.essay_question_releases (exam_question_id, id DESC);
CREATE INDEX idx_essay_submissions_attempt
    ON public.essay_submissions (exam_attempt_id);
CREATE INDEX idx_essay_submissions_question_status
    ON public.essay_submissions (exam_question_id, status, submitted_at DESC);
CREATE INDEX idx_essay_grading_runs_submission
    ON public.essay_grading_runs (submission_id, run_number DESC);
CREATE INDEX idx_essay_criterion_results_run
    ON public.essay_criterion_results (grading_run_id);
CREATE INDEX idx_essay_grade_reviews_submission
    ON public.essay_grade_reviews (submission_id, reviewed_at DESC, id DESC);
CREATE INDEX idx_essay_grade_reviews_reviewer
    ON public.essay_grade_reviews (reviewer_id, reviewed_at DESC);

ALTER TABLE public.essay_question_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.essay_criteria ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.essay_question_releases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.essay_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.essay_grading_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.essay_criterion_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.essay_grade_reviews ENABLE ROW LEVEL SECURITY;

-- Supabase commonly grants table access to these roles by default. RLS with no
-- policies already denies them, and explicit revoke is defense in depth. Keep
-- this migration portable to plain Postgres where those roles do not exist.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE
            public.essay_question_versions,
            public.essay_criteria,
            public.essay_question_releases,
            public.essay_submissions,
            public.essay_grading_runs,
            public.essay_criterion_results,
            public.essay_grade_reviews
        FROM anon;
        REVOKE ALL ON SEQUENCE
            public.essay_question_versions_id_seq,
            public.essay_criteria_id_seq,
            public.essay_question_releases_id_seq,
            public.essay_grading_runs_id_seq,
            public.essay_criterion_results_id_seq,
            public.essay_grade_reviews_id_seq
        FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE
            public.essay_question_versions,
            public.essay_criteria,
            public.essay_question_releases,
            public.essay_submissions,
            public.essay_grading_runs,
            public.essay_criterion_results,
            public.essay_grade_reviews
        FROM authenticated;
        REVOKE ALL ON SEQUENCE
            public.essay_question_versions_id_seq,
            public.essay_criteria_id_seq,
            public.essay_question_releases_id_seq,
            public.essay_grading_runs_id_seq,
            public.essay_criterion_results_id_seq,
            public.essay_grade_reviews_id_seq
        FROM authenticated;
    END IF;
END $$;

COMMIT;
