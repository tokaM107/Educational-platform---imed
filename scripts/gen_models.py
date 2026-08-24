"""Reflect a Postgres database into SQLAlchemy models.

The output is a drift canary, not runtime code. Nothing in `app/` imports it.
Its only job is to change when the database changes, so that CI can notice a
schema that moved without a migration behind it.

Run it through `scripts/gen_models.sh`, or `make db-gen`.

Two things this does that plain `sqlacodegen` on the command line does not:

`--schemas public` is not enough on Supabase. `public.users.auth_user_id` has a
foreign key onto `auth.users`, and SQLAlchemy reflects the target of a foreign
key whether or not its schema was asked for. The whole of Supabase's `auth`
schema lands in the output, and it changes whenever Supabase upgrades the
platform underneath us — a diff nobody in this repository caused. Foreign keys
pointing outside `public` are therefore dropped before the code is generated.
That is also what makes the output identical whether it was reflected from
Supabase or from a throwaway database built out of the migrations: the plain
Postgres container CI uses has no `auth` schema, so migration 013's conditional
`DO` block never adds that constraint in the first place.

Importing `pgvector.sqlalchemy` registers `vector` in the dialect's type map.
Without it reflection fails on `transcript_chunks.embedding`, and the column
that the entire retrieval path depends on would silently not be modelled.
"""

from __future__ import annotations

import argparse
import sys

import pgvector.sqlalchemy  # noqa: F401  (registers the `vector` column type)
from sqlacodegen.generators import DeclarativeGenerator
from sqlalchemy import Engine, MetaData, create_engine


HEADER = """\
# AUTO-GENERATED — DO NOT EDIT — run `make db-gen`
#
# Reflected from the database by scripts/gen_models.py. Every hand-written
# addition — relationships, helpers, business logic, the pgvector overrides —
# belongs in app/db/models.py, which imports from here.
#
# CI regenerates this file against a database built from the migrations and
# fails if the result differs from what is committed. A diff here means the
# database moved and the migrations did not, or the other way round.
"""

SCHEMA = "public"


def _sqlalchemy_url(url: str) -> str:
    """psycopg 3 is the driver this project already has; name it explicitly."""

    if url.startswith("postgresql+"):
        return url

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    return "postgresql+psycopg://" + url[len("postgresql://") :]


def _drop_foreign_schema_keys(metadata: MetaData) -> list[str]:
    """Remove foreign keys whose target lives outside `public`.

    Dropping the constraint is only half of it. Reflecting a foreign key also
    pulls its target table into the metadata, and a table left there is a class
    in the output. Both have to go.

    Returns what was dropped, so the caller can say so out loud rather than
    quietly producing a model that is missing a constraint.
    """

    dropped = []

    for table in list(metadata.tables.values()):
        for constraint in list(table.foreign_key_constraints):
            targets = {fk.column.table.schema for fk in constraint.elements}

            if targets - {SCHEMA, None}:
                dropped.append(f"{table.name}.{constraint.name}")
                table.constraints.discard(constraint)

                for fk in constraint.elements:
                    fk.parent.foreign_keys.discard(fk)
                    table.foreign_keys.discard(fk)

    for table in list(metadata.tables.values()):
        if table.schema != SCHEMA:
            dropped.append(f"table {table.schema}.{table.name}")
            metadata.remove(table)

    return dropped


def generate(engine: Engine) -> str:

    metadata = MetaData()

    with engine.connect() as connection:

        metadata.reflect(bind=connection, schema=SCHEMA)

        dropped = _drop_foreign_schema_keys(metadata)

        for name in dropped:
            print(f"dropped cross-schema foreign key: {name}", file=sys.stderr)

        code = DeclarativeGenerator(metadata, connection, options=()).generate()

    return HEADER + "\n" + code


def main() -> None:

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Postgres connection string")
    parser.add_argument("--outfile", required=True)

    args = parser.parse_args()

    engine = create_engine(_sqlalchemy_url(args.url))

    try:
        code = generate(engine)
    finally:
        engine.dispose()

    with open(args.outfile, "w", encoding="utf-8") as handle:
        handle.write(code)

    print(f"wrote {args.outfile}", file=sys.stderr)


if __name__ == "__main__":
    main()
