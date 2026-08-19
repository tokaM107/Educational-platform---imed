-- Proof that the catalog hierarchy supports the deterministic lookups the
-- Smart Search Assistant will need.
--
--   psql "$DATABASE_URL" -f db/verify_catalog.sql
--
-- Every query here is plain SQL over relational fields. No LLM writes any of
-- this: the assistant's only job is to turn a sentence into the parameters
-- (doctor / subject / academic_year / module) that these queries take.

\pset pager off

\echo '== A. all subjects =='
SELECT s.id, s.name, count(c.id) AS courses
FROM subjects s LEFT JOIN courses c ON c.subject_id = s.id
GROUP BY s.id, s.name ORDER BY s.name;

\echo '== B. doctors teaching Physiology =='
SELECT DISTINCT d.id, d.name, count(*) OVER (PARTITION BY d.id) AS physiology_courses
FROM courses c
JOIN subjects s ON s.id = c.subject_id
JOIN users d ON d.id = c.doctor_id
WHERE s.name = 'Physiology'
ORDER BY d.name;

\echo '== C. Physiology courses taught by a specific doctor (id 28) =='
SELECT c.id, c.title, c.academic_year
FROM courses c
JOIN subjects s ON s.id = c.subject_id
WHERE s.name = 'Physiology' AND c.doctor_id = 28
ORDER BY c.academic_year, c.id;

\echo '== D. ...narrowed to academic_year = 1 =='
SELECT c.id, c.title, c.academic_year, d.name AS doctor
FROM courses c
JOIN subjects s ON s.id = c.subject_id
JOIN users d ON d.id = c.doctor_id
WHERE s.name = 'Physiology' AND c.academic_year = 1
ORDER BY c.id;

\echo '== E. modules of a course (Physiology 1) =='
SELECT m.id, m.position, m.title, count(l.id) AS lectures
FROM modules m
JOIN courses c ON c.id = m.course_id
LEFT JOIN lectures l ON l.module_id = m.id
WHERE c.title LIKE '%Physiology 1%'
GROUP BY m.id, m.position, m.title
ORDER BY m.position;

\echo '== F. lectures of a module (Cardiovascular) =='
SELECT l.id, l.title, COALESCE(MAX(t.end_ts), 0) AS duration_seconds
FROM lectures l
JOIN modules m ON m.id = l.module_id
LEFT JOIN transcript_chunks t ON t.lecture_id = l.id
WHERE m.title = 'Cardiovascular'
GROUP BY l.id, l.title ORDER BY l.id;

\echo '== G. the full filter: doctor + subject + year + module -> lectures =='
-- This is the shape the assistant produces once every field is resolved.
SELECT d.name AS doctor, s.name AS subject, c.academic_year AS yr,
       m.title AS module, l.id AS lecture_id, l.title AS lecture
FROM lectures l
JOIN modules m ON m.id = l.module_id
JOIN courses c ON c.id = l.course_id
JOIN subjects s ON s.id = c.subject_id
JOIN users d ON d.id = c.doctor_id
WHERE d.name LIKE '%أحمد حسن%'
  AND s.name = 'Physiology'
  AND c.academic_year = 1
  AND m.title = 'Renal'
ORDER BY l.id;

\echo '== H. the AMBIGUOUS case: "دكتور أحمد" + Physiology =='
-- Two different doctors, two different years. The assistant must ASK, not pick.
SELECT d.id AS doctor_id, d.name AS doctor, c.id AS course_id, c.title,
       c.academic_year AS yr, count(l.id) AS lectures
FROM courses c
JOIN users d ON d.id = c.doctor_id
JOIN subjects s ON s.id = c.subject_id
LEFT JOIN lectures l ON l.course_id = c.id
WHERE d.role = 'doctor' AND d.name LIKE '%أحمد%' AND s.name = 'Physiology'
GROUP BY d.id, d.name, c.id, c.title, c.academic_year
ORDER BY c.academic_year;

\echo '== H2. every doctor matching "أحمد" at all =='
SELECT d.id, d.name, count(DISTINCT c.id) AS courses,
       string_agg(DISTINCT s.name, ', ') AS subjects
FROM users d
LEFT JOIN courses c ON c.doctor_id = d.id
LEFT JOIN subjects s ON s.id = c.subject_id
WHERE d.role = 'doctor' AND d.name LIKE '%أحمد%'
GROUP BY d.id, d.name ORDER BY d.id;

\echo '== I. enrolment relationships unchanged =='
SELECT c.id, left(c.title, 34) AS course, count(e.student_id) AS enrolled
FROM courses c LEFT JOIN enrollments e ON e.course_id = c.id
GROUP BY c.id, c.title ORDER BY c.id;

\echo '== J. subscription / paywall unchanged: who can open what =='
SELECT u.email AS student, d.name AS doctor,
       count(DISTINCT l.id) AS lectures_unlocked
FROM subscriptions sub
JOIN users u ON u.id = sub.student_id
JOIN users d ON d.id = sub.doctor_id
LEFT JOIN courses c ON c.doctor_id = d.id
LEFT JOIN lectures l ON l.course_id = c.id
GROUP BY u.email, d.name
ORDER BY u.email, d.name
LIMIT 8;

\echo '== J2. a lecture a student may NOT open (no subscription to its doctor) =='
SELECT u.email AS student, l.id AS lecture_id, left(l.title,34) AS lecture,
       d.name AS doctor,
       EXISTS (SELECT 1 FROM subscriptions s
               WHERE s.student_id = u.id AND s.doctor_id = c.doctor_id) AS accessible
FROM users u
CROSS JOIN lectures l
JOIN courses c ON c.id = l.course_id
JOIN users d ON d.id = c.doctor_id
WHERE u.email = 'test.student10@example.com' AND l.module_id IS NOT NULL
ORDER BY accessible, l.id
LIMIT 6;

\echo '== K. RAG data untouched: the real lecture still has its transcript =='
SELECT l.id, left(l.title,34) AS lecture, l.course_id, l.module_id,
       count(t.id) AS chunks, count(t.embedding) AS embedded,
       max(t.end_ts) AS duration_seconds
FROM lectures l JOIN transcript_chunks t ON t.lecture_id = l.id
WHERE t.embedding IS NOT NULL
GROUP BY l.id, l.title, l.course_id, l.module_id;

\echo '== K2. courses still missing catalog metadata (needs backfill) =='
SELECT c.id, c.title, d.name AS doctor,
       c.subject_id, c.academic_year,
       (SELECT count(*) FROM lectures l WHERE l.course_id = c.id) AS lectures
FROM courses c JOIN users d ON d.id = c.doctor_id
WHERE c.subject_id IS NULL OR c.academic_year IS NULL
ORDER BY c.id;

\echo '== K3. lectures not yet filed under a module =='
SELECT l.id, left(l.title,40) AS lecture, l.course_id
FROM lectures l WHERE l.module_id IS NULL ORDER BY l.id;
