# Educational Platform — RAG tutor over recorded lectures

A student asks a question in Arabic. The tutor answers **only** from the
lecture transcript — simplifying the doctor's own explanation and reusing the
doctor's own examples and mnemonics — and points at the exact stretch of the
recorded lecture the answer came from.

The video is always served whole. An answer just moves the playhead to the
start of the relevant chunk and drops a 🚩 flag where it ends. **Playback is
never stopped at the flag** — the student can keep watching straight past it.

```mermaid
flowchart LR
    A[lecture video] -->|ffmpeg| B[5-min audio chunks]
    B -->|ASR| C[transcript.txt]
    C -->|rag.chunking| D[~120-word windows<br/>with timestamps]
    D -->|rag.ingest| E[(Postgres + pgvector)]
    F[student question] --> G[embed query]
    G --> E
    E -->|top-k passages| H[Gemini, grounded prompt]
    H --> I[simplified answer + citations]
    E --> J[video segment<br/>start → 🚩 flag]
```

## Layout

```
app/                    FastAPI service
  config.py             every tunable value, read once from .env
  db.py                 pgvector-aware connection pool
  api/                  HTTP layer only (auth, lectures, chat)
  schemas/              pydantic request/response models
  services/
    embeddings.py       Gemini embeddings (batching, quota throttling)
    query_cache.py      question embeddings cached in Postgres
    retrieval.py        vector search + grouping hits into video segments
    prompts.py          system instruction + the model's reply contract
    llm.py              generation with retry and a fallback model
    tutor.py            the RAG orchestration
  static/               demo UI (chat + player with segment flag)

rag/                    offline pipelines, not imported by the API
  transcribe_whisper.py video -> audio chunks -> transcript (HF/fal Whisper)
  transcribe_cohere.py  same, with a local Cohere Arabic ASR model
  chunking.py           transcript -> timestamped chunks (pure, tested)
  ingest.py             chunk -> embed -> store  (CLI)
  eval_retrieval.py     retrieval / answer smoke test (CLI)

db/schema.sql           tables, including transcript_chunks(embedding vector)
data/                   videos, audio chunks, transcripts
tests/                  pure-logic tests: chunking + segment building
```

`app/` and `rag/` share `app.config` and `app.services`, so the ingest
pipeline and the live API can never disagree about the embedding model, its
dimension, or the chunking parameters.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d                       # Postgres 16 + pgvector
psql "$DATABASE_URL" -f db/schema.sql       # first run only

# existing database created before the vector index / query cache:
psql "$DATABASE_URL" -f db/migrations/001_vector_index_and_query_cache.sql
```

`.env`:

```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/medical_ai
GEMINI_API_KEY=...
HF_TOKEN=...        # only for rag/transcribe_whisper.py
```

## Ingest a lecture

```bash
# 1. video -> transcript (writes data/transcripts/transcript.txt)
python rag/transcribe_cohere.py

# 2. transcript -> chunks -> embeddings -> Postgres
python -m rag.ingest --lecture-id 1 \
    --title "الجهاز الهيكلي — Skeletal System" \
    --transcript data/transcripts/transcript.txt \
    --video sample1.mp4

python -m rag.ingest --dry-run     # chunking only: no API calls, no writes
```

Re-running replaces that lecture's chunks instead of duplicating them. The
video file itself lives in `data/videos/`, and `lectures.video_url` stores its
file name.

## Run

```bash
uvicorn app.main:app --reload
```

- `http://localhost:8000/` — demo UI
- `http://localhost:8000/docs` — OpenAPI

| Endpoint | Purpose |
| --- | --- |
| `POST /api/chat` | question → grounded answer + video segments + citations |
| `GET /api/lectures` | lectures with chunk count and duration |
| `GET /api/lectures/{id}/video` | the whole video, with byte-range support so seeking works |
| `POST /api/auth/login` | stub, unchanged |

```bash
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"عظمة القعبرة اسمها ايه بالانجليزي وليه؟","lecture_id":1}'
```

