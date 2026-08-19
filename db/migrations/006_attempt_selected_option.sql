-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/006_attempt_selected_option.sql

-- Which option the student actually picked.
--
-- Until now an attempt recorded only whether it was right. That is enough to
-- score a student and useless for improving a question: "38% got it wrong" does
-- not say whether the class split evenly across the three distractors (the
-- question is simply hard) or whether 34% of them chose the same wrong option
-- (that distractor is teaching them something false, or the stem is ambiguous).
-- The second case is the most actionable finding a post-exam review produces,
-- and it needs the choice itself.
--
-- Nullable, because every attempt recorded before this column existed has no
-- answer to give and inventing one would be a lie. The instructor view reports
-- how many attempts carry a choice, so a partly-filled column never reads as a
-- complete picture.
ALTER TABLE question_attempts ADD COLUMN IF NOT EXISTS selected_option VARCHAR(5);

-- The distribution is grouped per question, over the attempts that have one.
CREATE INDEX IF NOT EXISTS idx_question_attempts_option
    ON question_attempts (question_id, selected_option)
    WHERE selected_option IS NOT NULL;
