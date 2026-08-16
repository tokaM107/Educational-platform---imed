"""Turning an ASR transcript into retrievable, timestamped chunks.

Chunking method: sliding word window *inside* each ASR block.

Why this one, for these transcripts:
  - The ASR output has almost no reliable punctuation (one 5-minute block has
    13 sentence marks across 747 words), so sentence or paragraph splitting
    produces garbage. A word window is stable no matter how the model
    punctuated.
  - A whole 5-minute block (~750 words) is far too coarse to retrieve: the
    student would get a 5-minute jump target, and one embedding would have to
    represent several unrelated ideas.
  - ~120 words at this lecturer's pace (~2.5 words/sec) is ~50 seconds of
    video — a useful "jump here" granularity — and the 25-word overlap stops
    an answer from being cut in half at a chunk boundary.
  - Windows never cross a block header, so start_ts / end_ts stay honest: they
    are interpolated inside the block's own time range.

The module is pure: no printing, no database, no API calls.
"""

import re
from dataclasses import dataclass


# ===== chunk_000.wav | 00:00:00 --> 00:05:00 =====
HEADER = re.compile(
    r"=====\s*(\S+)\s*\|\s*(\d{2}:\d{2}:\d{2})\s*-->\s*(\d{2}:\d{2}:\d{2})\s*====="
)

SENTENCE_END = (".", "،", "؟", "!", "؛", "?")

# Look back this far (in words) for a sentence end before cutting
SNAP_WINDOW = 25

# A trailing window shorter than this is merged into the one before it
MIN_WORDS = 40


@dataclass
class Block:
    """One ASR output block, with the time range it covers in the video."""

    source: str
    start_ts: int
    end_ts: int
    text: str


@dataclass
class Chunk:
    """One retrievable piece of transcript."""

    text: str
    start_ts: int
    end_ts: int
    source: str


def to_seconds(stamp):
    """HH:MM:SS -> seconds."""

    hours, minutes, secs = (int(part) for part in stamp.split(":"))

    return hours * 3600 + minutes * 60 + secs


def to_stamp(seconds):
    """Seconds -> HH:MM:SS."""

    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_blocks(raw):
    """Split transcript text on its '===== chunk_xxx.wav | ... =====' headers."""

    headers = list(HEADER.finditer(raw))
    blocks = []

    for i, header in enumerate(headers):

        body_start = header.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(raw)

        text = raw[body_start:body_end].strip()

        if not text:
            continue

        blocks.append(
            Block(
                source=header.group(1),
                start_ts=to_seconds(header.group(2)),
                end_ts=to_seconds(header.group(3)),
                text=text,
            )
        )

    return blocks


def read_transcript(path):
    """Load a transcript file into blocks."""

    with open(path, "r", encoding="utf-8") as file:
        return parse_blocks(file.read())


def _snap_end(words, start, end, chunk_words):
    """Pull `end` back onto a sentence boundary if one is within SNAP_WINDOW.

    Chunks then break on a pause instead of mid-phrase whenever the ASR did
    give us punctuation, and fall back to the plain window when it did not.
    """

    if end >= len(words):
        return end

    floor = max(end - SNAP_WINDOW, start + chunk_words // 2)

    for i in range(end - 1, floor - 1, -1):
        if words[i].endswith(SENTENCE_END):
            return i + 1

    return end


def chunk_block(block, chunk_words=120, overlap_words=25):
    """One block -> chunks with interpolated timestamps."""

    words = block.text.split()
    total = len(words)

    if total == 0:
        return []

    duration = max(block.end_ts - block.start_ts, 1)

    ranges = []
    start = 0

    while start < total:

        end = _snap_end(
            words,
            start,
            min(start + chunk_words, total),
            chunk_words,
        )

        ranges.append([start, end])

        if end >= total:
            break

        start = end - overlap_words

    if len(ranges) > 1 and ranges[-1][1] - ranges[-1][0] < MIN_WORDS:
        ranges[-2][1] = ranges[-1][1]
        ranges.pop()

    chunks = []

    for start, end in ranges:

        # Words are assumed evenly spaced across the block's time range — the
        # ASR gives us no word-level timings. Worst case the student lands a
        # few seconds early or late inside the right minute.
        start_ts = block.start_ts + round(duration * start / total)
        end_ts = block.start_ts + round(duration * end / total)

        chunks.append(
            Chunk(
                text=" ".join(words[start:end]),
                start_ts=int(start_ts),
                end_ts=int(max(end_ts, start_ts + 1)),
                source=block.source,
            )
        )

    return chunks


def chunk_transcript(blocks, chunk_words=120, overlap_words=25):
    """All blocks -> one flat list of chunks, in video order."""

    chunks = []

    for block in blocks:
        chunks.extend(chunk_block(block, chunk_words, overlap_words))

    return chunks


def describe(chunks):
    """Small summary used by the ingest CLI."""

    if not chunks:
        return "no chunks"

    words = [len(chunk.text.split()) for chunk in chunks]
    spans = [chunk.end_ts - chunk.start_ts for chunk in chunks]

    return (
        f"{len(chunks)} chunks | "
        f"words min {min(words)} / avg {sum(words) / len(words):.0f} / max {max(words)} | "
        f"avg {sum(spans) / len(spans):.0f}s per chunk | "
        f"covers {to_stamp(chunks[0].start_ts)} --> {to_stamp(chunks[-1].end_ts)}"
    )
