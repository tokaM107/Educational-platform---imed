"""Offline pipeline: transcript file -> chunks -> embeddings -> Postgres.

    python -m rag.ingest --video-id 11 \
        --transcript data/transcripts/video_11.txt

    python -m rag.ingest --dry-run     # chunk only: no API calls, no database

Re-running replaces that video's chunks instead of piling up duplicates.
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
    parser.add_argument("--video-id", type=int, required=True)
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


def require_video(conn, video_id):
    """Return the existing course-item video; catalog creation belongs to Nest."""

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT id, title FROM course_items
            WHERE id = %s AND type = 'video'
            """,
            (video_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise SystemExit(f"no course-item video with id {video_id}")

    return row


def load_existing_embeddings(conn, video_id):
    """text -> stored vector, for chunks already in the database.

    Chunking is deterministic, so re-running ingest after a small transcript
    edit should only pay for the chunks whose text actually changed.
    """

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT text, embedding
            FROM transcript_chunks
            WHERE video_id = %s AND embedding IS NOT NULL
            """,
            (video_id,),
        )

        return {row[0]: row[1] for row in cur.fetchall()}


def replace_chunks(conn, video_id, chunks, embeddings):
    """Swap in a fresh set of chunks for this video, in one transaction."""

    with conn.cursor() as cur:

        cur.execute(
            "DELETE FROM transcript_chunks WHERE video_id = %s",
            (video_id,),
        )

        cur.executemany(
            """
            INSERT INTO transcript_chunks
            (video_id, text, start_ts, end_ts, embedding)
            VALUES (%s, %s, %s, %s, %s)
            """,
            [
                (
                    video_id,
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

            _, title = require_video(conn, args.video_id)
            print(f"Video {args.video_id}: {title}")

            # Vectors already in the database are reused as-is; only new or
            # edited text is sent to the embedding API.
            stored = {} if args.reembed else load_existing_embeddings(
                conn, args.video_id
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

            replace_chunks(conn, args.video_id, chunks, embeddings)

    finally:
        close_pool()

    print(f"\nStored {len(chunks)} chunks for video {args.video_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
