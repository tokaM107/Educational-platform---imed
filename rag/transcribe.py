"""Course-item video -> transcript, with the video living on Bunny Stream.

    python -m rag.transcribe --video-id 11

    # everything up to the audio, without loading the ASR model
    python -m rag.transcribe --video-id 11 --audio-only

What changed from the old script: the video is no longer read from
data/videos/. It is uploaded to Bunny once, and from then on the pipeline reads
its audio straight off the CDN — so re-transcribing a lecture needs nothing on
this machine, and neither does transcribing one somebody else uploaded.

The Bunny guid is read from `course_items.video_ref`. Video creation and upload
belong to the Nest API, so this pipeline never mutates the shared catalog.

Ordering matters here and the steps are deliberately separate: uploading is
slow and idempotent, encoding is slow and out of our hands, extraction is slow
and cacheable, and transcription is the expensive one. A failure in the last
should never cost the first three again.
"""

import argparse

from app.config import get_settings
from app.db import connection
from rag import bunny
from rag.audio import CHUNK_SECONDS, iter_audio_chunks


def parse_args(argv=None):

    parser = argparse.ArgumentParser(
        description="Upload a lecture to Bunny Stream and transcribe it."
    )

    parser.add_argument("--video-id", type=int, required=True)
    parser.add_argument("--output", help="transcript path")
    parser.add_argument("--chunk-seconds", type=int, default=CHUNK_SECONDS)
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="stop after cutting the chunks; do not load the ASR model",
    )
    parser.add_argument(
        "--keep-audio",
        metavar="DIR",
        help="write the chunks here instead of a temporary directory and "
             "leave them behind (debugging only — they are not needed again)",
    )

    return parser.parse_args(argv)


# -------------------------
# The lecture row
# -------------------------


def read_video(conn, video_id):

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, video_provider, video_ref
            FROM course_items WHERE id = %s AND type = 'video'
            """,
            (video_id,),
        )
        return cur.fetchone()


# -------------------------
# The pipeline
# -------------------------


def report_progress(status, progress):
    print(f"   encoding: {status} {progress}%")


def main(argv=None):

    args = parse_args(argv)
    settings = get_settings()

    with connection() as conn:

        item = read_video(conn, args.video_id)

        if item is None:
            raise SystemExit(f"no course-item video with id {args.video_id}")

        _, title, provider, guid = item
        if provider not in (None, "bunny"):
            raise SystemExit(f"video provider {provider!r} is not supported")

        print(f"Video {args.video_id}: {title}")

        if not guid:
            raise SystemExit(f"video {args.video_id} has no video_ref")

        print(f"1. Bunny video {guid}")

        guid, video = bunny.ensure_uploaded(
            title=title,
            path=None,
            guid=guid,
            on_progress=report_progress,
        )

    length = video.get("length") or 0
    print(
        f"2. Encoded: {bunny.resolutions(video) or 'unknown'} "
        f"({length // 60}m{length % 60:02d}s)"
    )

    if not bunny.is_finished(video):
        raise SystemExit(
            f"video {guid} is {bunny.status_name(video)}, not Finished — "
            "nothing to read yet"
        )

    source = bunny.audio_source_url(video)

    # The smallest rendition, because only the soundtrack is wanted and every
    # rendition carries the same one. Handed to ffmpeg as a URL: the video is
    # read where it lives and never lands on this machine.
    print(f"3. Streaming audio from {source.rsplit('/', 1)[-1]}")

    chunks = iter_audio_chunks(
        source,
        chunk_seconds=args.chunk_seconds,
        workspace=args.keep_audio,
    )

    if args.audio_only:
        # Still one at a time. --audio-only is for checking that the source
        # reads and cuts correctly, not for collecting the audio.
        for chunk in chunks:
            print(
                f"   chunk {chunk.index:03d}  "
                f"{chunk.start_seconds:>6.0f}s –{chunk.end_seconds:>7.0f}s  "
                f"{chunk.path.stat().st_size / 1024 / 1024:.1f} MB"
            )
        print("\nStopping before transcription (--audio-only).")
        return

    output = args.output or (
        settings.transcript_dir / f"video_{args.video_id}.txt"
    )

    # Imported here, not at module level: it pulls in transformers and a
    # multi-gigabyte model, and --audio-only should not pay for either.
    from rag.transcribe_cohere import transcribe_chunks

    print(f"4. Transcribing (one {args.chunk_seconds}s chunk at a time)…")

    # The generator is passed rather than a list on purpose: each chunk is
    # written, transcribed and deleted before the next one is cut, so the audio
    # on disk never exceeds one chunk and none of it survives the run.
    transcribe_chunks(chunks, output_path=output)

    print(
        f"\nNext: python -m rag.ingest --video-id {args.video_id} "
        f"--transcript {output}"
    )


if __name__ == "__main__":
    main()
