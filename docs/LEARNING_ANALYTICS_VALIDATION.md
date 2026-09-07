# Learning analytics production validation

Validated 2026-09-07 against the persisted local Supabase development database
(PostgreSQL 17), built from the authoritative `way2APlus_db` migration chain.
No in-memory or mock rows were used for these results. The configured hosted
Supabase database was inspected read-only; it was not mutated.

## Architecture found

- Production content is in `course_items`; its 86 `video_events` and 24
  `transcript_chunks` use `video_id`. The legacy `lectures` table currently has
  zero hosted rows. The prior report read legacy lectures only.
- Watch/session/visibility data is reconstructed from raw `video_events`.
- Legacy quiz attempts live in `question_attempts`; modern exams have
  `exam_attempts` but previously lacked durable per-question result rows.
- RAG chat is persisted in `chat_sessions`/`chat_messages`, scoped to either a
  legacy lecture or a course-item video.
- Weekly narrative text is cached; numerical report data is deterministic.

The upgrade reads both legacy lectures and modern course-item videos, performs
bounded bulk reads (not one query per lecture), and stores a versioned weekly
snapshot with a source fingerprint.

## Migration

Authoritative migration:
`way2APlus_db/supabase/migrations/20260907010000_production_learning_analytics.sql`

FastAPI review handoff:
`db/proposals/20260907_production_learning_analytics.sql`

It adds `checkpoint_questions`, `checkpoint_attempts`,
`student_study_sessions`, `course_weekly_assignments`,
`assessment_question_results`, `retention_assessment_results`, and
`weekly_analytics_snapshots`; adds playback rate/idempotency to video events;
and adds deterministic topic/objective/cognitive metadata hooks. All new
private learning tables have RLS enabled with no Data API policies.

The complete ordered migration chain and `supabase/ci/verify.sql` both passed
against the local database. The migration was also reapplied successfully to
verify idempotency.

## Persisted validation rows

Namespace: `[ANALYTICS-VALIDATION]`, week `2026-09-01` through `2026-09-07`.

| Object | IDs |
|---|---:|
| doctor / student / empty-evidence student | 4 / 5 / 6 |
| course / course-item video / topic | 2 / 2 / 2 |
| checkpoint questions | 5, 6, 7, 8 |

The validation script is idempotent and never deletes or updates unrelated
rows: `python -m scripts.validate_learning_analytics --database-url ...`.

## SQL reconciliation

Checkpoint reconciliation query:

```sql
SELECT count(*) AS shown,
       count(*) FILTER (WHERE answered_at IS NOT NULL) AS answered,
       count(*) FILTER (WHERE is_correct) AS correct,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY response_time_ms)
           FILTER (WHERE answered_at IS NOT NULL) AS median_ms
FROM checkpoint_attempts
WHERE student_id = 5 AND course_id = 2
  AND shown_at >= '2026-08-31T21:00:00Z'
  AND shown_at <  '2026-09-07T21:00:00Z';
```

Result: `shown=4, answered=3, correct=2, median_ms=10000`.

Assessment reconciliation query over the same student/course/week returned
`questions=3, correct=2` from `assessment_question_results`.

The generated report returned:

- unique coverage: 16.7%; actual watch time: 220 seconds; active days: 2;
- checkpoint accuracy: 66.7%; checkpoint completion: 75.0%;
- assessment accuracy: 66.7%;
- mastery: 64.2%, MEDIUM confidence;
- previous → current coverage: 10.0% → 16.7% (+6.7 pp);
- previous → current active days: 1 → 2;
- expected progress at validation time: 96.8%; actual assigned progress: 16.7%; gap: -80.1 pp;
- immediate evidence: 100%; delayed retention: 0%; change: -100 pp.

The deliberately enrolled second student had zero video/checkpoint/assessment
evidence, `mastery=null`, and no fabricated retention or quiz score. One and
only one versioned snapshot row existed after repeated report generation.

## PDF check

Headless Chrome rendered the persisted-data report on explicit A4 paper. The
PDF page tree reported `/Count 3`. The initial render without explicit A4
reported six pages; that defect was fixed by adding `size: A4` to `@page`.

Temporary validation artifacts:
`/tmp/learning-analytics-report.html` and
`/tmp/learning-analytics-report.pdf`.

## Automated checks

The final full regression run passed: `752 passed, 20 skipped`. A focused rerun
after adding the modern-video checkpoint route passed all 57 report,
checkpoint, engagement, and generated-schema tests. JavaScript syntax checks,
Python byte-compilation, migration verification, and `git diff --check` also
passed.
