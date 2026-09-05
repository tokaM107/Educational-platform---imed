-- INVESTIGATION AND BACKFILL PROPOSAL — NOT APPROVED, NOT RUN.
--
-- Nothing in this file has been executed against any database, and it must not
-- be until somebody has read the findings the SELECTs below produce and
-- decided what the right answer is. It is written as a proposal precisely
-- because the safe action is not yet known.
--
-- HANDOFF COPY: any approved statement belongs in a migration created in
-- educational-platform-db, not run from this checkout.
--
--
-- THE PROBLEM
--
-- `transcript_chunks` carries both `lecture_id` (the original key) and
-- `video_id` (added when the catalog moved to `course_items`). As observed on
-- the live database on 2026-09-05:
--
--     151 rows      across 26 distinct lecture_id values
--     0 rows        have a non-null video_id
--
-- app/services/retrieval.py joins course_items on `c.video_id`:
--
--     JOIN course_items AS item ON item.id = c.video_id AND item.type = 'video'
--
-- so every one of those 151 rows is invisible to course-video retrieval. A
-- student asking a question about a course video gets no passages from them.
--
-- This is pre-existing and is NOT caused by the transcription pipeline. New
-- transcriptions are unaffected: rag/ingest.py writes `video_id` on every row
-- it inserts, and tests/test_ingest_blocks.py holds it to that.
--
--
-- WHY THIS IS NOT A ONE-LINE UPDATE
--
-- There is no automatic mapping from `lectures` to `course_items`. They are
-- separate catalogs owned by different services — `lectures` is FastAPI's,
-- `course_items` is NestJS's — and a lecture is not guaranteed to have a
-- corresponding course item at all. Guessing the correspondence would attach a
-- transcript to the wrong video, which is worse than attaching it to none: the
-- tutor would answer confidently out of another lecture's material and cite a
-- timestamp in a video the student is not watching.
--
-- So: find out first. Decide second. Write third.


-- ---------------------------------------------------------------
-- STEP 1 — read-only. Safe to run anywhere. Answers "how bad is it".
-- ---------------------------------------------------------------

-- 1a. Confirm the shape of the problem.
SELECT count(*) AS total_chunks,
       count(*) FILTER (WHERE video_id IS NULL) AS without_video_id,
       count(*) FILTER (WHERE lecture_id IS NULL) AS without_lecture_id,
       count(DISTINCT lecture_id) AS distinct_lectures,
       count(DISTINCT video_id) AS distinct_videos
FROM public.transcript_chunks;

-- 1b. Which lectures have orphaned chunks, and do those lectures still exist?
SELECT c.lecture_id,
       count(*) AS chunks,
       l.title,
       l.bunny_video_id
FROM public.transcript_chunks AS c
LEFT JOIN public.lectures AS l ON l.id = c.lecture_id
WHERE c.video_id IS NULL
GROUP BY c.lecture_id, l.title, l.bunny_video_id
ORDER BY chunks DESC;

-- 1c. The only defensible bridge: a lecture and a course item that point at
--     the SAME Bunny video. Anything matched here is matched on identity, not
--     on a title that happens to look similar.
--
--     If this returns nothing, there is no safe automatic backfill and the
--     answer is Option C below.
SELECT l.id AS lecture_id,
       item.id AS video_id,
       l.bunny_video_id,
       count(c.id) AS chunks_that_would_move
FROM public.lectures AS l
JOIN public.course_items AS item
  ON item.video_ref = l.bunny_video_id AND item.type = 'video'
JOIN public.transcript_chunks AS c
  ON c.lecture_id = l.id AND c.video_id IS NULL
WHERE l.bunny_video_id IS NOT NULL
GROUP BY l.id, item.id, l.bunny_video_id;

-- 1d. Safety check for 1c: no lecture may map to more than one course item.
--     A row here means the Bunny guid is duplicated in the catalog and the
--     backfill must stop until that is resolved.
SELECT l.bunny_video_id, count(DISTINCT item.id) AS course_items
FROM public.lectures AS l
JOIN public.course_items AS item
  ON item.video_ref = l.bunny_video_id AND item.type = 'video'
WHERE l.bunny_video_id IS NOT NULL
GROUP BY l.bunny_video_id
HAVING count(DISTINCT item.id) > 1;


-- ---------------------------------------------------------------
-- STEP 2 — the options. Pick one AFTER reading step 1's output.
-- ---------------------------------------------------------------

-- OPTION A — backfill only what 1c matched on a shared Bunny guid.
--            Conservative and reversible. Touches nothing it cannot prove.
--
--            Take a backup first. Run 1d and confirm it returns no rows.
--
-- BEGIN;
--
-- UPDATE public.transcript_chunks AS c
-- SET video_id = item.id
-- FROM public.lectures AS l
-- JOIN public.course_items AS item
--   ON item.video_ref = l.bunny_video_id AND item.type = 'video'
-- WHERE c.lecture_id = l.id
--   AND c.video_id IS NULL
--   AND l.bunny_video_id IS NOT NULL;
--
-- -- Expect this to equal the sum of `chunks_that_would_move` from 1c.
-- -- If it does not, something changed between reading and writing: ROLLBACK.
-- SELECT count(*) FROM public.transcript_chunks WHERE video_id IS NOT NULL;
--
-- COMMIT;

-- OPTION B — re-transcribe instead of mapping. Slower and costs GPU time, but
--            it produces chunks under the current chunker and embedding model
--            rather than preserving whatever produced these in 2026-08. For 26
--            lectures at the benchmarked 117x RTFx this is minutes of GPU, not
--            hours. Per video:
--
--                python -m rag.worker --video-id <id>
--
--            No SQL, no risk to existing rows, and the result is consistent
--            with everything the pipeline writes from now on. This is the
--            recommended option where a course item exists.

-- OPTION C — leave them. If 1c returns nothing, these 151 rows belong to
--            lectures that were never migrated into `course_items`, and they
--            are still reachable through the legacy lecture path. Deleting or
--            force-mapping them would lose data to no benefit. Document and
--            move on.


-- ---------------------------------------------------------------
-- WHAT NOT TO DO
-- ---------------------------------------------------------------
--
--   UPDATE transcript_chunks SET video_id = lecture_id;
--
-- `lectures.id` and `course_items.id` are independent sequences in different
-- catalogs. This would silently attach transcripts to unrelated videos, and
-- because both columns are plain integers nothing would raise an error. It
-- would look like it worked.
