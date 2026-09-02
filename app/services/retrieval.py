"""Vector search over transcript_chunks, plus grouping hits into video segments."""

from dataclasses import dataclass

from app.config import get_settings


@dataclass
class Passage:
    """One retrieved transcript chunk."""

    chunk_id: int
    video_id: int
    video_title: str
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

    video_id: int
    video_title: str
    start_ts: int
    end_ts: int
    distance: float


SEARCH_SQL = """
    SELECT
        c.id,
        c.video_id,
        item.title,
        c.text,
        c.start_ts,
        c.end_ts,
        c.embedding <=> %(query)s AS distance
    FROM transcript_chunks AS c
    JOIN course_items AS item ON item.id = c.video_id AND item.type = 'video'
    WHERE c.embedding IS NOT NULL
      AND (%(video_id)s::int IS NULL OR c.video_id = %(video_id)s)
    ORDER BY c.embedding <=> %(query)s
    LIMIT %(top_k)s
"""

COURSE_FALLBACK_SQL = """
    SELECT
        c.id,
        c.video_id,
        item.title,
        c.text,
        c.start_ts,
        c.end_ts,
        c.embedding <=> %(query)s AS distance
    FROM transcript_chunks AS c
    JOIN course_items AS item ON item.id = c.video_id AND item.type = 'video'
    WHERE c.embedding IS NOT NULL
      AND c.video_id = ANY(%(video_ids)s)
    ORDER BY c.embedding <=> %(query)s
    LIMIT %(top_k)s
"""


def _passages(rows):
    return [
        Passage(
            chunk_id=row[0], video_id=row[1], video_title=row[2], text=row[3],
            start_ts=row[4], end_ts=row[5], distance=float(row[6]),
        )
        for row in rows
    ]


def search(conn, query_embedding, top_k=None, video_id=None):
    """Nearest chunks by cosine distance, closest first."""

    settings = get_settings()

    with conn.cursor() as cur:

        cur.execute(
            SEARCH_SQL,
            {
                "query": query_embedding,
                "video_id": video_id,
                "top_k": top_k or settings.top_k,
            },
        )

        return _passages(cur.fetchall())


def search_videos(conn, query_embedding, video_ids, top_k=None):
    """Nearest chunks from an explicit, already-authorized set of videos."""

    if not video_ids:
        return []
    settings = get_settings()
    with conn.cursor() as cur:
        cur.execute(COURSE_FALLBACK_SQL, {
            "query": query_embedding,
            "video_ids": list(video_ids),
            "top_k": top_k or settings.top_k,
        })
        return _passages(cur.fetchall())


def by_chunk_ids(conn, chunk_ids, video_ids):
    """Previously cited context, hard-scoped to authorized course videos."""
    if not chunk_ids or not video_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.video_id, item.title, c.text,
                   c.start_ts, c.end_ts, 0.0 AS distance
            FROM transcript_chunks AS c
            JOIN course_items AS item
              ON item.id = c.video_id AND item.type = 'video'
            WHERE c.video_id = ANY(%s) AND c.id = ANY(%s)
            ORDER BY array_position(%s::bigint[], c.id)
            """,
            (list(video_ids), list(chunk_ids), list(chunk_ids)),
        )
        return [
            Passage(
                chunk_id=row[0], video_id=row[1], video_title=row[2],
                text=row[3], start_ts=row[4], end_ts=row[5], distance=float(row[6]),
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

    for passage in sorted(passages, key=lambda item: (item.video_id, item.start_ts)):

        merged = None

        for segment in segments:

            if (
                segment.video_id == passage.video_id
                and passage.start_ts <= segment.end_ts + gap
                and passage.end_ts >= segment.start_ts - gap
            ):
                merged = segment
                break

        if merged is None:

            segments.append(
                Segment(
                    video_id=passage.video_id,
                    video_title=passage.video_title,
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
