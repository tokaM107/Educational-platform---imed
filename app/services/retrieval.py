"""Vector search over transcript_chunks, plus grouping hits into video segments."""

from dataclasses import dataclass

from app.config import get_settings


@dataclass
class Passage:
    """One retrieved transcript chunk."""

    chunk_id: int
    lecture_id: int
    lecture_title: str
    text: str
    start_ts: int
    end_ts: int
    distance: float


@dataclass
class Segment:
    """A continuous stretch of video that answers the question.

    `start_ts` is where playback jumps to; `end_ts` is where the flag goes.
    Playback is never stopped there — the flag is only a marker.
    """

    lecture_id: int
    lecture_title: str
    start_ts: int
    end_ts: int
    distance: float


SEARCH_SQL = """
    SELECT
        c.id,
        c.lecture_id,
        l.title,
        c.text,
        c.start_ts,
        c.end_ts,
        c.embedding <=> %(query)s AS distance
    FROM transcript_chunks AS c
    JOIN lectures AS l ON l.id = c.lecture_id
    WHERE c.embedding IS NOT NULL
      AND (%(lecture_id)s::int IS NULL OR c.lecture_id = %(lecture_id)s)
    ORDER BY c.embedding <=> %(query)s
    LIMIT %(top_k)s
"""


def search(conn, query_embedding, top_k=None, lecture_id=None):
    """Nearest chunks by cosine distance, closest first."""

    settings = get_settings()

    with conn.cursor() as cur:

        cur.execute(
            SEARCH_SQL,
            {
                "query": query_embedding,
                "lecture_id": lecture_id,
                "top_k": top_k or settings.top_k,
            },
        )

        return [
            Passage(
                chunk_id=row[0],
                lecture_id=row[1],
                lecture_title=row[2],
                text=row[3],
                start_ts=row[4],
                end_ts=row[5],
                distance=float(row[6]),
            )
            for row in cur.fetchall()
        ]


def keep_relevant(passages, max_distance=None):
    """Drop hits that are too far away to be about the question at all."""

    settings = get_settings()
    limit = settings.max_distance if max_distance is None else max_distance

    return [passage for passage in passages if passage.distance <= limit]


def to_segments(passages, merge_gap=None, lead_in=None):
    """Group passages into playable segments.

    Chunks overlap by design and good hits usually sit next to each other, so
    neighbouring passages are merged into one span instead of sending the
    student three jump buttons that all point at the same minute.
    """

    settings = get_settings()

    gap = settings.segment_merge_gap if merge_gap is None else merge_gap
    lead = settings.segment_lead_in if lead_in is None else lead_in

    segments = []

    for passage in sorted(passages, key=lambda item: (item.lecture_id, item.start_ts)):

        merged = None

        for segment in segments:

            if (
                segment.lecture_id == passage.lecture_id
                and passage.start_ts <= segment.end_ts + gap
                and passage.end_ts >= segment.start_ts - gap
            ):
                merged = segment
                break

        if merged is None:

            segments.append(
                Segment(
                    lecture_id=passage.lecture_id,
                    lecture_title=passage.lecture_title,
                    start_ts=passage.start_ts,
                    end_ts=passage.end_ts,
                    distance=passage.distance,
                )
            )

        else:
            merged.start_ts = min(merged.start_ts, passage.start_ts)
            merged.end_ts = max(merged.end_ts, passage.end_ts)
            merged.distance = min(merged.distance, passage.distance)

    # Best match first, and back up a few seconds so playback starts on a
    # sentence rather than mid-word.
    for segment in segments:
        segment.start_ts = max(segment.start_ts - lead, 0)

    return sorted(segments, key=lambda item: item.distance)
