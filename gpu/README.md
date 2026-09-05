# Transcription on RunPod Serverless

The Cohere Arabic ASR, running on a GPU that starts when a lecture arrives and
stops when the queue empties. Nothing is billed between videos, and nobody
presses Start or Stop.

## The whole flow

```
Bunny finishes encoding
      │  POST /api/webhooks/bunny?secret=…        (app/api/webhooks.py)
      ▼
transcription_jobs  status=pending                UNIQUE(bunny_guid)
      │  rag/worker.py claims it, asks Bunny for the smallest rendition URL
      ▼
RunPod  POST /run  {"input": {"bunny_url": …}}    status=submitted
      │  GPU cold-starts (min workers 0 → 1)
      ▼
gpu/handler.py  ───► downloads from Bunny directly
      │              ffmpeg → 5-min wavs → Cohere ASR → blocks
      │              /tmp/transcription_jobs/<id>/ removed in a finally
      ▼
RunPod  GET /status/<id>  COMPLETED               status=processing → completed
      │  worker chunks + embeds the returned text
      ▼
transcript_chunks in Supabase (with video_id)
      │
      ▼
GPU idles → RunPod scales the worker to zero
```

**The application server never touches the media.** It passes a URL and
receives text. That is why its image has no ffmpeg — if something there ever
needs it, the bytes have started flowing the wrong way.

## Where data lives

| Data | Where | Lifetime |
| --- | --- | --- |
| Source video | Bunny Stream | permanent, the only copy |
| Downloaded media, wav chunks | RunPod worker `/tmp/transcription_jobs/<job_id>/` | deleted in a `finally`, every job |
| Transcript text | returned in the RunPod job result | not written to disk anywhere |
| Chunks + embeddings | Supabase `transcript_chunks` | permanent |
| Job state | Supabase `transcription_jobs` | permanent |

Nothing lands on the application VPS, in Supabase Storage, in this repository,
or on a RunPod network volume. A failed job keeps nothing, because Bunny still
has the lecture — a retry simply fetches it again.

## Building and deploying the worker

```bash
# From the repository root — the image needs rag/ as well as gpu/.
docker build -f gpu/Dockerfile -t <registry>/lecture-asr:v1 .

# If the model repo is gated, pass the token as a build secret so it does not
# become an image layer:
docker build -f gpu/Dockerfile \
    --secret id=hf_token,env=HF_TOKEN \
    -t <registry>/lecture-asr:v1 .

docker push <registry>/lecture-asr:v1
```

The model is baked into the image on purpose. A scale-from-zero that had to
pull several gigabytes from Hugging Face first would make the first lecture of
the day wait minutes; from the image layer it is seconds.

### The endpoint

In the RunPod console, **Serverless → New Endpoint**:

| Setting | Value | Why |
| --- | --- | --- |
| Container image | `<registry>/lecture-asr:v1` | |
| GPU | 20 GB class (RTX A4500 or better) | measured 4.90 GB peak; 20 GB leaves room |
| Min workers | **0** | no GPU billed while idle |
| Max workers | **1** | raise later; one worker transcribes ~117x realtime |
| Idle timeout | 30–60 s | how long a warm worker waits before scaling down |
| Execution timeout | ≥ 3600 s | must exceed `RUNPOD_JOB_TIMEOUT_SECONDS` |
| Container disk | ≥ 30 GB | image plus one lecture's temporary audio |

Environment variables on the endpoint:

```
COHERE_TRANSCRIBE_MODEL=CohereLabs/cohere-transcribe-arabic-07-2026
BUNNY_MEDIA_HOSTS=vz-xxxx.b-cdn.net,video.bunnycdn.com
```

`BUNNY_MEDIA_HOSTS` is a security control, not a convenience. The worker
refuses any URL outside it: this container holds a GPU and an identity, and
anyone able to submit a job could otherwise make it fetch an internal address.

Then on the application server:

```env
ASR_BACKEND=runpod
RUNPOD_API_KEY=...
RUNPOD_ENDPOINT_ID=...        # the id in the endpoint's URL
```

### Raising throughput

