# Teacher assessment analytics

## Architecture and persisted sources

`GET /api/exams/{exam_id}` is a doctor-only, read-only aggregation. The route
checks `exams -> courses.doctor_id` before reading the cohort and returns 404 on
failure, preventing sequential-ID discovery. The service performs bounded bulk
reads, not queries per student or question.

| Output | Persisted source |
|---|---|
| exam, pass mark, ownership | `exams JOIN courses` |
| enrolled denominator and identities | active, unexpired `enrollments JOIN users` |
| starts, completion, duration, legacy score fallback | `exam_attempts` |
| question/topic/objective/options | `exam_questions LEFT JOIN topics LEFT JOIN exam_options` |
| answers, retries, skips/timeouts, response time | `assessment_question_attempts` |

Migration `20260909120000_assessment_analytics.sql` adds nullable
`exam_questions.topic_id`, `learning_objective`, and `difficulty`, plus the raw
`assessment_question_attempts` table. No derived metric is stored. Scope foreign
keys and a trigger enforce matching exam/student/question ownership. RLS is
enabled and Data API grants are revoked.

## Formulas and rules

- Participation = enrolled students with an exam attempt / active enrolled.
  Completion = students with a submitted attempt / started. Abandonment =
  started but not submitted / started.
- Score = final correct responses / all exam questions; unanswered is wrong.
  Attempted accuracy = final correct / answered. Pre-migration rows may use
  persisted awarded/max points, but item metrics stay unavailable.
- First and final accuracy use the earliest and latest persisted response per
  student/question. Retry gain = final - first in percentage points.
- Duration = `submitted_at - started_at`; response time uses persisted
  milliseconds. Mean, median, P25 and P75 use continuous interpolation.
- Difficulty: easy >= 80%, moderate >= 50% and < 80%, difficult < 50%.
- Discrimination: Pearson point-biserial correlation between final binary item
  result and the score on all other items. The item is excluded. It is absent
  below 10 students or at zero variance. Good >= .20, weak 0–<.20, negative < 0.
- Wrong options: non-functioning below 5%; strong misconception at >= 25%;
  suspicious when selected >= 20 percentage points more by high performers,
  with at least five students in each comparison group.
- Review priority: difficulty severity 35 points; negative/weak discrimination
  25/12; concentrated/suspicious distractor 15/10; retry rate up to 15;
  skip/timeout rate up to 15; median response time above exam P75 10. High >=
  50, medium >= 25, otherwise low. Every flag includes reasons.
- Topics are conclusive at >= 2 questions and >= 10 response events. General
  confidence is HIGH at >= 30 students and >= 60 events, MEDIUM at >= 10 and >=
  20, otherwise LOW.
- Support segments use completion, score, attempted accuracy, first-attempt
  accuracy, retries, and conclusive weak topics. Missing detail is “Incomplete /
  insufficient evidence”; no student is classified by score alone.

All configuration is centralized in `AnalyticsConfig`. The summary/actions are
deterministic templates; no LLM is called.

## SQL reconciliation

`scripts/validate_assessment_analytics.py` idempotently inserts only rows named
`[ASSESSMENT-ANALYTICS-VALIDATION]`. It independently verifies every displayed
metric family:

| Metric family | SQL/database verification source |
|---|---|
| cohort, participation | active course `enrollments`, distinct exam users |
| completion, abandonment, duration | `exam_attempts.status`, timestamps |
| score, median, pass rate, distribution | latest response per student/item with all `exam_questions` as denominator |
| attempted, first/final accuracy, gain | ranked `assessment_question_attempts` |
| attempts and retry rate | response counts/ranks per student/item |
| difficulty, skip/timeout, item time | final ranked response grouped by question |
| discrimination | final item result and other-item results for the same students |
| distractors/high-low groups | final option IDs joined to `exam_options` and student scores |
| topic metrics/misconceptions | ranked responses grouped by persisted topic |
| support and review priority | deterministic aggregates above |

The JSON audit is `/tmp/assessment-analytics-validation.json`. The same real
payload rendered by production JS/CSS is
`/tmp/assessment-analytics-report.pdf`; headless Chrome reports 3 pages.
