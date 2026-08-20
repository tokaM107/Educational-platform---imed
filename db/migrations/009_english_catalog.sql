-- Catalog identifiers in English.
--
-- The search assistant matches a name the student typed against the name stored
-- here. Two scripts cannot both be right about which alphabet that is, so the
-- database picks one: every value a search can filter on — a person's name, a
-- course, module or lecture title, a subject, a topic — is English.
--
-- What stays in Arabic, on purpose:
--
--   transcript_chunks.text   what the doctor actually said. Translating it would
--                            destroy the thing the RAG answers are grounded in.
--   query_embeddings.query   a hash-keyed cache of questions students asked.
--                            Input, like the transcript.
--   notifications, reports, report_narratives
--                            generated prose. These are written by the prompts
--                            in app/services/prompts.py, which write Egyptian
--                            Arabic because the interface is Egyptian Arabic.
--                            Rewriting the stored copies would be undone by the
--                            next report; that is a prompt decision, not a data
--                            one.
--
-- Doctors lose the "د." prefix rather than gaining a "Dr." one: users.role
-- already says who is a doctor, and a title inside the name column is one more
-- thing every match has to step over.
--
-- Idempotent: matched on the exact Arabic value, so a second run updates nothing
-- and a row someone has since renamed by hand is left alone.

BEGIN;

UPDATE users SET name = 'Ahmed Selim'        WHERE name = 'د. أحمد سليم';
UPDATE users SET name = 'Ahmed Mahmoud Sayed' WHERE name = 'أحمد محمود سيد';
UPDATE users SET name = 'Ahmed Hassan'       WHERE name = 'د. أحمد حسن';
UPDATE users SET name = 'Ahmed Mahmoud'      WHERE name = 'د. أحمد محمود';
UPDATE users SET name = 'Mona Abdelrahman'   WHERE name = 'د. منى عبد الرحمن';

UPDATE users SET name = 'Sara Ibrahim Mohamed'  WHERE name = 'سارة إبراهيم محمد';
UPDATE users SET name = 'Omar Khaled Fouad'     WHERE name = 'عمر خالد فؤاد';
UPDATE users SET name = 'Nourhan Mostafa Ali'   WHERE name = 'نورهان مصطفى علي';
UPDATE users SET name = 'Youssef Hassan Eldeeb' WHERE name = 'يوسف حسن الديب';
UPDATE users SET name = 'Mariam Adel Shaker'    WHERE name = 'مريم عادل شاكر';
UPDATE users SET name = 'Karim Samir Abdallah'  WHERE name = 'كريم سمير عبد الله';
UPDATE users SET name = 'Hebatallah Nasser'     WHERE name = 'هبة الله ناصر';
UPDATE users SET name = 'Mahmoud Anwar Zaki'    WHERE name = 'محمود أنور زكي';
UPDATE users SET name = 'Reem Tarek Saeed'      WHERE name = 'ريم طارق سعيد';
UPDATE users SET name = 'Ahmed Fathy Elgendy'   WHERE name = 'أحمد فتحي الجندي';

-- Courses 1 and 11 are both "تشريح ١". They stay distinguishable: 1 is the real
-- course with the ingested lecture, 11 is the empty duplicate.
UPDATE courses SET title = 'Anatomy 1 — Musculoskeletal System'
    WHERE title = 'تشريح ١ — الجهاز الهيكلي والعضلي';
UPDATE courses SET title = 'Anatomy 1'              WHERE title = 'تشريح ١';
UPDATE courses SET title = '[TEST] Anatomy 1'       WHERE title = '[TEST] Anatomy 1 — تشريح ١';
UPDATE courses SET title = '[TEST] Histology 1'     WHERE title = '[TEST] Histology 1 — أنسجة ١';
UPDATE courses SET title = '[TEST] Physiology 1'    WHERE title = '[TEST] Physiology 1 — فسيولوجي ١';
UPDATE courses SET title = '[TEST] Physiology 2'    WHERE title = '[TEST] Physiology 2 — فسيولوجي ٢';
UPDATE courses SET title = '[TEST] Biochemistry 1'  WHERE title = '[TEST] Biochemistry 1 — كيمياء حيوية ١';

UPDATE lectures SET title = 'Skeletal System'
    WHERE title = 'الجهاز الهيكلي — Skeletal System';

UPDATE topics SET name = 'Bone Classification' WHERE name = 'تصنيف العظام';

COMMIT;
