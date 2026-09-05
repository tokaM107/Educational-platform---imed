-- The transcription queue: one row per Bunny video, so a finished upload
-- transcribes itself exactly once on a RunPod Serverless GPU.
--
-- HANDOFF COPY: the authoritative executable migration lives in
-- educational-platform-db. Do not run this proposal as a second migration from
-- the FastAPI repository.
--
-- Ownership: FastAPI. This adds one new FastAPI-owned table and changes no
-- NestJS-owned or shared object, so it needs no cross-owner review — unlike
-- 20260901_essay_grading_production.sql, which alters a NestJS constraint.
--
-- Why a table and not a background task in the API process.
--
-- Transcription runs on a GPU this server does not have, and takes minutes. The
-- webhook's whole job is to write down that a video is ready; rag/worker.py
-- submits it to RunPod and settles the result later. A table is also what
-- survives the API or the worker restarting between the callback and the
-- transcript, which an in-process queue would not.
--
-- Why the key is the Bunny guid rather than the course_items id.
--
-- The guid is what the webhook carries, and the only identifier that exists at
-- the moment Bunny finishes encoding — the catalog row may not have been
-- written yet, since `course_items.video_ref` is filled in by the Nest API on
-- its own schedule. Keying on the guid lets the job be recorded immediately and
-- resolved to a video_id later.
--
-- `course_items` is NestJS-owned, so `video_id` deliberately carries no foreign
-- key: a FastAPI table must not be able to block a delete on a table another
-- service owns.
--
-- There is deliberately no `course_items` existence guard in this migration.
-- An earlier draft had one, copied from 20260901_llm_daily_usage.sql, but that
-- file guards on `public.users` because it declares a real REFERENCES to it —
-- the guard turns a confusing FK error into a readable one. This table
-- references nothing: `video_id` is a nullable plain integer and is *expected*
-- to be null for a while, because a webhook can arrive before Nest writes the
-- catalog row. The queue is therefore capable of existing, being written to and
-- being claimed with no `course_items` in the database at all, and a guard
-- would only make the migration fail on an environment where this table is
-- perfectly usable. The dependency is the application's (rag/worker.py resolves
-- video_ref), not the schema's.

BEGIN;

