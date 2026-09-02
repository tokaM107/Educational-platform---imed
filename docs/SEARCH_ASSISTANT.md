# Catalog search assistant

`POST /api/search` accepts a public, unauthenticated natural-language query and
returns public catalog results. It can return only:

- published courses;
- doctors (`users.role = 'doctor'`);
- published books;
- active categories;
- active educational levels, unified from `pre_college_stages` and
  `college_stages`.

Lectures, transcripts, students, enrolments, entitlements, reports, questions,
drafts, archived records, and every other table are outside the search boundary.

## Request flow

1. The language model converts Arabic or English into a small `Plan`: result
   target, allow-listed filters, optional public-metadata text, ordering, and
   limit. The model never creates SQL.
2. `extract_info.validate()` drops hallucinated tables, columns, empty values,
   and non-filterable fields. An unsupported target becomes an `unsupported`
   response.
3. `search.build()` maps the remaining plan through a server-owned target map.
   All values use PostgreSQL parameters. The server itself adds visibility
   predicates such as `status = 'published'`, `is_active`, and
   `role = 'doctor'`; these protections do not depend on model behavior.
4. PostgreSQL returns zero, one, or several rows. The response outcome is
   respectively `none`, `go`, or `choose`. Vague supported requests produce
   `clarify`; out-of-scope requests produce `unsupported`.

Free text uses case-insensitive contains matching (`ILIKE`) against public
metadata only: titles, subtitles, descriptions, names, and category/level
labels. It never searches lecture transcripts.

## Access and limits

Search is intentionally public and has no per-user or per-IP request limit.
Other AI endpoints retain their existing authenticated daily quota.
