-- Shared daily budget for authenticated LLM API requests.
--
-- HANDOFF COPY: the authoritative executable migration lives in way2APlus_db.
-- Do not run this proposal as a second migration from the FastAPI repository.
--
-- The application reserves quota atomically before provider work. UTC dates
-- keep every worker on the same reset boundary.
BEGIN;

DO $$
BEGIN
    IF to_regclass('public.users') IS NULL THEN
        RAISE EXCEPTION 'llm daily usage migration requires public.users';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.llm_daily_usage (
    user_id INTEGER NOT NULL
        REFERENCES public.users(id) ON DELETE CASCADE,
    usage_date DATE NOT NULL DEFAULT
        ((CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date),
    query_count INTEGER NOT NULL CHECK (query_count > 0),
    feature_counts JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(feature_counts) = 'object'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, usage_date)
);

CREATE INDEX IF NOT EXISTS idx_llm_daily_usage_date
    ON public.llm_daily_usage (usage_date DESC);

COMMENT ON TABLE public.llm_daily_usage IS
    'FastAPI-owned atomic per-user LLM request totals by UTC day. RLS enabled with no policies.';
COMMENT ON COLUMN public.llm_daily_usage.feature_counts IS
    'Aggregate request units by controlled feature name; contains no prompt or answer text.';

ALTER TABLE public.llm_daily_usage ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE public.llm_daily_usage FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE public.llm_daily_usage FROM authenticated;
    END IF;
END $$;

COMMIT;
