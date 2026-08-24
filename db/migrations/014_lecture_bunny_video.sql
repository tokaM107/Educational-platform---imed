-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/014_lecture_bunny_video.sql

-- Where the lecture's video actually lives, now that it is not this laptop.
--
-- Bunny's own id for the video, a GUID. Kept alongside `video_url` rather than
-- replacing it: `video_url` still names a file under data/videos for lectures
-- that have not been moved, and the two answer different questions — "which
-- file was this ingested from" and "what does a student play". Nothing has to
-- migrate in one go.
--
-- Unique, because two lectures pointing at one Bunny video is not a state with
-- a sensible reading: re-uploading the same lecture should replace the id on
-- that row, not appear as a second row sharing it.
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS bunny_video_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'lectures_bunny_video_id_key'
    ) THEN
        ALTER TABLE lectures
            ADD CONSTRAINT lectures_bunny_video_id_key UNIQUE (bunny_video_id);
    END IF;
END $$;

-- The lookup the pipeline runs before deciding whether to upload anything.
-- NULLs are distinct in a unique index, so every lecture still on a local file
-- is untouched by the constraint above.
CREATE INDEX IF NOT EXISTS idx_lectures_bunny_video
    ON lectures (bunny_video_id)
    WHERE bunny_video_id IS NOT NULL;
