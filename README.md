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
    A[lecture video] -->|upload once| Z[(Bunny Stream)]
    Z -->|smallest rendition,<br/>read by ffmpeg| B[5-min audio chunks]
    B -->|Arabic ASR| C[transcript.txt]
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
    deps.py             get_current_user, require_student/require_doctor, get_conn
  schemas/              pydantic request/response models
  services/
    supabase_client.py  the publishable client, and the secret-key admin one
    security.py         verifying a Supabase access token (no hashing, no minting)
    authz.py            who may read whose data (ownership, not just role)
    embeddings.py       Gemini embeddings (batching, quota throttling)
    query_cache.py      question embeddings cached in Postgres
    engagement.py       video_events -> watch time + coverage (pure, tested)
    report.py           a student's week: engagement + questions + narrative
    report_cache.py     stored narratives, keyed by the figures behind them
    exam_stats.py       post-exam aggregation for the instructor (no model)
    subscriptions.py    paid access: which teachers a student may watch
    report_store.py     reports a completion froze, kept as issued
    triggers.py         which completions earn a report, and the background job
    notifications.py    how a report reaches the student and their doctor
    retrieval.py        vector search + grouping hits into video segments
    prompts.py          system instruction + the model's reply contract
    llm.py              generation with retry and a fallback model
    tutor.py            the RAG orchestration
  static/               demo UI (chat + player with segment flag)
    login.html/.js/.css   the login screen
    auth.js               session storage + the authenticated API client
    report.html/.js/.css  the weekly report, printed to PDF by the browser

scripts/                operational CLIs
  enroll.py             courses, lecture assignment, enrolment (what a report counts)
  generate_weekly_reports.py  write everyone's narrative ahead of time (optional)
  remove_demo_data.py   take fabricated demo rows back out of a database

rag/                    offline pipelines, not imported by the API
  bunny.py              Bunny Stream: list/upload videos, resolve a readable URL (CLI)
  audio.py              streams a source into 5-min wav chunks, one at a time
  transcribe.py         the pipeline: upload -> encode -> audio -> transcript (CLI)
  transcribe_cohere.py  Arabic ASR over the chunks (local Cohere model)
  transcribe_whisper.py superseded; kept for reference (writes permanent audio)
  chunking.py           transcript -> timestamped chunks (pure, tested)
  ingest.py             chunk -> embed -> store  (CLI)
  eval_retrieval.py     retrieval / answer smoke test (CLI)

db/schema.sql           tables, including transcript_chunks(embedding vector)
data/                   videos, audio chunks, transcripts
tests/                  pure-logic tests: chunking, segments, engagement
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

# existing database created before the engagement event types:
psql "$DATABASE_URL" -f db/migrations/002_engagement_events.sql

# existing database created before courses / enrolments:
psql "$DATABASE_URL" -f db/migrations/003_courses_and_enrollments.sql

# existing database created before stored report narratives:
psql "$DATABASE_URL" -f db/migrations/004_report_narratives.sql

# existing database created before event-triggered reports / notifications:
psql "$DATABASE_URL" -f db/migrations/005_event_reports_and_notifications.sql

# existing database created before distractor analysis / subscriptions:
psql "$DATABASE_URL" -f db/migrations/006_attempt_selected_option.sql
psql "$DATABASE_URL" -f db/migrations/007_subscriptions.sql

# existing database created before login credentials:
psql "$DATABASE_URL" -f db/migrations/012_user_password_and_phone.sql

# links public.users to Supabase Auth (run this one against Supabase):
psql "$DATABASE_URL" -f db/migrations/013_add_auth_user_id.sql

