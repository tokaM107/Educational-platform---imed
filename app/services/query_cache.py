"""Cache for question embeddings.

Transcript chunks are embedded once, offline, by rag/ingest.py. The only thing
that still has to be embedded at request time is the student's question — a
vector search cannot compare a question to stored vectors without turning it
into one first.

So questions get stored too. The demo's suggested questions, a class of
students asking the same thing before an exam, and any retry all become a
lookup in `query_embeddings` instead of a Gemini call.

The key includes the model and the dimension: vectors from two different
models are not comparable, so a config change must miss the cache rather than
silently return nonsense.
"""

import hashlib
import logging
import re


logger = logging.getLogger(__name__)


def normalise(question):
    """Collapse whitespace so trivial spacing differences still hit the cache."""

    return re.sub(r"\s+", " ", (question or "").strip())


def cache_key(question):

    return hashlib.sha256(normalise(question).encode("utf-8")).hexdigest()


def get(conn, question, model, dim):
    """Stored vector for this question, or None."""

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT embedding
            FROM query_embeddings
            WHERE query_hash = %s AND model = %s AND dim = %s
            """,
            (cache_key(question), model, dim),
        )

        row = cur.fetchone()

    return row[0] if row else None


def put(conn, question, model, dim, embedding):
    """Store a freshly computed vector. Concurrent writers are harmless."""

    with conn.cursor() as cur:

        cur.execute(
            """
            INSERT INTO query_embeddings (query_hash, model, dim, query, embedding)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (query_hash, model, dim) DO NOTHING
            """,
            (cache_key(question), model, dim, normalise(question), embedding),
        )

    conn.commit()


def embed_query(conn, embedder, question):
    """Cached embed: look it up, and only call the API on a miss."""

    model = embedder.settings.embed_model
    dim = embedder.settings.embed_dim

    cached = get(conn, question, model, dim)

    if cached is not None:
        logger.debug("query embedding cache hit")
        return cached

    embedding = embedder.embed_query(question)

    put(conn, question, model, dim, embedding)

    return embedding
