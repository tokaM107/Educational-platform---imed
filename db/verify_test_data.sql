-- Verification queries for the synthetic dataset.
--   psql "$DATABASE_URL" -f db/verify_test_data.sql
--
-- Test rows are identifiable by: users.email LIKE 'test.%@example.com',
-- courses/lectures/topics.title LIKE '[TEST]%', video_events.session_id LIKE 'test-%'.
-- Drop the LIKE filters to include your real data.

\pset pager off

\echo '== 1. students per course =='
SELECT c.id, c.title, count(e.student_id) AS students,
       string_agg(split_part(u.email,'@',1), ', ' ORDER BY u.email) AS who
FROM courses c
JOIN enrollments e ON e.course_id = c.id
JOIN users u ON u.id = e.student_id
GROUP BY c.id, c.title ORDER BY c.id;

\echo '== 2. lecture completion per student =='
SELECT u.email, l.title,
       bool_or(v.event_type = 'complete') AS completed,
       count(*) FILTER (WHERE v.event_type = 'complete') AS complete_events
FROM video_events v
JOIN users u ON u.id = v.student_id
JOIN lectures l ON l.id = v.lecture_id
GROUP BY u.email, l.title
ORDER BY u.email, l.title;

\echo '== 3. average watch time per lecture (heartbeat-derived, capped at 90s/gap) =='
WITH steps AS (
  SELECT v.lecture_id, v.student_id, v.session_id, v.event_type, v.created_at,
         lag(v.event_type) OVER w AS prev_type,
         EXTRACT(EPOCH FROM v.created_at - lag(v.created_at) OVER w) AS gap
  FROM video_events v
  WINDOW w AS (PARTITION BY v.student_id, v.lecture_id, v.session_id ORDER BY v.created_at)
)
SELECT l.title,
       count(DISTINCT s.student_id) AS students,
       round(sum(LEAST(s.gap, 90)) FILTER (
         WHERE s.prev_type IN ('play','heartbeat','seek','tab_visible'))::numeric / 60, 1)
         AS total_watch_min,
       round(sum(LEAST(s.gap, 90)) FILTER (
         WHERE s.prev_type IN ('play','heartbeat','seek','tab_visible'))::numeric
         / NULLIF(count(DISTINCT s.student_id),0) / 60, 1) AS avg_watch_min
FROM steps s JOIN lectures l ON l.id = s.lecture_id
GROUP BY l.title ORDER BY total_watch_min DESC NULLS LAST;

\echo '== 4. replay hotspots: where students seek BACKWARDS to =='
WITH back AS (
  SELECT v.lecture_id, v.student_id, v.video_ts AS landed,
         lag(v.video_ts) OVER (PARTITION BY v.student_id, v.lecture_id, v.session_id
                               ORDER BY v.created_at) AS came_from
  FROM video_events v WHERE v.event_type IN ('seek','play','heartbeat','pause')
)
SELECT l.title,
       (floor(b.landed / 60) * 60)::int AS bucket_start_sec,
       to_char(((floor(b.landed / 60) * 60)::int) * interval '1 second',
               'HH24:MI:SS') AS bucket,
       count(DISTINCT b.student_id) AS students,
       count(*) AS rewinds,
       string_agg(DISTINCT split_part(u.email, '@', 1), ', ') AS who
FROM back b
JOIN lectures l ON l.id = b.lecture_id
JOIN users u ON u.id = b.student_id
WHERE b.came_from IS NOT NULL AND b.landed < b.came_from - 5
GROUP BY l.title, 2, 3
ORDER BY students DESC, rewinds DESC, 2;

\echo '== 5. question accuracy per student =='
SELECT u.email,
       count(DISTINCT a.question_id) AS questions_tried,
       count(DISTINCT a.question_id) FILTER (WHERE a.is_correct) AS got_right,
       count(*) AS attempts,
       round(100.0 * count(DISTINCT a.question_id) FILTER (WHERE a.is_correct)
             / NULLIF(count(DISTINCT a.question_id),0), 1) AS accuracy_pct