# existing database created before videos moved to Bunny Stream:
psql "$DATABASE_URL" -f db/migrations/014_lecture_bunny_video.sql
```

**Applying 007 locks every video.** Watching needs a subscription to the
lecture's teacher, so existing students need one granting before they can play
anything:

```bash
python -m scripts.enroll subscribe --student-id 2 --doctor-id 1
```

`.env` also takes `REPORT_TIMEZONE` (default `Africa/Cairo`). A week is seven
*local* days: in UTC, a 23:00 session lands on the next day, moving study onto
the wrong day of the report and, at the week's edges, out of it entirely.

Before a report can be built, two things have to be true, and both are real
administrative data rather than anything invented: the lectures have to belong to
a course, and the student has to be enrolled on it. That is what the report counts
"3 of 5 lectures" against.

```bash
python -m scripts.enroll list                    # what exists, and what is missing
python -m scripts.enroll course --title "Anatomy 1" --doctor-id 1
python -m scripts.enroll assign --course-id 1 --lectures 1,2,3
python -m scripts.enroll add --student-id 2 --course-id 1
python -m scripts.enroll rename --user-id 1 --name "..."   # fix placeholder names
```

`enroll list` is the one to run first: it prints every user, course and lecture,
and tells you which lectures are in no course and which have no transcript, both
of which leave holes in a report.

**There is no seed script, and no fabricated study.** Every figure in a report
comes from `video_events` the player recorded while a student was actually in
front of a lecture, and from questions they actually answered. A student who has
not watched anything gets a report that says exactly that, which is the truth and
more useful than an invented week.

## Ingest a lecture

Videos live in a **Bunny Stream** library, not on disk. The pipeline uploads a
lecture once, remembers its Bunny id, and from then on reads the audio straight
off the CDN — so re-transcribing needs nothing local, and neither does
transcribing a lecture somebody else uploaded.

```bash
# 1. upload to Bunny (once) and transcribe
python -m rag.transcribe --lecture-id 1 --video sample1.mp4

# already on Bunny? give the id and nothing is uploaded
python -m rag.transcribe --lecture-id 1 --bunny-video-id <guid>

# stop after cutting the chunks, without loading the ASR model
python -m rag.transcribe --lecture-id 1 --video sample1.mp4 --audio-only

# what is in the library, and is it encoded yet?
python -m rag.bunny list
python -m rag.bunny list --unfinished
python -m rag.bunny show <guid>

# 2. transcript -> chunks -> embeddings -> Postgres
python -m rag.ingest --lecture-id 1 \
    --title "Skeletal System" \
    --transcript data/transcripts/lecture_1.txt \
    --video sample1.mp4

python -m rag.ingest --dry-run     # chunking only: no API calls, no writes
```

`.env` needs three values from the Stream library's API tab:

```env
BUNNY_STREAM_LIBRARY_ID=...
BUNNY_STREAM_API_KEY=...        # write access to every video — server only
BUNNY_STREAM_CDN_HOSTNAME=vz-xxxx.b-cdn.net
```

**Enable MP4 Fallback in the library's encoding tab before uploading anything.**
Only videos uploaded after it is on get MP4 renditions; without one the pipeline
falls back to reading the HLS playlist, which works but is slower.

**Nothing is stored.** The video is never downloaded: ffmpeg is given the Bunny
URL and writes raw PCM to a pipe, which this end cuts into five-minute wavs one
at a time. Each chunk is transcribed and deleted before the next is written, so
peak temporary disk is **one chunk — about 9.6 MB** regardless of lecture
length, and it all lives in a `tempfile.TemporaryDirectory` that goes away on
success, failure or cancellation alike. Measured on the 74-minute sample: 9.2 MB
peak, against 274 MB left behind by the previous version.

The pipe is also what paces it. While the ASR is busy nobody is reading, the
buffer fills and ffmpeg blocks — so it cannot run ahead and pile up chunks.

It asks for the *smallest* rendition on purpose — every rendition carries the
same soundtrack, so fetching 240p instead of 1080p is the same audio for a
fraction of the bytes.
Anything that needs the picture rather than the sound asks for it explicitly:
`bunny.rendition_url(video, prefer="highest")`, or a specific height, which
resolves down to what the source actually has since Bunny does not upscale.

`bunny.iter_videos()` pages through the whole library for batch work, and
`bunny.is_finished(video)` is the guard to run before anything expensive — a
video still transcoding has no renditions, and skipping the check turns "not
ready yet" into a 404 from ffmpeg.

Re-running replaces that lecture's chunks instead of duplicating them.
`lectures.bunny_video_id` holds the Bunny GUID; `lectures.video_url` still names
the local file a lecture was ingested from, and lectures move to Bunny one at a
time rather than all at once.

## Run

```bash
uvicorn app.main:app --reload
```

- `http://localhost:8000/` — demo UI
- `http://localhost:8000/docs` — OpenAPI

