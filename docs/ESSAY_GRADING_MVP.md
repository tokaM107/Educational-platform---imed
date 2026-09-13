# Essay grading evaluation MVP

This is an isolated engineering prototype. It does not write to the database
and is not connected to exam submission. Its synthetic fixture is not a
validated academic benchmark.

## Architecture

The pipeline makes exactly two LLM calls:

1. `essay-criteria-v1` sees the question and model answer, then extracts atomic,
   independently gradable criteria. It never sees the student answer.
2. `essay-evaluator-v1` sees the question, generated criteria, and student answer,
   then assigns `yes`, `partial`, `no`, or `contradicted` once per criterion. It
   cannot create criteria and is instructed to treat answer text as untrusted data.

Both stages use Gemini through the repository's existing client, API key,
structured response schema, timeout, retry, and fallback conventions. Temperature
is zero. Pydantic rejects invalid structures, duplicate IDs, missing results, and
unknown result IDs. One retry is made when an otherwise successful provider reply
does not validate.

The LLM never calculates points. It proposes each criterion's **relative
importance as a share of 1.0** (`weight`), and Python turns judgements into a
mark. Two weighting paths exist, and which one applies depends on what the
caller supplies:

```text
status factors:  yes = 1.0, partial = 0.5, no = 0.0, contradicted = 0.0

# 1. Teacher allocations — used when EVERY criterion carries `marks`.
#    The caller converts the model's weights against the question's total
#    (a 5-mark question with weights 0.4/0.35/0.25 becomes 2/1.75/1.25)
#    and sends those marks on the evaluation request.
score = Σ (marks × status_factor)

# 2. Equal split — the fallback when marks are absent or only partial.
#    This is the prototype path, and a freshly generated proposal before a
#    teacher has reviewed it. The model's weights are NOT used here.
weight = max_points / criterion_count
score  = Σ (weight × status_factor)
```

Mixed or partial allocations fall back to the equal split rather than guessing
at the missing ones.

Calculations use `Decimal`, round half-up to two places, and are capped to the
configured maximum. A review flag preserves a provisional score; malformed or
incomplete stage output produces `grading_failed` and no finalized score.

## Configuration and local UI

Required for real calls:

```env
GEMINI_API_KEY=...
ENABLE_GRADING_DEMO_UI=true
```

Optional stage-specific model overrides:

```env
ESSAY_CRITERIA_MODEL=gemini-model-name
ESSAY_EVALUATOR_MODEL=gemini-model-name
```

Each unset model inherits `CHAT_MODEL`. Provider retry may use the existing
`CHAT_FALLBACK_MODEL`. The dedicated demo flag defaults to false and controls
both `/grading-demo` and every `/api/grading-demo/*` endpoint.

When enabled, every grading API endpoint uses the same shared
`get_current_user` dependency as the chat API. Any authenticated application
user may call it; there is no grading-specific role check or separate auth
implementation. The page reuses the existing FastAPI/Supabase login,
access-token, and refresh-token flow. Sensitive grading API responses carry
`Cache-Control: no-store`. Inputs are bounded before any provider call, and the
full 40-case dataset endpoint is limited to two starts per user per ten minutes
per application worker. It also consumes the complete default daily LLM budget;
normal grading calls share the same Postgres-backed 10-request UTC-day budget as
chat, search, and generated reports.

Start the API:

```bash
ENABLE_GRADING_DEMO_UI=true .venv/bin/uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/grading-demo>. Choose a fixture case and use **Load
Example**, then **Run Full Pipeline**. Raw output shown by this internal endpoint
is only Gemini's structured response, never hidden reasoning. API keys remain on
the server.

## Dataset evaluation

The fixture at `app/fixtures/essay_grading_dataset.json` contains exactly 10
synthetic questions and 40 cases. The runner generates criteria once per question
and reuses them for its four answers:

```bash
python -m scripts.evaluate_essay_grading
python -m scripts.evaluate_essay_grading --json /tmp/essay-report.json --csv /tmp/essay-report.csv
```

The UI's **Run All Dataset Cases** button runs the same service. Individual
failures are retained in the report and do not abort later cases. Reports are not
created or committed unless an output path is explicitly supplied.

## Known limitations

- The equal-split fallback cannot represent differing clinical importance.
  Weighted scoring addresses this, but only when the caller converts the
  model's weights into per-criterion marks and sends them back; nothing in
  this service performs that conversion.
- Criteria decomposition is itself model-dependent and may change expected score
  granularity.
- The prototype has no calibration, inter-rater study, or validated acceptance
  threshold.
- It performs only structural/completeness checks: no semantic validation, quote
  verification, NLI, embeddings, or extra model pass.
- A review flag identifies uncertainty but does not replace a doctor's judgment.
- The in-process dataset rate limit is per worker and resets on deployment; use
  a proxy or shared limiter if the tool is exposed beyond a controlled faculty
  group. The separate daily LLM budget is database-backed and cross-worker.

Do not approve production use until predictions have been compared with blinded
human grading, disagreements have been reviewed at criterion level, and acceptable
error/review thresholds have been agreed with faculty.

The proposed versioned production persistence model and migration handoff are
documented in [ESSAY_GRADING_STORAGE_DESIGN.md](ESSAY_GRADING_STORAGE_DESIGN.md).