Increase **Max workers** in the endpoint settings. Nothing in the code changes;
the application-side worker submits as fast as it claims. Min workers stays 0 —
setting it to 1 keeps a GPU permanently billed and is only worth it if cold
starts become the bottleneck.

## Testing one video

```bash
# 1. Is the endpoint alive? (a trivial job that fails fast is fine)
curl -s -X POST https://api.runpod.ai/v2/$RUNPOD_ENDPOINT_ID/run \
    -H "Authorization: Bearer $RUNPOD_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"input":{"bunny_url":"https://not-allowed.test/x.mp4"}}'
# expect a job that completes with {"error": "... not one of the allowed Bunny hosts"}

# 2. A real lecture, end to end.
python -m rag.worker --video-id 11

# 3. What the queue thinks.
python -m rag.worker --status
```

## Inspecting a failed job

```sql
SELECT id, bunny_guid, video_id, status, attempt_count, max_attempts,
       runpod_job_id, last_error, submitted_at, completed_at
FROM transcription_jobs
WHERE status <> 'completed'
ORDER BY created_at;
```

`python -m rag.worker --status` prints the same thing. Common `last_error`
values and what they mean:

| Message | Cause |
| --- | --- |
| `AudioError: ffmpeg is not installed` | the image was built without ffmpeg |
| `... not one of the allowed Bunny hosts` | `BUNNY_MEDIA_HOSTS` does not include your pull zone |
| `RunPod FAILED: CUDA out of memory` | GPU too small, or max workers sharing one card |
| `waiting for the catalog row` | Nest has not written `course_items.video_ref` yet; it retries |
| `gave up after Ns (RunPod still IN_QUEUE)` | no worker picked it up — check max workers > 0 |
| `RunPod TIMED_OUT` | the endpoint's execution timeout is below the lecture length |

A job with `attempt_count < max_attempts` is picked up again on its own. One
that has run out of attempts stays `failed` until requeued.

## Retrying

```bash
# Re-run one video, resetting its attempts. Replaces its chunks; does not
# duplicate them (replace_chunks deletes then inserts in one transaction).
python -m rag.worker --video-id 11
```

To hand a stubborn lecture more attempts without changing the global default:

```sql
UPDATE transcription_jobs SET max_attempts = 6, status = 'pending'
WHERE bunny_guid = '<guid>';
```

## Benchmarking RTFx

Every completed job records what it cost:

```sql
SELECT video_id,
       audio_duration_seconds,
       gpu_processing_seconds,
       round(audio_duration_seconds / gpu_processing_seconds, 1) AS rtfx
FROM transcription_jobs
WHERE status = 'completed' AND gpu_processing_seconds > 0
ORDER BY completed_at DESC;
```

Reference, measured on RTX A4500 20 GB with bfloat16:

| Metric | Value |
| --- | --- |
| Model idle VRAM | ~3.85 GB |
| Peak VRAM (5-min chunk) | ~4.90 GB |
| 5-min audio | ~2.55 s |
| RTFx | ~117x |

At 117x an hour-long lecture is roughly 31 seconds of GPU. Cold start and the
CDN read dominate the wall clock, not the ASR.

## Turning transcription off safely

Stop the application-side worker; leave everything else alone:

```bash
docker compose stop worker
```

Webhooks keep arriving and keep queueing rows — nothing is lost, and the queue
drains when the worker comes back. To stop Bunny calling at all, clear the
webhook URL in the Stream library settings. To stop the GPU specifically, set
the endpoint's max workers to 0 in the RunPod console; jobs then sit in
`IN_QUEUE` until `RUNPOD_JOB_TIMEOUT_SECONDS` expires and are retried later.

Do **not** delete `transcription_jobs` rows to "reset" things — a deleted row
means the next Bunny redelivery transcribes that video again from scratch.

## Local development without RunPod

`ASR_BACKEND=cohere` loads the model in-process instead of calling RunPod. It
needs a local GPU and the full `requirements.txt`, and it is the path used for
benchmarking a new GPU or checking a transcript by hand:

```bash
ASR_BACKEND=cohere python -m rag.transcribe --video-id 11
```