All endpoints below require `Authorization: Bearer <supabase access token>`;
only `/health` and the login route are public.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/login` | email + password → Supabase session (public) |
| `POST /api/auth/refresh` | refresh token → a new access token |
| `POST /api/auth/logout` | revoke the session server-side |
| `POST /api/auth/password/forgot` | email a six-digit recovery code (public) |
| `POST /api/auth/password/reset` | check the code, set a new password (public) |
| `GET /api/auth/me` | the application user behind the token |
| `POST /api/chat` | question → grounded answer + video segments + citations |
| `GET /api/lectures` | lectures with chunk count and duration |
| `GET /api/lectures/{id}/video` | the whole video, with byte-range support so seeking works |
| `POST /api/events` | record one video event (insert only, deliberately trivial) |
| `GET /api/events/analytics` | watch time / time away / session length for a session |
| `GET /api/reports/weekly` | a student's week on a course, with a generated narrative |
| `GET /api/reports/subjects` | student/course pairs **the caller** may open a report for |
| `GET /api/reports/{id}` | a report a completion produced, as it was issued |
| `GET /api/exams` | the caller's own lectures that have questions (doctors) |
| `GET /api/exams/{lecture_id}` | post-exam statistics for one lecture (its doctor only) |
| `POST /api/subscriptions` | subscribe the authenticated student to a teacher |
| `GET /api/subscriptions/access` | may the caller watch this lecture? |
| `GET /api/notifications` | the caller's inbox, with the unread count |
| `POST /api/notifications/{id}/read` | mark one read |

```bash
TOKEN=$(curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"…"}' | python -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -s localhost:8000/api/chat \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
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

**Engagement tracking** (`app/static/app.js` → `video_events` →
`app/services/engagement.py`) — the player records `play` / `pause` / `seek` /
`complete`, plus a `heartbeat` every 30 seconds *while the video is actually
running*, plus `tab_hidden` / `tab_visible`. The heartbeat is what separates
"watched for half an hour" from "pressed play and left the room"; without it a
play/pause pair says nothing about the time in between.

Watch time is then **reconstructed**, never subtracted. `last video_ts - first
video_ts` is the tempting answer and it is wrong: a jump from 00:01:40 to
00:08:20 adds six minutes of video position and zero seconds of watching, and
rewatching a stretch adds nothing at all. So `engagement.replay()` walks the
session in real time — `created_at` gives the elapsed seconds, the event types
say whether the video was running through them — and adds up the playback
intervals. A gap larger than three heartbeats is capped, because it means
events went missing (sleeping laptop, dropped network) rather than that
somebody watched in silence.

Four numbers, deliberately never merged:

| | |
| --- | --- |
| `watch_time_seconds` | the video was playing |
| `session_duration_seconds` | first event to last, pauses and absences included |
| `time_away_seconds` | the lecture page was hidden |
| `lecture_duration` | the lecture's own length |

A 180-minute session on a 75-minute lecture with 68 minutes watched and 42
minutes away is all four at once.

What `tab_hidden` means is exactly "this page stopped being visible". A
switched tab, a locked screen and a minimised window are the same event, and
nothing here identifies what the student looked at instead — so it is reported
as time away from the lecture, not as distraction.

`video_events` is indexed on `(student_id, lecture_id, session_id, created_at)`,
which is the analytics query's exact shape: one ordered range scan instead of a
filter-and-sort. As with the HNSW index, a table this small still gets a
sequential scan until it grows.

**The weekly report** (`app/services/report.py`, `/static/report.html`) — one
student, one course, seven days, assembled from rows that already exist. Open
it from the 📄 button in the player, or at `/static/report.html` — it reports on
whoever is logged in. A doctor can add `?student_id=<id>`, and the server checks
they teach that student before answering.

The page reads as prose with the figures set into it, not as a grid of numbers:
"شفت 45% من مادة الأسبوع" says something a tile reading `45.1` does not. The
week is drawn as a row of bars, coverage as a ring, and where the time went as
one stacked bar — each replacing several numbers nobody would have compared by
eye. Per-lecture numbers sit behind a "التفاصيل" toggle, and are opened
automatically for printing.

