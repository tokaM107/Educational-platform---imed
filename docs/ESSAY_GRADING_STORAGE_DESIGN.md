# Production essay grading storage design

The SQL handoff is
[`db/proposals/20260901_essay_grading_production.sql`](../db/proposals/20260901_essay_grading_production.sql).
It is deliberately a proposal in this repository because the shared Supabase
schema is owned by `educational-platform-db` and the existing `exam_*` tables are
owned by the NestJS API.

## Data model

`exam_questions` remains the ordered exam question and gains the `essay` type.
The new tables form an append-only audit chain:

```text
exam_questions
  └─ essay_question_versions
       ├─ essay_criteria
       └─ essay_question_releases
            └─ essay_submissions ── exam_attempts
                 ├─ essay_grading_runs
                 │    └─ essay_criterion_results
                 └─ essay_grade_reviews
```

- A version freezes the displayed question, answer key, maximum points, criteria
  generator response, model, prompt version, usage, latency, retries and errors.
- A release makes a successful criteria version available. Releasing a version
  flagged for review requires an explicit doctor note. New releases never
  rewrite an old submission.
- A submission freezes the student's answer and links it to both the exam attempt
  and exact released version.
- Every evaluator retry or regrade is a separate grading run. Successful runs
  store exact `NUMERIC` weights, factors and awarded contributions for every
  criterion. Failed runs store no score.
- Reviews are append-only. A doctor may approve the provisional result, override
  it with notes, or request another run. A finalized submission points at the
  review that established its academic score.

## Important invariants

Database triggers enforce ownership and consistency across tables:

- Only the doctor owning the exam's course can create/release versions or review.
- Only released versions with generated criteria can receive submissions;
  review-required criteria need an explicit doctor release note.
- The student attempt, question and version must belong to the same exam.
- Student evidence, versions, criteria, releases, runs, results and reviews cannot
  be overwritten or deleted.
- Every successful run has exactly one result per criterion, no foreign criterion,
  and a score equal to `round(sum(weight * status_factor), 2)`.
- A final score must come from a review of that submission and cannot exceed the
  version's maximum points.
- RLS is enabled with no browser policies and `anon`/`authenticated` access is
  explicitly revoked where those Supabase roles exist.

## Coordinated application work still required

Do not apply the migration until both services are ready:

1. NestJS must accept `essay` questions, omit answer keys from student responses,
   store essay answers through `essay_submissions`, and avoid marking an exam final
   while an essay is pending review.
2. FastAPI must persist version generation, submissions, grading runs, criterion
   results and review state transactionally instead of returning demo-only data.
3. The owners must define how finalized essay points combine with the existing
   `exam_attempts.score` and `passed` fields. Their current score semantics are not
   changed by this migration because guessing whether that integer is points or a
   percentage would corrupt existing results.
4. Add retention/export rules for student answer text and model evidence.
5. Regenerate `app/db/_generated_models.py` after applying the upstream migration.

The migration intentionally stores provider structured responses for audit but
never chain-of-thought. Normal application logs must continue to exclude full
student and model answers.
