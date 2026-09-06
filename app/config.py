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

        # Additional pull zones, comma-separated, for a deployment serving more
        # than one. Read on both sides so the two allowlists stay identical.
        self.bunny_media_hosts_extra = env("BUNNY_MEDIA_HOSTS", "")

        # Bunny Stream webhooks carry no signature, so the only thing that
        # distinguishes a real callback from anybody who guessed the path is a
        # secret we put in the URL ourselves. Unset means the endpoint refuses
        # every request rather than trusting the internet.
        self.bunny_webhook_secret = env("BUNNY_WEBHOOK_SECRET", "")

        # --- Transcription ---
        #
        # Which ASR the worker uses.
        #
        #   runpod  RunPod Serverless. The GPU starts when a job arrives and
        #           scales back to zero when the queue empties, so nothing is
        #           billed between lectures. What production uses: this server
        #           needs no GPU, no ffmpeg and no model weights of its own.
        #   cohere  the same model loaded in this process. Needs a GPU and the
        #           full requirements.txt — the workstation/benchmark path.
        self.asr_backend = env("ASR_BACKEND", "runpod")

        # RunPod Serverless. The endpoint id is the one in the endpoint's URL;
        # the API key is account-wide and must never reach a browser.
        self.runpod_api_key = env("RUNPOD_API_KEY", "")
        self.runpod_endpoint_id = env("RUNPOD_ENDPOINT_ID", "")

        # RunPod's REST base. Configurable only so tests can point it at a
        # local stub; there is no reason to change it in a deployment.
        self.runpod_api_base = env("RUNPOD_API_BASE", "https://api.runpod.ai/v2")

        # How often the worker asks RunPod whether a submitted job has
        # finished. The job is minutes long, so a tight poll is all overhead.
        self.runpod_poll_interval_seconds = int(
            env("RUNPOD_POLL_INTERVAL_SECONDS", "15")
        )

        # How long a job may stay unfinished on RunPod before this side gives
        # up on it and retries. Measured from submission, not from claim.
        self.runpod_job_timeout_seconds = int(
            env("RUNPOD_JOB_TIMEOUT_SECONDS", "3600")
        )

        # How long a single HTTP call to RunPod may hang. Submitting and
        # polling are both quick calls — this is not the transcription budget.
        self.runpod_request_timeout_seconds = int(
            env("RUNPOD_REQUEST_TIMEOUT_SECONDS", "60")
        )

        # The model the GPU worker loads. Here as well as on the worker so the
        # two cannot silently disagree about which transcript they produce.
        self.cohere_transcribe_model = env(
            "COHERE_TRANSCRIBE_MODEL", "CohereLabs/cohere-transcribe-arabic-07-2026"
        )

        # A job that has failed this many times stops being retried. Something
        # that broke three times is broken in a way another attempt will not
        # fix, and an unbounded retry on a metered GPU bills for every one.
        self.transcription_max_attempts = int(env("TRANSCRIPTION_MAX_ATTEMPTS", "3"))

        # How long a job may sit claimed but unfinished before another worker
        # may take it. Crash recovery, not a limit on how long a lecture takes:
        # a worker killed mid-poll would otherwise hold its job forever.
        self.transcription_stale_minutes = int(
            env("TRANSCRIPTION_STALE_MINUTES", "120")
        )

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

    def require_bunny_webhook_secret(self):

        if not self.bunny_webhook_secret:
            raise RuntimeError("BUNNY_WEBHOOK_SECRET is not set (see .env)")

        return self.bunny_webhook_secret

    def require_runpod(self):
        """(endpoint_base, api_key), or a refusal naming what is missing.

        Both together: an endpoint id without a key cannot authenticate, and a
        key without an endpoint id has nowhere to go. Finding out one at a time
        means a job is claimed, fails, and burns an attempt before the
        configuration problem is visible.
        """

        missing = [
            name
            for name, value in (
                ("RUNPOD_API_KEY", self.runpod_api_key),
                ("RUNPOD_ENDPOINT_ID", self.runpod_endpoint_id),
            )
            if not value
        ]

        if missing:
            raise RuntimeError(f"{', '.join(missing)} not set (see .env)")

        base = self.runpod_api_base.strip().rstrip("/")

        return f"{base}/{self.runpod_endpoint_id.strip()}", self.runpod_api_key

    def bunny_media_hosts(self):
        """Hostnames the GPU worker is allowed to fetch media from.

        The worker is handed a URL and downloads it on a machine holding a
        Hugging Face token and an API key, so an unchecked URL is a
        server-side request forgery with a GPU attached. Only the configured
        pull zone qualifies, and `video.bunnycdn.com` for API-issued links.

        Built by rag.media_url rather than here, so this side and the GPU
        worker cannot disagree about what counts as a Bunny host. They did:
        this read BUNNY_STREAM_CDN_HOSTNAME and the worker read
        BUNNY_MEDIA_HOSTS, so the server submitted lectures the worker then
        refused to fetch.
        """

        from rag import media_url

        return media_url.bunny_hosts(
            self.bunny_cdn_hostname, self.bunny_media_hosts_extra
        )

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