The measurements come from replaying the week's events per lecture; the only
generated part is the closing narrative, and it is generated **from the
measured numbers**, not from the raw events. Three of them are worth knowing:

- **coverage** — how much of a lecture was seen at least once, rewatching
  counted once. This is the number that catches a lecture "completed" by
  skipping through it: the seeded demo has one at 100% finished and 28%
  covered.
- **parts never watched / parts replayed** — the holes and the overlaps in the
  coverage, reported as timestamps. A rewound stretch is the strongest signal
  in the whole table: it is the student telling you which explanation did not
  land the first time.
- **time away** — carried through from `tab_hidden` with its meaning intact.
  The prompt forbids the model from reading it as distraction, the page says
  so under every occurrence, and the footnote says it again.

**Narratives are stored, not regenerated.** The numbers are recomputed on every
request because a replay costs milliseconds. The narrative costs a model call of
about half a minute, and `report_narratives` keeps it against a hash of the exact
figures it was written from (the same trick `query_embeddings` uses for
questions). So while the week is still running and the student keeps watching,
the fingerprint moves and the narrative is rewritten; once the week closes it
settles and the report becomes a fixed document. That last property is the point
— a report a student is meant to act on cannot give different advice on every
refresh. Measured here: 25 s to generate, **12 ms** to serve afterwards.

**Instructor post-exam view** (`app/services/exam_stats.py`,
`/static/exam.html`) — how the class did on one lecture's questions: average
score, per-question correct %, per-topic breakdown, score distribution, and the
roster. Every figure is a `GROUP BY` over `question_attempts` joined to
`questions`. **No model is involved**, so the page answers in ~20 ms and says the
same thing every time it is opened; prose commentary can be layered on later if
the numbers turn out not to speak for themselves.

Two definitions kept apart, because "score" is ambiguous:

| | |
| --- | --- |
| `score` | correct ÷ **every** question in the exam — unanswered counts as wrong, which is what a mark means |
| `accuracy` | correct ÷ what the student actually attempted — fair to a partial sitting, and the right lens on a *question* |

`correct_percent` counts a student right if any attempt was (matching the weekly
report), and `first_attempt_percent` keeps the stricter reading beside it — the
gap between the two is where a question needed a second think. Figures cover the
enrolled cohort; attempts from anyone else are excluded and counted in
`attempts_from_non_enrolled`, so one stray row cannot move an average.

**Distractor analysis.** `question_attempts.selected_option` records the choice
itself, so the view shows which wrong answer the class went for. That is the
difference between *"38% got it wrong"* — which might just be a hard question —
and *"50% of them chose A"*, which says one distractor is teaching something
false or the stem is ambiguous. A wrong option taken by a quarter of the answers
or more is called out; wrong answers spread evenly across the distractors are
not, because that is a hard question and nothing is broken. Options nobody picked
are still listed: a distractor no one touches means the question is really a
three-way choice.

Attempts recorded before that column existed carry no choice, so every
distribution reports the count it was built from and never passes off a partial
picture as a complete one.

**`/api/exams/*` hands out the answer key** — a distractor table is unreadable
without marking which option was right. It is an instructor endpoint and needs
authentication before launch; today nothing stops a student calling it.

## Authentication

Supabase Auth owns credentials — password hashing, sign-in, token issuance and
expiry, refresh rotation, email verification and recovery. Nothing in this
repository hashes, compares or signs anything; a second implementation of that
would be one too many.

What stays here is identity mapping and authorization:

```
Supabase auth.users.id  (UUID, owns the password)
        |
        |  users.auth_user_id
        v
public.users.id         (INTEGER, what every domain table keys on)
```

The integer id is unchanged and all eleven foreign keys still point at it. The
UUID identifies the login; the integer identifies the person the rest of the
schema knows about.

