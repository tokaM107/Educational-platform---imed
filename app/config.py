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


class Settings:

    def __init__(self):

        # --- Infrastructure ---
        self.database_url = os.getenv("DATABASE_URL", "")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")

        # --- Models ---
        self.embed_model = os.getenv("EMBED_MODEL", "gemini-embedding-2")
        self.embed_dim = int(os.getenv("EMBED_DIM", "1536"))
        self.chat_model = os.getenv("CHAT_MODEL", "gemini-3.7-flash")

        # Used when the primary model answers 429/503. Newer flash models get
        # busy; an older one is usually free and good enough for restating a
        # transcript.
        self.chat_fallback_model = os.getenv("CHAT_FALLBACK_MODEL", "gemini-2.5-flash")

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

        # --- Chunking (offline pipeline) ---
        self.chunk_words = int(os.getenv("CHUNK_WORDS", "120"))
        self.overlap_words = int(os.getenv("OVERLAP_WORDS", "25"))

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


@lru_cache
def get_settings():
    """Cached so every module shares one Settings instance."""

    return Settings()
