"""Lecture video -> transcript, with the video living on Bunny Stream.

    # a lecture that is still a local file: upload it, then transcribe
    python -m rag.transcribe --lecture-id 1 --video sample1.mp4

    # a lecture already in the Stream library
    python -m rag.transcribe --lecture-id 1 --bunny-video-id <guid>

    # everything up to the audio, without loading the ASR model
    python -m rag.transcribe --lecture-id 1 --video sample1.mp4 --audio-only

What changed from the old script: the video is no longer read from
data/videos/. It is uploaded to Bunny once, and from then on the pipeline reads
its audio straight off the CDN — so re-transcribing a lecture needs nothing on
this machine, and neither does transcribing one somebody else uploaded.

The guid is written back to `lectures.bunny_video_id`, which is what makes the
upload happen exactly once. Run this twice on the same lecture and the second
run skips straight to the audio.

Ordering matters here and the steps are deliberately separate: uploading is
slow and idempotent, encoding is slow and out of our hands, extraction is slow
and cacheable, and transcription is the expensive one. A failure in the last
should never cost the first three again.
"""

import argparse
from pathlib import Path

from app.config import get_settings
from app.db import connection
from rag import bunny
from rag.audio import CHUNK_SECONDS, iter_audio_chunks


def parse_args(argv=None):

    parser = argparse.ArgumentParser(
        description="Upload a lecture to Bunny Stream and transcribe it."
    )

    parser.add_argument("--lecture-id", type=int, required=True)
    parser.add_argument(
        "--video",
        help="file name inside data/videos (or a path), to upload if the "
             "lecture is not on Bunny yet",
    )
    parser.add_argument(
        "--bunny-video-id",
        help="use this existing Bunny video instead of uploading",
    )
    parser.add_argument("--title", help="title for a newly created Bunny video")
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


def read_lecture(conn, lecture_id):

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, video_url, bunny_video_id FROM lectures WHERE id = %s",
            (lecture_id,),
        )
        return cur.fetchone()


def save_bunny_id(conn, lecture_id, guid):

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE lectures SET bunny_video_id = %s WHERE id = %s",
            (guid, lecture_id),
        )
    conn.commit()


def resolve_local_video(name):
    """A --video argument as a path. A bare name means data/videos/<name>."""

    settings = get_settings()

    candidate = Path(name)

    if not candidate.is_absolute() and not candidate.exists():
        candidate = settings.video_dir / candidate.name

    if not candidate.is_file():
        raise SystemExit(f"no such video file: {candidate}")

    return candidate


# -------------------------
# The pipeline
# -------------------------


def report_progress(status, progress):
    print(f"   encoding: {status} {progress}%")


def main(argv=None):

    args = parse_args(argv)
    settings = get_settings()

    with connection() as conn:

        lecture = read_lecture(conn, args.lecture_id)

        if lecture is None:
            raise SystemExit(f"no lecture with id {args.lecture_id}")

        _, title, video_url, stored_guid = lecture

        # Explicit argument wins, then whatever the row already knows. Only when
        # neither exists does anything get uploaded.
        guid = args.bunny_video_id or stored_guid

        print(f"Lecture {args.lecture_id}: {title}")

        if guid:
            print(f"1. Bunny video {guid} (already uploaded)")
            local = None
        else:
            local = resolve_local_video(args.video or video_url or "")
            size_mb = local.stat().st_size / 1024 / 1024
            print(f"1. Uploading {local.name} ({size_mb:.0f} MB) to Bunny…")

        guid, video = bunny.ensure_uploaded(
            title=args.title or title,
            path=local,
            guid=guid,
            on_progress=report_progress,
        )

        if guid != stored_guid:
            save_bunny_id(conn, args.lecture_id, guid)
            print(f"   saved lectures.bunny_video_id = {guid}")

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
        settings.transcript_dir / f"lecture_{args.lecture_id}.txt"
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
        f"\nNext: python -m rag.ingest --lecture-id {args.lecture_id} "
        f"--transcript {output}"
    )


if __name__ == "__main__":
    main()