A request arrives with `Authorization: Bearer <token>`. `decode_access_token`
(`app/services/security.py`) verifies it against the project's ES256 public
keys, fetched once from the JWKS endpoint and cached — so verification is local
and costs no round trip. `get_current_user` (`app/api/deps.py`) then maps the
token's `sub` onto a `public.users` row and returns it. Missing, invalid,
expired, or valid-but-unlinked all come back **401**; a user who is who they say
and still may not have the thing gets **403**.

**The application role is read from the database, never from the token.**
Supabase puts a `role` claim in the JWT and it says `authenticated`, meaning a
Postgres role. Reading that as the student/doctor role would flatten every user
into the same permissions.

**FastAPI owns the session.** The browser talks only to this API and never to
Supabase directly, so a session is created in one place and ended in one place.
The page keeps the tokens and `app/static/auth.js` attaches, refreshes and
discards them; every protected request in the UI goes through its `api()`
helper, so no page carries token logic of its own.

One exception, and it is deliberate: a `<video>` element fetches its own source
and cannot be given a header, so `/api/lectures/{id}/video` also accepts the
token as a query parameter. Same token, same verification — only the transport
differs, and only on that route.

### Password recovery

"نسيت كلمة المرور؟" on the login page runs a two-step flow, both steps public
because being unable to log in is the whole premise:

```
POST /api/auth/password/forgot   {email}                       -> code by email
POST /api/auth/password/reset    {email, code, new_password}   -> password set
```

Supabase generates the code, mails it, decides how long it lives and verifies
it. **Nothing here stores a code** — there is no reset-codes table and there
should not be one: a second store of a second secret is a second thing to leak.

**This needs one change in the Supabase dashboard.** Authentication → Emails →
*Reset Password* must render `{{ .Token }}`. Left on the default
`{{ .ConfirmationURL }}` it mails a link instead of a code, and the code box on
the login page has nothing to type into it.

Two things the flow is careful about. It answers identically whether or not the
address is registered, so it cannot be used to find out who has an account — on
a platform whose users are all students at one school, that is worth
protecting. And the new password is set through the admin API rather than
through the session the code produces, so the recovery session is never handed
back as a way in; every other session for that user is signed out at the same
time, since whoever forced the reset may be signed in as them right now.

### Rate limits

`app/services/rate_limit.py`, applied to sign-in and to both recovery steps.

It exists because of where the session is owned. Every login and every code
reaches Supabase *from this server*, so Supabase's per-IP limits see the entire
user base as one client — they cannot tell an attacker from everybody else, and
tripping them would lock out everybody at once. The only place callers are still
distinguishable is here.

The limiter is in-process, so the budget is per worker and a restart forgets it.
That is enough to slow password guessing and to stop the reset form being used
to send mail; it is not enough on its own at scale, where this belongs in Redis
or at the proxy.

Authorization lives in `app/services/authz.py`, because a role is rarely the
whole answer. Being a doctor is not permission to read every student on the
platform; teaching them is. `may_view_student` admits exactly two cases — it is
your own data, or you are the doctor teaching them.

## Paid access

A subscription is per **teacher** and unlocks everything they publish. It is
separate from enrolment because the two change independently: a subscription
lapses without un-enrolling anybody, and a student subscribed to a teacher may be
enrolled on none, one or several of that teacher's courses.

Enforced in two places — `GET /api/lectures/{id}/video` answers **402** without
one, and `scripts.enroll add` refuses to enrol a student on a course whose
teacher they do not pay for, so nobody is enrolled on something they cannot open.
`GET /api/subscriptions/access` lets the UI show a lock before the student
presses play.

```bash
python -m scripts.enroll subscribe   --student-id 2 --doctor-id 1
python -m scripts.enroll unsubscribe --student-id 2 --doctor-id 1
```

**This is a real boundary now.** The viewer is the authenticated user, read
from a verified Supabase token, so it can no longer be walked past by sending
somebody else's id. `ENFORCE_SUBSCRIPTIONS=false` turns it off in development;
authentication itself stays on either way.

Two things `subscriptions` deliberately does not record, both of which need a
column the day the product needs them: **when access ends** (no `expires_at`, so
a row means access until it is deleted) and **what was paid** (no amount or
processor reference — this records the entitlement a payment produced, not the
payment).

