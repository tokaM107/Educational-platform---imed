"""Gemini embeddings, shared by the ingest pipeline and the live API.

Two things here are not obvious and were both real bugs:

  - `contents=[str, str, ...]` is read by the API as ONE multi-part document
    and returns a single embedding for the whole list. Each text has to be
    wrapped in its own `types.Content`.
  - The free tier counts every text (not every HTTP call) against a limit of
    100 per minute, so bulk embedding has to be throttled.
"""

import time

from google import genai
from google.genai import errors
from google.genai import types
from pgvector import Vector

from app.config import get_settings


DOCUMENT = "RETRIEVAL_DOCUMENT"
QUERY = "RETRIEVAL_QUERY"


class Embedder:
    """Turns text into pgvector-ready vectors."""

    def __init__(self, settings=None, client=None):

        self.settings = settings or get_settings()

        self.client = client or genai.Client(
            api_key=self.settings.require_gemini_api_key()
        )

        # Rolling one-minute window used to stay under the quota
        self._window_start = time.monotonic()
        self._used = 0

    # -------------------------
    # Public API
    # -------------------------

    def embed_documents(self, texts, progress=None):
        """Embed transcript chunks (asymmetric: document side)."""

        return self._embed(texts, DOCUMENT, progress)

    def embed_query(self, text):
        """Embed a student question (asymmetric: query side)."""

        return self._embed([text], QUERY)[0]

    # -------------------------
    # Internals
    # -------------------------

    def _embed(self, texts, task_type, progress=None):

        if not texts:
            return []

        config = types.EmbedContentConfig(
            output_dimensionality=self.settings.embed_dim,
            task_type=task_type,
        )

        batch_size = self.settings.embed_batch_size
        vectors = []

        for start in range(0, len(texts), batch_size):

            batch = texts[start:start + batch_size]
            response = self._embed_batch(batch, config)

            if len(response.embeddings) != len(batch):
                raise RuntimeError(
                    f"asked for {len(batch)} embeddings, "
                    f"got {len(response.embeddings)}"
                )

            vectors.extend(Vector(item.values) for item in response.embeddings)

            if progress:
                progress(len(vectors), len(texts))

        return vectors

    def _embed_batch(self, batch, config, attempts=3):

        for attempt in range(attempts):

            self._wait_for_quota(len(batch))

            try:
                return self.client.models.embed_content(
                    model=self.settings.embed_model,
                    contents=[
                        types.Content(parts=[types.Part(text=text)])
                        for text in batch
                    ],
                    config=config,
                )

            except errors.ClientError as error:

                if getattr(error, "code", None) != 429 or attempt == attempts - 1:
                    raise

                time.sleep(60)
                self._reset_quota()

        raise RuntimeError("unreachable")

    def _wait_for_quota(self, size):

        elapsed = time.monotonic() - self._window_start

        if elapsed >= 60:
            self._reset_quota()

        elif self._used + size > self.settings.embed_rate_limit:

            time.sleep(max(60 - elapsed, 0))
            self._reset_quota()

        self._used += size

    def _reset_quota(self):

        self._window_start = time.monotonic()
        self._used = 0
