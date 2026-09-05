"""Writing the transcript format that rag/chunking.py parses.

    ===== block_000 | 00:00:00 --> 00:05:00 =====

This is a contract, not a detail. Every timestamp the product shows a student —
the second a citation seeks the player to — is read back out of one of these
headers by chunking.parse_blocks. A producer that gets the header wrong does
not fail loudly; it produces a transcript whose blocks are silently dropped, or
worse, one whose timestamps point at the wrong minute of the lecture.

transcribe_cohere.py writes its own copy of this format inline, because it
writes each block the moment that chunk finishes so an ASR failure at chunk
twelve leaves the first twelve on disk. This module is for producers that have
all their blocks before they write anything — currently the RunPod client,
which receives the whole transcript in one response.
"""

from pathlib import Path


def timestamp(seconds):
    """Seconds -> HH:MM:SS."""

    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def write_blocks(blocks, output_path):
    """Write (index, start_ts, end_ts, text) blocks to a transcript file.

    Truncated on entry rather than appended to. Appending was an early bug in
    transcribe_cohere.py and it silently doubled the transcript on a re-run,
    which downstream reads as a lecture where every passage occurs twice at two
    different timestamps.
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file:

        for index, start_ts, end_ts, text in blocks:

            if not str(text).strip():
                # An empty body makes chunking.parse_blocks skip the block
                # anyway; writing it would only leave a header claiming to
                # cover a stretch of lecture that has no words in it.
                continue

            file.write(
                f"\n\n===== block_{index:03d} | "
                f"{timestamp(start_ts)} --> {timestamp(end_ts)} =====\n\n"
            )
            file.write(str(text).strip())
            file.write("\n")

    return output_path
