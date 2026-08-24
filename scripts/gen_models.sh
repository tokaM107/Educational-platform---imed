#!/usr/bin/env bash
#
# Regenerate app/db/_generated_models.py from a database.
#
#   scripts/gen_models.sh                     # uses $DIRECT_DATABASE_URL, else $DATABASE_URL
#   scripts/gen_models.sh postgresql://...    # or an explicit one
#
# Which database you point this at matters.
#
# The file's contract is with supabase/migrations, not with whatever is live.
# CI regenerates it against a throwaway Postgres built out of the migration
# files and fails on any diff, so that is the reference. Generating against
# Supabase gives the same bytes only while the live schema and the migrations
# agree — which is exactly the thing being checked, and a difference here is a
# finding, not a mistake to paper over.
#
# On the connection string: introspection needs session mode. Supabase's
# pooler on port 5432 is session mode and works fine; port 6543 is transaction
# mode and cannot hold the catalogue queries reflection makes. The direct host
# (db.<ref>.supabase.co:5432) also works where it is reachable — it is
# IPv6-only on newer projects, which is why the pooler is the default here.

set -euo pipefail

cd "$(dirname "$0")/.."

OUTFILE=app/db/_generated_models.py

URL="${1:-${DIRECT_DATABASE_URL:-${DATABASE_URL:-}}}"

if [ -z "$URL" ] && [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
    URL="${DIRECT_DATABASE_URL:-${DATABASE_URL:-}}"
fi

if [ -z "$URL" ]; then
    echo "no database URL: pass one, or set DIRECT_DATABASE_URL / DATABASE_URL" >&2
    exit 2
fi

PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3

"$PYTHON" scripts/gen_models.py "$URL" --outfile "$OUTFILE"
