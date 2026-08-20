-- A lecture is taught by the doctor who teaches its course.
--
-- scripts/seed_test_data.py always inserts a lecture with its course's doctor,
-- but `upsert_lecture` matches an existing row by title and never updates
-- doctor_id — so when a re-run assigned a course to a different doctor, the
-- course moved and its lectures did not. Eight rows drifted apart that way.
--
-- It is not cosmetic. The catalog search reaches a lecture's doctor through
-- lectures.doctor_id, so "عايز محاضرات د. منى" returned nothing at all while
-- her four Histology lectures sat under another doctor's name.
--
-- Only rows that disagree are touched, and only where the lecture has a course
-- to inherit from. A standalone lecture keeps whoever recorded it.

BEGIN;

UPDATE lectures l
SET doctor_id = c.doctor_id
FROM courses c
WHERE c.id = l.course_id
  AND l.doctor_id <> c.doctor_id;

COMMIT;