FROM question_attempts a JOIN users u ON u.id = a.student_id
GROUP BY u.email ORDER BY accuracy_pct DESC NULLS LAST;

\echo '== 6. students who completed EVERY lecture of a course (module trigger) =='
SELECT u.email, c.title,
       count(DISTINCT l.id) AS lectures,
       count(DISTINCT l.id) FILTER (WHERE done.lecture_id IS NOT NULL) AS completed
FROM enrollments e
JOIN users u ON u.id = e.student_id
JOIN courses c ON c.id = e.course_id
JOIN lectures l ON l.course_id = c.id
LEFT JOIN (SELECT DISTINCT student_id, lecture_id FROM video_events
           WHERE event_type = 'complete') done
       ON done.lecture_id = l.id AND done.student_id = e.student_id
GROUP BY u.email, c.title
HAVING count(DISTINCT l.id) = count(DISTINCT l.id) FILTER (WHERE done.lecture_id IS NOT NULL)
ORDER BY u.email;

\echo '== 7. students who answered EVERY question of a lecture (exam trigger) =='
SELECT u.email, l.title, count(DISTINCT q.id) AS questions
FROM users u
JOIN questions q ON TRUE
JOIN lectures l ON l.id = q.lecture_id
WHERE NOT EXISTS (
  SELECT 1 FROM questions q2 WHERE q2.lecture_id = l.id AND NOT EXISTS (
    SELECT 1 FROM question_attempts a
    WHERE a.question_id = q2.id AND a.student_id = u.id))
AND EXISTS (SELECT 1 FROM question_attempts a2
            JOIN questions q3 ON q3.id = a2.question_id
            WHERE a2.student_id = u.id AND q3.lecture_id = l.id)
GROUP BY u.email, l.title ORDER BY u.email;

\echo '== 8. unread notifications per user =='
SELECT u.email, u.role,
       count(*) FILTER (WHERE n.read_at IS NULL) AS unread,
       count(*) FILTER (WHERE n.read_at IS NOT NULL) AS read,
       count(*) AS total
FROM notifications n JOIN users u ON u.id = n.user_id
GROUP BY u.email, u.role ORDER BY unread DESC;

\echo '== 9. generated reports =='
SELECT r.id, r.kind, u.email AS student, c.title AS course, l.title AS lecture,
       r.generated_at,
       (r.payload -> 'narrative' IS NOT NULL AND r.payload -> 'narrative' <> 'null')
         AS has_narrative,
       (SELECT count(*) FROM notifications n WHERE n.report_id = r.id) AS notifications
FROM reports r
JOIN users u ON u.id = r.student_id
JOIN courses c ON c.id = r.course_id
LEFT JOIN lectures l ON l.id = r.lecture_id
ORDER BY r.id;

\echo '== 10. watch time vs time away, per student and lecture =='
WITH steps AS (
  SELECT v.student_id, v.lecture_id, v.session_id, v.event_type, v.created_at,
         lag(v.event_type) OVER w AS prev_type,
         EXTRACT(EPOCH FROM v.created_at - lag(v.created_at) OVER w) AS gap
  FROM video_events v
  WINDOW w AS (PARTITION BY v.student_id, v.lecture_id, v.session_id ORDER BY v.created_at)
)
SELECT u.email, l.title,
       round(COALESCE(sum(LEAST(s.gap,90)) FILTER (
         WHERE s.prev_type IN ('play','heartbeat','seek','tab_visible')),0)::numeric/60,1)
         AS watch_min,
       round(COALESCE(sum(s.gap) FILTER (WHERE s.prev_type = 'tab_hidden'),0)::numeric/60,1)
         AS away_min,
       round(COALESCE(sum(s.gap),0)::numeric/60,1) AS session_span_min
FROM steps s JOIN users u ON u.id = s.student_id JOIN lectures l ON l.id = s.lecture_id
GROUP BY u.email, l.title
HAVING COALESCE(sum(s.gap) FILTER (WHERE s.prev_type = 'tab_hidden'),0) > 0
ORDER BY away_min DESC;
