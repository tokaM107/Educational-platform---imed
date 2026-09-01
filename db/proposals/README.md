# Database migration proposals

Files here are complete migration handoffs, but this repository does not own the
shared Supabase schema. Do not apply them directly from this checkout.

Copy the approved SQL into a migration created in `educational-platform-db`, run
that repository's reset/verification workflow, update both the NestJS and FastAPI
code in the same release plan, and regenerate `app/db/_generated_models.py` here.

`20260901_essay_grading_production.sql` changes the NestJS-owned exam question
type constraint and adds FastAPI-owned essay grading tables. It therefore needs
review by both service owners before it is applied.