**Reports also fire on what a student does, not only on the calendar.** The
pipeline is the same one — Stage 1 replays `video_events` into figures, Stage 2
hands those figures to the model — and only the trigger differs:

| Trigger | Report | Scope |
| --- | --- | --- |
| a `complete` event that finishes the course's last lecture | `module` | the whole course, all time |
| an answer that finishes a lecture's last question | `exam` | the course, framed on that lecture |

Both run in FastAPI `BackgroundTasks`, so the student's request returns before
the model is called — measured: **8 ms** to record the event, the report lands
about half a minute later. Because they run after the response they cannot use
the request's pooled connection, so each opens its own, and every failure is
swallowed and logged: a report that did not get written must never look like a
lost quiz answer.

A completion report is **frozen** in `reports`. "You finished the module"
describes an instant; recomputed a fortnight later, after the student had gone
back and rewatched half of it, the same query would quietly rewrite history.
Uniqueness is enforced by an index, because `complete` fires again every time
somebody replays the last minute.

Both the student and the doctor who teaches the course get a row in
`notifications`, worded for who is reading — *«خلّصت المقرر — تقريرك جاهز»*
against *«أحمد خلّص «Anatomy 1»»*. The site polls the inbox from the 🔔 in the
player and opens the frozen report from there. Polling, not a socket: the
requirement is "tell them next time they are on the site", which a table with a
`read_at` column does exactly, with nothing to keep running and nothing lost if
the browser was closed while the report was being written.

**A weekly report is produced when someone asks for it.** Pressing 📄 in the player opens
the page, which draws the measured half immediately and writes the narrative if
there is not already one for those figures — roughly half a minute the first time,
milliseconds afterwards. No batch run is required for a report to exist.

`scripts/generate_weekly_reports.py` is an optional warm-up: run it after the week
closes and every student's narrative is already stored before anyone opens it. It
is safe to re-run — a narrative whose figures have not moved is left alone — and
exits non-zero if any student failed, so a cron wrapper can alert on it. Two tabs
opening the same uncached report will each ask the model once; the second write
simply replaces the first, so it costs a duplicate call rather than a wrong
answer.

If the model is unreachable the report still returns every number, and falls back
to the stored narrative for that week even if the figures have since moved,
saying so. `?narrative=false` skips the model entirely; `?refresh=true` rewrites.

**PDF** is the browser's own "Save as PDF" behind a `window.print()` button,
with a `@media print` stylesheet — no PDF library on the server, and no second
renderer that could drift out of step with the page.

**Degraded mode** — if Gemini is overloaded, generation retries, then falls
back to `CHAT_FALLBACK_MODEL`. If that fails too, the student still gets the
retrieved video segment with a notice instead of an error.

## Tests

```bash
pytest tests -q                    # pure logic + auth, no database or network
python -m rag.eval_retrieval       # retrieval against the real database
python -m rag.eval_retrieval --with-answer
```

`tests/test_auth.py` and `tests/test_security.py` cover authentication and
authorization without touching Supabase or Postgres: token verification is
stubbed at the one seam that matters, and `tests/fake_db.py` stands in for the
connection so a test can assert on the queries that were actually run — which
is how "the id came from the token, not the request body" is checked rather
than assumed.

## Notes and limits

- Timestamps assume words are evenly spaced inside a 5-minute block, since the
  ASR gives no word-level timings; a segment can start a few seconds off. Emit
  word-level timings from the ASR step to make seeking frame-accurate.
- The Gemini free tier counts every embedded text (not every HTTP call) toward
  100 requests/minute, so ingesting 126 chunks takes ~2 minutes. `Embedder`
  paces itself and retries on 429.
- Subscribing is self-service and free: `POST /api/subscriptions` grants a
  logged-in student access to a teacher without any payment step. It records an
  entitlement a payment is supposed to have produced, and nothing yet checks
  that one did. This is the next real gap.
- Sign-up is not exposed. Accounts are created in Supabase and linked to a
  `public.users` row by hand; a token with no linked row is refused. Password
  *recovery* is wired; account *creation* is not.
- `password_hash`, `phone` and `phone_verified_at` on `users` are dead columns
  from the short-lived attempt to own logins here. Nothing reads them.
