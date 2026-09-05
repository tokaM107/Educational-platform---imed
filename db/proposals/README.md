# Database migration proposals

Files here are complete migration handoffs, but this repository does not own the
shared Supabase schema. Do not apply them directly from this checkout.

Copy the approved SQL into a migration created in `educational-platform-db`, run
that repository's reset/verification workflow, update both the NestJS and FastAPI
code in the same release plan, and regenerate `app/db/_generated_models.py` here.

`20260901_essay_grading_production.sql` changes the NestJS-owned exam question
type constraint and adds FastAPI-owned essay grading tables. It therefore needs
review by both service owners before it is applied.

`20260905_transcription_jobs.sql` adds the FastAPI-owned `transcription_jobs`
queue that turns a finished Bunny upload into exactly one transcription. It
touches no NestJS-owned or shared table, so it needs only the FastAPI owner —
but it must still be applied from `educational-platform-db`, not from here.
