-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/002_engagement_events.sql

-- 1. Three new event types.
--
--    'heartbeat'   proof the video is still running, sent every 30 s while it
--                  plays. Play/pause alone cannot separate half an hour of
--                  watching from pressing play and leaving the room.
--    'tab_hidden'  the lecture page stopped being visible.
--    'tab_visible' it came back.
--
--    The visibility pair only ever means "time away from the lecture page".
--    A hidden page looks the same whether the student switched tabs, locked
--    the screen, or minimised the window, so nothing here identifies what
--    they were doing instead.
--
--    The existing constraint is looked up by definition rather than by name,
--    because a database created from an older schema.sql may have it under a
--    generated name.
DO $$
DECLARE
    existing text;
BEGIN
    SELECT conname INTO existing
    FROM pg_constraint
    WHERE conrelid = 'video_events'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%event_type%';

    IF existing IS NOT NULL THEN
        EXECUTE format('ALTER TABLE video_events DROP CONSTRAINT %I', existing);
    END IF;
END $$;

ALTER TABLE video_events
    ADD CONSTRAINT video_events_event_type_check CHECK (
        event_type IN (
            'play', 'pause', 'seek', 'skip', 'complete', 'rewatch_segment',
            'heartbeat', 'tab_hidden', 'tab_visible'
        )
    );

-- 2. Engagement analytics always reads one student's one session in event
--    order. Without this it can use only one of the two single-column indexes,
--    then filters and sorts the rest by hand; with it the whole query is one
--    ordered range scan. Heartbeats make video_events the fastest-growing
--    table in the schema, so the read side is worth the extra write.
CREATE INDEX IF NOT EXISTS idx_video_events_session
    ON video_events (student_id, lecture_id, session_id, created_at);
