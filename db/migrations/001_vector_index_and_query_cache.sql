-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/001_vector_index_and_query_cache.sql

-- 1. Stop the similarity search from scanning every chunk.
CREATE INDEX IF NOT EXISTS idx_transcript_chunks_embedding
    ON transcript_chunks
    USING hnsw (embedding vector_cosine_ops);

-- 2. Cache the query side of the embeddings too, so a repeated question
--    costs a lookup instead of a Gemini call.
CREATE TABLE IF NOT EXISTS query_embeddings (
    id SERIAL PRIMARY KEY,
    query_hash CHAR(64) NOT NULL,
    model VARCHAR(64) NOT NULL,
    dim INTEGER NOT NULL,
    query TEXT NOT NULL,
    embedding vector(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (query_hash, model, dim)
);