CREATE TABLE IF NOT EXISTS public.transcription_jobs (
    id BIGSERIAL PRIMARY KEY,

    -- The uniqueness that makes transcription happen exactly once. Bunny does
    -- not promise to deliver a webhook once — it retries, and it fires for
    -- several status transitions — so the endpoint inserts on every callback
    -- and lets this constraint collapse them into a single job. Without it a
    -- redelivered callback would start a second GPU run and re-embed the whole
    -- lecture.
    bunny_guid TEXT NOT NULL UNIQUE,

    -- Resolved from course_items.video_ref. Null while the catalog row does not
    -- exist yet; the worker retries the lookup rather than failing for good.
    -- Every chunk this pipeline writes carries it, which is what makes a
    -- transcript reachable from course-video retrieval.
    video_id INTEGER,

    --   pending     queued by the webhook, not yet submitted
    --   submitted   a worker owns it; RunPod has it once runpod_job_id is set
    --   processing  RunPod reports a GPU is on it
    --   completed   chunks are stored; terminal, never re-run by a webhook
    --   failed      retried while attempt_count < max_attempts, else terminal
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'submitted', 'processing',
                          'completed', 'failed')),

    -- RunPod's own id for the submitted job. Persisted rather than held in
    -- memory because it is what lets a worker restart resume an in-flight
    -- lecture instead of paying the GPU for it a second time.
    runpod_job_id TEXT,

    -- Counted when a job is claimed, not when it fails: a worker that dies
    -- without recording anything has still used an attempt, which is what
    -- stops a job that reliably kills its worker from being retried forever.
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),

    -- Per row rather than only in configuration, so raising the limit for one
    -- stubborn lecture does not raise it for every job in the table.
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),

    last_error TEXT,

    -- What the run produced and what it cost. Kept so "is this video
    -- searchable" is answerable without counting transcript_chunks, and so GPU
    -- configurations can be compared later: rtfx is
    -- audio_duration_seconds / gpu_processing_seconds.
    --
    -- All three are non-negative by construction — a count of chunks, a
    -- duration, and an elapsed time. The CHECKs exist so that a unit bug on
    -- the worker (a subtraction the wrong way round, a clock that went
    -- backwards) fails the write instead of being averaged into the RTFx
    -- figures these columns exist to produce.
    chunk_count INTEGER CHECK (chunk_count IS NULL OR chunk_count >= 0),
    audio_duration_seconds NUMERIC(10, 1)
        CHECK (audio_duration_seconds IS NULL OR audio_duration_seconds >= 0),
    gpu_processing_seconds NUMERIC(10, 2)
        CHECK (gpu_processing_seconds IS NULL OR gpu_processing_seconds >= 0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Stale-job recovery reads this column and nothing else, so it must move on
    -- every write. DEFAULT only covers the INSERT; the trigger below covers
    -- every UPDATE. See the note above the trigger.
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    submitted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

COMMENT ON TABLE public.transcription_jobs IS
    'FastAPI-owned queue of Bunny videos awaiting transcription on RunPod '
    'Serverless. One row per bunny_guid; the UNIQUE constraint is what makes a '
    'redelivered webhook a no-op rather than a second GPU run. RLS enabled '
    'with no policies.';

COMMENT ON COLUMN public.transcription_jobs.runpod_job_id IS
    'RunPod job id, so a worker restart resumes an in-flight transcription '
    'rather than re-submitting and paying twice.';

COMMENT ON COLUMN public.transcription_jobs.updated_at IS
    'Liveness clock for stale-job recovery: a job is reclaimed when no worker '
    'has touched it for TRANSCRIPTION_STALE_MINUTES. Maintained by trigger.';


-- ---------------------------------------------------------------
-- updated_at
-- ---------------------------------------------------------------
--
-- Every UPDATE in app/services/transcription_jobs.py already sets
-- `updated_at = now()` explicitly, and a test asserts it. This trigger is the
-- structural backstop, because the failure mode is expensive and silent: a
-- write that forgets it leaves the row's clock frozen, stale recovery reclaims
-- a job that is still running on the GPU, and the same lecture is transcribed
-- and billed twice. Nothing raises. The only visible symptom is a duplicate
-- RunPod charge and a re-embedded video.
--
-- Explicit sets are kept alongside it so each statement still reads as what it
-- does; the trigger is what makes it true regardless.

CREATE OR REPLACE FUNCTION public.transcription_jobs_touch_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_transcription_jobs_updated_at
    ON public.transcription_jobs;

CREATE TRIGGER trg_transcription_jobs_updated_at
BEFORE UPDATE ON public.transcription_jobs
FOR EACH ROW
EXECUTE FUNCTION public.transcription_jobs_touch_updated_at();


-- ---------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------
--
-- Each partial index is written to match one query's WHERE clause exactly, so
-- that the planner can walk it in order and stop at the first row rather than
-- collecting candidates and sorting them.
--
-- Verified on Postgres 17 with the queries in the block at the foot of this
-- file. The claim query plans as a single ordered Index Scan. Folding stale
-- recovery back into it as a third OR-branch instead produces a BitmapOr that
-- scans the claimable index twice and then sorts — which for an
-- ORDER BY … LIMIT 1 is the entire cost of the statement.

-- The claim query in app/services/transcription_jobs.py:CLAIM_SQL.
--
-- `attempt_count < max_attempts` is part of the predicate so that terminally
-- failed jobs — the ones that exhausted their attempts and will never be
-- claimed again — leave the index instead of accumulating in it forever. A
-- permanently broken video should cost nothing to skip past.
--
-- Note this is a partial index over a comparison of two columns, which is
-- allowed: the predicate is evaluated per row at write time. A row that
-- exhausts its attempts is removed from the index by the same UPDATE that
-- records the failure.
CREATE INDEX IF NOT EXISTS idx_transcription_jobs_claimable
    ON public.transcription_jobs (created_at)
    WHERE status = 'pending'
       OR (status = 'failed' AND attempt_count < max_attempts);

-- Two queries share this one, and both filter on exactly this predicate: the
-- settle pass (everything RunPod is holding) and RECOVER_STALE_SQL (those of
-- them whose worker has gone quiet). Ordered on updated_at because that is
-- what stale recovery compares against; the settle pass reads few enough rows
-- that its ORDER BY submitted_at is a sort of a handful of tuples.
CREATE INDEX IF NOT EXISTS idx_transcription_jobs_in_flight
    ON public.transcription_jobs (updated_at)
    WHERE status IN ('submitted', 'processing');

-- Answers "which catalog video is this" from the other direction, for the
-- status lookup and for re-runs driven by video id.
CREATE INDEX IF NOT EXISTS idx_transcription_jobs_video
    ON public.transcription_jobs (video_id)
    WHERE video_id IS NOT NULL;


-- ---------------------------------------------------------------
-- Access control
-- ---------------------------------------------------------------
--
-- This is an internal FastAPI work queue. It is not user data, no frontend
-- reads it, and nothing in it should ever be reachable from a browser session.
-- A row here names a Bunny guid and a RunPod job id, and a client able to
-- UPDATE one could set a job back to 'pending' and make the GPU re-transcribe
-- a lecture at will — a billing denial-of-service that needs no other access.
--
-- Same posture as every other FastAPI-owned table (llm_daily_usage, the essay_*
-- set): RLS on with no policies, which denies anon and authenticated outright,
-- while the service role the API connects as bypasses RLS as table owner.
-- The revokes are defence in depth against Supabase's default grants, and are
-- guarded so this file still loads on plain Postgres where the roles are absent.

ALTER TABLE public.transcription_jobs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE public.transcription_jobs FROM anon;
        -- The sequence as well as the table: INSERT rights are useless without
        -- nextval, but leaving it granted is an inconsistency that reads as an
        -- oversight the next time someone audits this.
        REVOKE ALL ON SEQUENCE public.transcription_jobs_id_seq FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE public.transcription_jobs FROM authenticated;
        REVOKE ALL ON SEQUENCE public.transcription_jobs_id_seq FROM authenticated;
    END IF;
END $$;

COMMIT;


-- ---------------------------------------------------------------
-- Verification — read-only, run after applying.
-- ---------------------------------------------------------------
--
-- 1. RLS is on and no policy grants anything back.
--
--   SELECT relrowsecurity, relforcerowsecurity
--   FROM pg_class WHERE oid = 'public.transcription_jobs'::regclass;
--   -- expect: relrowsecurity = true
--
--   SELECT count(*) FROM pg_policies
--   WHERE schemaname = 'public' AND tablename = 'transcription_jobs';
--   -- expect: 0
--
-- 2. anon and authenticated hold no privileges.
--
--   SELECT grantee, privilege_type
--   FROM information_schema.role_table_grants
--   WHERE table_name = 'transcription_jobs'
--     AND grantee IN ('anon', 'authenticated');
--   -- expect: no rows
--
-- 3. The claim index is actually used rather than politely ignored.
--
--   EXPLAIN SELECT id FROM public.transcription_jobs
--   WHERE status = 'pending'
--      OR (status = 'failed' AND attempt_count < max_attempts)
--   ORDER BY created_at LIMIT 1;
--   -- expect: Index Scan using idx_transcription_jobs_claimable
--   -- (on an empty table Postgres may prefer a seq scan; check again once
--   --  there are rows, or with SET enable_seqscan = off)
--
-- 4. The trigger maintains updated_at even when a statement forgets to.
--
--   INSERT INTO public.transcription_jobs (bunny_guid) VALUES ('trigger-test');
--   SELECT pg_sleep(1);
--   UPDATE public.transcription_jobs SET status = 'failed'
--   WHERE bunny_guid = 'trigger-test';               -- note: no updated_at
--   SELECT updated_at > created_at AS trigger_works
--   FROM public.transcription_jobs WHERE bunny_guid = 'trigger-test';
--   -- expect: true
--   DELETE FROM public.transcription_jobs WHERE bunny_guid = 'trigger-test';
