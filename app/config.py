"""Every tunable value in one place, read once from the environment.

Import `get_settings()` instead of calling os.getenv() around the codebase, so
the offline pipeline in `rag/` and the API in `app/` always agree on things
like the embedding model and its dimension.
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(name, default):
    """os.getenv, except that a variable set to nothing counts as unset.

    `KEY=` in a .env file is not the same as leaving KEY out: os.getenv finds
    the key, returns "", and the default never applies. For a string that is
    merely wrong (an empty algorithm name); for an int it is fatal, because
    int("") raises and the exception surfaces at import time as a stack trace
    from whichever module happened to touch Settings first.
    """

    value = os.getenv(name, "").strip()

    return value if value else default


class Settings:

    def __init__(self):

        # --- Infrastructure ---
        self.database_url = os.getenv("DATABASE_URL", "")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")

        # --- Models ---
        self.embed_model = os.getenv("EMBED_MODEL", "gemini-embedding-2")
        self.embed_dim = int(os.getenv("EMBED_DIM", "1536"))
        self.chat_model = os.getenv("CHAT_MODEL", "gemini-3.1-flash-lite")
        self.essay_criteria_model = env("ESSAY_CRITERIA_MODEL", self.chat_model)
        self.essay_evaluator_model = env("ESSAY_EVALUATOR_MODEL", self.chat_model)
        self.llm_daily_query_limit = int(env("LLM_DAILY_QUERY_LIMIT", "10"))

        if self.llm_daily_query_limit <= 0:
            raise ValueError("LLM_DAILY_QUERY_LIMIT must be positive")

        # Used when the primary model answers 429/503. Newer flash models get
        # busy; an older one is usually free and good enough for restating a
        # transcript.
        self.chat_fallback_model = os.getenv("CHAT_FALLBACK_MODEL", "gemini-2.5-flash")

        # --- Conversational RAG token budgets ---
        # Product limits are deliberately far below Gemini's context window to
        # bound latency and cost. The full prompt is checked against both.
        self.llm_context_window = int(env("LLM_CONTEXT_WINDOW", "1048576"))
        self.chat_max_input_tokens = int(env("CHAT_MAX_INPUT_TOKENS", "12000"))
        self.chat_max_output_tokens = int(env("CHAT_MAX_OUTPUT_TOKENS", "1200"))
        self.chat_safety_margin_tokens = int(
            env("CHAT_SAFETY_MARGIN_TOKENS", "500")
        )
        self.chat_rewrite_history_tokens = int(
            env("CHAT_REWRITE_HISTORY_TOKENS", "2000")
        )
        self.chat_answer_history_tokens = int(
            env("CHAT_ANSWER_HISTORY_TOKENS", "2500")
        )
        self.chat_summary_tokens = int(env("CHAT_SUMMARY_TOKENS", "1000"))
        self.chat_max_student_message_tokens = int(
            env("CHAT_MAX_STUDENT_MESSAGE_TOKENS", "1500")
        )
        self.chat_summary_update_threshold = int(
            env("CHAT_SUMMARY_UPDATE_THRESHOLD", "3500")
        )
        self.chat_rewrite_max_output_tokens = int(
            env("CHAT_REWRITE_MAX_OUTPUT_TOKENS", "160")
        )
        self.chat_summary_max_output_tokens = int(
            env("CHAT_SUMMARY_MAX_OUTPUT_TOKENS", "1000")
        )
        self.chat_history_load_limit = int(env("CHAT_HISTORY_LOAD_LIMIT", "100"))
        self.chat_retrieval_candidate_limit = int(
            env("CHAT_RETRIEVAL_CANDIDATE_LIMIT", "30")
        )
        self.chat_llm_timeout_seconds = float(env("CHAT_LLM_TIMEOUT_SECONDS", "30"))
        self.chat_prompt_resize_max_attempts = int(
            env("CHAT_PROMPT_RESIZE_MAX_ATTEMPTS", "8")
        )
        self.chat_token_count_retry_attempts = int(
            env("CHAT_TOKEN_COUNT_RETRY_ATTEMPTS", "2")
        )
        self.chat_token_count_retry_delay_seconds = float(
            env("CHAT_TOKEN_COUNT_RETRY_DELAY_SECONDS", "0.25")
        )

        if min(
            self.llm_context_window,
            self.chat_max_input_tokens,
            self.chat_max_output_tokens,
            self.chat_safety_margin_tokens,
            self.chat_max_student_message_tokens,
            self.chat_prompt_resize_max_attempts,
            self.chat_token_count_retry_attempts,
        ) <= 0:
            raise ValueError("chat token limits must all be positive")

        if (
            self.chat_max_input_tokens
            + self.chat_max_output_tokens
            + self.chat_safety_margin_tokens
            > self.llm_context_window
        ):
            raise ValueError(
                "CHAT_MAX_INPUT_TOKENS + CHAT_MAX_OUTPUT_TOKENS + "
                "CHAT_SAFETY_MARGIN_TOKENS exceeds LLM_CONTEXT_WINDOW"
            )

        # The free tier counts every text in a batch as one request and allows
        # 100 per minute, so the ingest pipeline has to pace itself.
        self.embed_batch_size = int(os.getenv("EMBED_BATCH_SIZE", "32"))
        self.embed_rate_limit = int(os.getenv("EMBED_RATE_LIMIT", "90"))

        # --- Retrieval ---
        self.top_k = int(os.getenv("TOP_K", "4"))

        # Cosine distance above which a hit is considered off-topic. Measured
        # on this lecture: real answers land at 0.22-0.30, unrelated chunks at
        # 0.50+. Anything past the cut-off means "not covered in the lecture"
        # and the tutor says so instead of guessing.
        self.max_distance = float(os.getenv("MAX_DISTANCE", "0.45"))

        # Two hits closer than this are treated as one continuous explanation
        # and merged into a single video segment.
        self.segment_merge_gap = int(os.getenv("SEGMENT_MERGE_GAP", "20"))

        # Start playback slightly before the chunk so the sentence is not
        # already half-spoken when the video starts.
        self.segment_lead_in = int(os.getenv("SEGMENT_LEAD_IN", "3"))

        # --- Access ---

        # Whether a lecture video requires a subscription to its teacher.
        #
        # This is now a real boundary. The student it checks comes from the
        # verified token rather than from a request parameter, so it can no
        # longer be walked past by sending somebody else's id. Turning it off
        # unlocks every video for every logged-in user, which is a development
        # convenience and not something to leave off in production.
        self.enforce_subscriptions = (
            os.getenv("ENFORCE_SUBSCRIPTIONS", "true").strip().lower()
            not in ("0", "false", "no", "off")
        )

        # The static browser client is a local-development convenience.  It is
        # deliberately opt-in so an API-only deployment neither publishes the
        # demo routes nor depends on the static directory being present.
        self.enable_demo_ui = (
            os.getenv("ENABLE_DEMO_UI", "false").strip().lower()
            in ("1", "true", "yes", "on")
        )
        self.enable_grading_demo_ui = (
            os.getenv("ENABLE_GRADING_DEMO_UI", "false").strip().lower()
            in ("1", "true", "yes", "on")
        )

        # --- Weekly report ---

        # A week is seven *local* days. In UTC, a 23:00 Cairo session lands on
        # the next day, which silently moves study onto the wrong day and can
        # push it out of the reported week altogether. Any IANA zone name works;
        # an unknown one falls back to UTC with a warning.
        self.report_timezone = os.getenv("REPORT_TIMEZONE", "Africa/Cairo")
        self.report_week_days = int(os.getenv("REPORT_WEEK_DAYS", "7"))

        # --- Chunking (offline pipeline) ---
        self.chunk_words = int(os.getenv("CHUNK_WORDS", "120"))
        self.overlap_words = int(os.getenv("OVERLAP_WORDS", "25"))

        # --- Auth ---

        # Nest signs user access tokens with this shared secret. It must be the
        # exact value of JWT_ACCESS_SECRET in the Nest deployment and must never
        # be exposed to a browser. Supabase verification keeps using that
        # project's public JWKS through app/services/supabase_client.py.
        self.nest_jwt_access_secret = env("NEST_JWT_ACCESS_SECRET", "")

        # --- Bunny Stream ---
        #
        # Where the lecture videos live. The library id and key come from the
        # Stream library's API tab; the hostname is its pull zone, and is the
        # only one of the three that is safe in a browser — the key has write
        # access to every video in the library.
        self.bunny_library_id = env("BUNNY_STREAM_LIBRARY_ID", "")
        self.bunny_api_key = env("BUNNY_STREAM_API_KEY", "")
        self.bunny_cdn_hostname = env("BUNNY_STREAM_CDN_HOSTNAME", "")

        # --- Paths ---
        self.base_dir = BASE_DIR
        self.data_dir = BASE_DIR / "data"
        self.video_dir = self.data_dir / "videos"
        self.transcript_dir = self.data_dir / "transcripts"
        self.static_dir = BASE_DIR / "app" / "static"

    def require_database_url(self):

        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not set (see .env)")

        return self.database_url

    def require_gemini_api_key(self):

        if not self.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (see .env)")

        return self.gemini_api_key

    def require_nest_jwt_access_secret(self):

        if not self.nest_jwt_access_secret:
            raise RuntimeError("NEST_JWT_ACCESS_SECRET is not set (see .env)")

        if len(self.nest_jwt_access_secret) < 32:
            raise RuntimeError("NEST_JWT_ACCESS_SECRET must be at least 32 characters")

        return self.nest_jwt_access_secret

    def require_bunny(self):
        """(library_id, api_key, cdn_hostname), or a refusal naming what is missing.

        All three together, because a pipeline that uploads with two of them and
        then cannot work out where to read the result from has already spent the
        upload by the time it finds out.
        """

        missing = [
            name
            for name, value in (
                ("BUNNY_STREAM_LIBRARY_ID", self.bunny_library_id),
                ("BUNNY_STREAM_API_KEY", self.bunny_api_key),
                ("BUNNY_STREAM_CDN_HOSTNAME", self.bunny_cdn_hostname),
            )
            if not value
        ]

        if missing:
            raise RuntimeError(f"{', '.join(missing)} not set (see .env)")

        return (
            self.bunny_library_id,
            self.bunny_api_key,
            self.bunny_cdn_hostname.strip().rstrip("/"),
        )


@lru_cache
def get_settings():
    """Cached so every module shares one Settings instance."""

    return Settings()
