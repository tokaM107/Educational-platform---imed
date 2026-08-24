"""Hand-written companion to the generated models.

`_generated_models.py` is reflected from the database and overwritten wholesale
by `make db-gen`. Anything written by a person goes here instead, where a
regeneration cannot destroy it.

Neither module is on the request path. The API and the ingest pipeline talk to
Postgres through the psycopg pool in `app.db` and hand-written SQL; these
models exist so that CI has something that changes when the schema changes.
Keeping them out of the runtime is deliberate — an ORM that nothing queries
cannot be wrong about how a query behaves.

The API's own request and response shapes live in `app.schemas` and are not
derived from anything here. A column can be added, widened or renamed without
the HTTP contract following it around.
"""

from __future__ import annotations

from sqlalchemy import Select, select

from app.db._generated_models import (
    Base,
    Courses,
    Enrollments,
    Lectures,
    Modules,
    Notifications,
    PasswordResetCodes,
    QueryEmbeddings,
    QuestionAttempts,
    Questions,
    RefreshTokens,
    ReportNarratives,
    Reports,
    Subjects,
    Subscriptions,
    Topics,
    TranscriptChunks,
    Users,
    VideoEvents,
)


__all__ = [
    "Base",
    "Courses",
    "Enrollments",
    "Lectures",
    "Modules",
    "Notifications",
    "PasswordResetCodes",
    "QueryEmbeddings",
    "QuestionAttempts",
    "Questions",
    "RefreshTokens",
    "ReportNarratives",
    "Reports",
    "Subjects",
    "Subscriptions",
    "Topics",
    "TranscriptChunks",
    "Users",
    "VideoEvents",
    "nearest_chunks",
]


# Present in the database, absent from the generated models on purpose.
#
# `public.users.auth_user_id` really does carry a foreign key onto
# `auth.users`, added by db/migrations/013 wherever an `auth` schema exists.
# The generator drops it, because reflecting it would drag the whole of
# Supabase's auth schema into the output and make the file churn every time
# Supabase upgrades the platform. The constraint is enforced by the database;
# it is simply not modelled here. See scripts/gen_models.py.
UNMODELLED_CONSTRAINTS = ("users.users_auth_user_id_fkey",)


def nearest_chunks(embedding, limit: int = 5) -> Select:
    """Cosine-nearest transcript chunks to `embedding`.

    This is the one query the generated models are actually asked to express,
    and it is here for a narrow reason: it is the proof that regeneration kept
    `transcript_chunks.embedding` as a pgvector column. If sqlacodegen ever
    falls back to NullType for it — which is what happens when the pgvector
    type registration is lost — `cosine_distance` stops existing and the test
    in tests/test_generated_models.py fails loudly rather than the retrieval
    path degrading quietly in production.
    """

    distance = TranscriptChunks.embedding.cosine_distance(embedding)

    return (
        select(TranscriptChunks.id, TranscriptChunks.text_, distance.label("distance"))
        .order_by(distance)
        .limit(limit)
    )
