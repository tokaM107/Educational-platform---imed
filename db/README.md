# Frozen

Nothing in this directory is applied to any database any more.

The schema moved to
[educational-platform-db](https://github.com/tokaM107/educational-platform-db)
on 2026-08-24. The Supabase project behind it is shared with the NestJS API,
and a schema owned by one of its two consumers is a schema that drifts — which
it had already done, in three places, before anybody noticed. That repository's
`SCHEMA.md` says who owns which table and how to change one safely;
`docs/DRIFT-REPORT.md` there records what was found.

`schema.sql` and `migrations/001`–`014` stay here because of what is written in
them. The reasoning is the valuable part: why `users.password_hash` and
`users.phone` were left in place rather than dropped (012), why the foreign key
onto `auth.users` is added conditionally so the file loads on plain Postgres
too (013), why a lecture's doctor is pinned to its course's doctor with a
composite key (011). A `supabase db pull` baseline preserves none of that — it
is a snapshot of shape, with every reason stripped out.

So: read these files. Do not run them.

## What replaced what

| Was | Is now |
|---|---|
| `psql -f db/schema.sql` on a new database | `supabase db reset` in the schema repository |
| a new `db/migrations/0NN_*.sql` | `supabase migration new <name>` there |
| checking a change landed by hand | the `live schema drift` CI job, nightly |

## The two files that are not history

`verify_catalog.sql` and `verify_test_data.sql` are read-only queries for
checking the contents of a database, not its shape. They still work and are
still worth running. `ERD.md` and `erd.svg` are a hand-drawn picture of the
schema; they were already only as current as the last person to redraw them,
and the drift report is the honest account of what is actually there.