```jsonc
{
  "answer": "عظمة الكعبرة اسمها بالإنجليزي الراديوس (Radius) [2]. …",
  "grounded": true,
  "segments": [
    { "start_ts": 1158, "end_ts": 1247,          // player seeks here, flag there
      "start_label": "00:19:18", "end_label": "00:20:47",
      "video_url": "/api/lectures/1/video" }
  ],
  "citations": [ { "index": 1, "start_ts": 1122, "text": "…", "distance": 0.2583 } ]
}
```

## How the pieces work

**Chunking** (`rag/chunking.py`) — a sliding 120-word window with 25 words of
overlap, taken inside each 5-minute ASR block. The ASR barely punctuates (one
block has 13 sentence marks across 747 words), so sentence splitting is not an
option; a whole block is too coarse to retrieve. At this lecturer's pace
~120 words ≈ 50 seconds of video, which is a useful jump target. Windows never
cross a block header, so timestamps stay honest — they are interpolated inside
the block's own time range.

**Embeddings are computed once, never per request.** The transcript is
embedded by `rag/ingest.py` and lives in `transcript_chunks.embedding
vector(1536)`; the API only ever reads it. Two consequences worth knowing:

- Re-running ingest reuses the stored vector for every chunk whose text has
  not changed, so editing part of a transcript re-embeds only the affected
  chunks (a full re-run of this lecture: 126 embeddings and ~2 minutes the
  first time, 0 embeddings and 3 seconds after). `--reembed` forces the lot.
- The one thing that must be embedded at request time is the student's
  question — a vector search cannot compare text to vectors otherwise — so
  those are cached in `query_embeddings`, keyed by hash + model + dimension.
  A repeat question costs a 2 ms lookup instead of a ~400 ms API call and
  spends no quota.

`transcript_chunks.embedding` is indexed with **HNSW** (`vector_cosine_ops`,
matching the `<=>` operator used in the search). At 126 rows Postgres still
prefers a sequential scan because it is genuinely cheaper; the index takes
over as lectures are added. Existing databases: apply
`db/migrations/001_vector_index_and_query_cache.sql`.

**Retrieval** (`app/services/retrieval.py`) — cosine distance over
`transcript_chunks.embedding`, then neighbouring hits are merged into one
continuous segment so the student gets one "play from here" button per idea
rather than three that point at the same minute. Playback backs up
`SEGMENT_LEAD_IN` seconds so it starts on a sentence.

**Grounding** — three layers, because none is sufficient alone:

1. a coarse distance cut-off (`MAX_DISTANCE`) drops obviously unrelated chunks;
2. the model only ever sees transcript excerpts and is told to refuse anything
   outside them;
3. it must return `found` and `used_excerpts` as structured JSON, so an
   off-topic question ends as an honest refusal with no video segment, and the
   segments point only at the excerpts the answer was actually built from.

Layer 3 matters: measured on this lecture, on-topic hits sit at 0.25–0.31 and
an off-topic question ("what raises stroke risk?") still lands at 0.39, so
distance alone cannot separate them.

**Degraded mode** — if Gemini is overloaded, generation retries, then falls
back to `CHAT_FALLBACK_MODEL`. If that fails too, the student still gets the
retrieved video segment with a notice instead of an error.

## Tests

```bash
pytest tests -q                    # pure logic, no database or API key
python -m rag.eval_retrieval       # retrieval against the real database
python -m rag.eval_retrieval --with-answer
```

## Notes and limits

- Timestamps assume words are evenly spaced inside a 5-minute block, since the
  ASR gives no word-level timings; a segment can start a few seconds off. Emit
  word-level timings from the ASR step to make seeking frame-accurate.
- The Gemini free tier counts every embedded text (not every HTTP call) toward
  100 requests/minute, so ingesting 126 chunks takes ~2 minutes. `Embedder`
  paces itself and retries on 429.
- Auth is still the original stub; the chat endpoint does not yet check who is
  asking.
