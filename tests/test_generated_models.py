"""The generated models are a drift canary — these tests check it still sings.

No database and no API key: everything here reads the committed file and the
mapper metadata built from it.
"""

import warnings

from sqlalchemy.exc import SAWarning

from app.db import _generated_models, models


# Every table the migrations are expected to define. A table dropped from the
# database without a migration disappears from the generated file, and this
# list is what notices.
EXPECTED_TABLES = {
    "courses",
    "enrollments",
    "lectures",
    "modules",
    "notifications",
    "password_reset_codes",
    "query_embeddings",
    "question_attempts",
    "questions",
    "refresh_tokens",
    "report_narratives",
    "reports",
    "subjects",
    "subscriptions",
    "topics",
    "transcript_chunks",
    "users",
    "video_events",
}


def test_every_expected_table_is_modelled():

    reflected = {table.name for table in models.Base.metadata.tables.values()}

    assert EXPECTED_TABLES <= reflected


def test_nothing_outside_the_public_schema_leaked_in():
    """Supabase owns `auth`; a class from it here means the generator regressed.

    `public.users.auth_user_id` carries a foreign key onto `auth.users`, and
    reflection follows it unless stopped. Left alone it puts Supabase's whole
    auth schema in the file, which then churns on every platform upgrade.
    """

    schemas = {table.schema for table in models.Base.metadata.tables.values()}

    assert schemas == {"public"}


def test_embedding_columns_kept_their_vector_type():
    """The retrieval path lives or dies on this.

    When the pgvector type registration is missing at generation time,
    sqlacodegen quietly emits NullType instead of failing, and the column that
    every similarity search depends on stops being a vector.
    """

    for model in (models.TranscriptChunks, models.QueryEmbeddings):

        column_type = model.embedding.type

        assert type(column_type).__name__ == "VECTOR"
        assert column_type.dim == 1536


def test_similarity_query_still_compiles_to_a_vector_operator():

    with warnings.catch_warnings():

        # The schema has deliberately overlapping foreign keys on `lectures`
        # (db/migrations/011 pins a lecture's doctor to its course's doctor),
        # so SQLAlchemy warns about the relationships reflected from them.
        # Nothing here maps rows, so the warning has nothing to be right about.
        warnings.simplefilter("ignore", SAWarning)

        sql = str(models.nearest_chunks([0.0] * 1536, limit=3))

    assert "<=>" in sql
    assert "transcript_chunks" in sql


def test_generated_file_says_not_to_edit_it():

    with open(_generated_models.__file__, encoding="utf-8") as handle:
        first_line = handle.readline()

    assert "DO NOT EDIT" in first_line
