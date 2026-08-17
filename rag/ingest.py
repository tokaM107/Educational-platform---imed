"""Offline pipeline: transcript file -> chunks -> embeddings -> Postgres.

    python -m rag.ingest --lecture-id 1 --title "Anatomy — Skeletal System" \
        --transcript data/transcripts/transcript.txt --video sample1.mp4

    python -m rag.ingest --dry-run     # chunk only: no API calls, no database

Re-running replaces that lecture's chunks instead of piling up duplicates.
"""

import argparse
import sys

from app.config import get_settings
from app.db import close_pool, connection
from app.services.embeddings import Embedder
from rag import chunking


def parse_args(argv=None):

    settings = get_settings()

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--transcript",
        default=str(settings.transcript_dir / "transcript.txt"),
    )
    parser.add_argument("--lecture-id", type=int, default=1)
    parser.add_argument("--title", default="Anatomy — Skeletal System")
    parser.add_argument(
        "--video",
        default="sample1.mp4",
        help="file name inside data/videos",
    )
    parser.add_argument("--doctor-id", type=int, default=None)
    parser.add_argument("--chunk-words", type=int, default=settings.chunk_words)
    parser.add_argument("--overlap-words", type=int, default=settings.overlap_words)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="chunk and print, without embedding or writing",
    )
    parser.add_argument(
        "--reembed",
        action="store_true",
        help="ignore stored embeddings and compute every chunk again",
    )

    return parser.parse_args(argv)


def ensure_lecture(conn, lecture_id, title, video, doctor_id=None):
    """Create or update the lecture row the chunks hang off."""

    with conn.cursor() as cur:

        if doctor_id is None:

            cur.execute(
                "SELECT id FROM users WHERE role = 'doctor' ORDER BY id LIMIT 1"
            )
            row = cur.fetchone()

            if row:
                doctor_id = row[0]

            else:
                cur.execute(
                    """
                    INSERT INTO users (role, name, email)
                    VALUES ('doctor', 'Test Doctor', 'doctor@example.com')
                    RETURNING id
                    """
                )
                doctor_id = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO lectures (id, doctor_id, title, video_url)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
                SET title = EXCLUDED.title,
                    video_url = EXCLUDED.video_url
            """,
            (lecture_id, doctor_id, title, video),
        )

        # Keep the SERIAL sequence ahead of any id we forced in
        cur.execute(
            """
            SELECT setval(
                pg_get_serial_sequence('lectures', 'id'),
                GREATEST((SELECT MAX(id) FROM lectures), 1)
            )
            """
        )

    conn.commit()


def load_existing_embeddings(conn, lecture_id):
    """text -> stored vector, for chunks already in the database.

    Chunking is deterministic, so re-running ingest after a small transcript
    edit should only pay for the chunks whose text actually changed.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT text, embedding
            FROM transcript_chunks
            WHERE lecture_id = %s AND embedding IS NOT NULL
            """,
            (lecture_id,),
        )

        return {row[0]: row[1] for row in cur.fetchall()}


def replace_chunks(conn, lecture_id, chunks, embeddings):
    """Swap in a fresh set of chunks for this lecture, in one transaction."""

    with conn.cursor() as cur:

        cur.execute(
            "DELETE FROM transcript_chunks WHERE lecture_id = %s",
            (lecture_id,),
        )

        cur.executemany(
            """
            INSERT INTO transcript_chunks
            (lecture_id, text, start_ts, end_ts, embedding)
            VALUES (%s, %s, %s, %s, %s)
            """,
            [
                (
                    lecture_id,
                    chunk.text,
                    chunk.start_ts,
                    chunk.end_ts,
                    embedding,
                )
                for chunk, embedding in zip(chunks, embeddings)
            ],
        )

    conn.commit()


def main(argv=None):

    args = parse_args(argv)

    blocks = chunking.read_transcript(args.transcript)

    chunks = chunking.chunk_transcript(
        blocks,
        chunk_words=args.chunk_words,
        overlap_words=args.overlap_words,
    )

    print(f"{args.transcript}: {len(blocks)} blocks")
    print(chunking.describe(chunks))

    if not chunks:
        print("Nothing to ingest.")
        return 1

    if args.dry_run:

        print("\nFirst 2 chunks:\n")

        for chunk in chunks[:2]:
            print(
                f"[{chunking.to_stamp(chunk.start_ts)} --> "
                f"{chunking.to_stamp(chunk.end_ts)}]"
            )
            print(chunk.text[:300], "...\n")

        return 0

    def progress(done, total):
        print(f"  embedded {done}/{total}")

    try:
        with connection() as conn:

            ensure_lecture(
                conn,
                args.lecture_id,
                args.title,
                args.video,
                args.doctor_id,
            )

            # Vectors already in the database are reused as-is; only new or
            # edited text is sent to the embedding API.
            stored = {} if args.reembed else load_existing_embeddings(
                conn, args.lecture_id
            )

            missing = [chunk for chunk in chunks if chunk.text not in stored]

            print(
                f"\n{len(chunks) - len(missing)} chunks reuse a stored embedding, "
                f"{len(missing)} need embedding"
            )

            if missing:

                print("Embedding (throttled to stay inside the free-tier quota)...")

                vectors = Embedder().embed_documents(
                    [chunk.text for chunk in missing],
                    progress=progress,
                )

                stored.update(
                    (chunk.text, vector) for chunk, vector in zip(missing, vectors)
                )

            embeddings = [stored[chunk.text] for chunk in chunks]

            replace_chunks(conn, args.lecture_id, chunks, embeddings)

    finally:
        close_pool()

    print(f"\nStored {len(chunks)} chunks for lecture {args.lecture_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
