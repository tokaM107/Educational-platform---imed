-- Enable the pgvector extension (needed for the embedding column below).
-- Requires a Postgres image that includes pgvector, e.g. pgvector/pgvector:pg16
-- CREATE EXTENSION IF NOT EXISTS vector;

-- People: students and doctors
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    role VARCHAR(20) NOT NULL CHECK (role IN ('student', 'doctor')),
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Subject tags, e.g. "Cardiology basics"
CREATE TABLE topics (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL
);

-- One row per lecture
CREATE TABLE lectures (
    id SERIAL PRIMARY KEY,
    doctor_id INTEGER NOT NULL REFERENCES users(id),
    title VARCHAR(255) NOT NULL,
    video_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Transcript, cut into small searchable pieces with timestamps
CREATE TABLE transcript_chunks (
    id SERIAL PRIMARY KEY,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    start_ts INTEGER NOT NULL,   -- seconds into the video
    end_ts INTEGER NOT NULL,
    embedding vector(1536)       -- match this to your embedding model's output size
);

-- Every pause / skip / completion a student does while watching
CREATE TABLE video_events (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    event_type VARCHAR(20) NOT NULL CHECK (
        event_type IN ('play', 'pause', 'seek', 'skip', 'complete', 'rewatch_segment')
    ),
    video_ts INTEGER,
    session_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quiz questions
CREATE TABLE questions (
    id SERIAL PRIMARY KEY,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    topic_id INTEGER REFERENCES topics(id),
    stem TEXT NOT NULL,
    options JSONB NOT NULL,        -- e.g. ["A) ...", "B) ...", "C) ...", "D) ..."]
    correct_option VARCHAR(5) NOT NULL,
    difficulty VARCHAR(20)
);

-- Every time a student answers a question
CREATE TABLE question_attempts (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    is_correct BOOLEAN NOT NULL,
    answered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for the lookups you'll run most (by student, by lecture)
CREATE INDEX idx_video_events_student ON video_events(student_id);
CREATE INDEX idx_video_events_lecture ON video_events(lecture_id);
CREATE INDEX idx_question_attempts_student ON question_attempts(student_id);
CREATE INDEX idx_transcript_chunks_lecture ON transcript_chunks(lecture_id);