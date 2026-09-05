"""Transcript blocks -> retrievable chunks, without ever touching the disk.

This is the path the RunPod worker uses: JSON off an HTTP response straight
into the chunker. The application server writes no transcript file, which is
why there is no file-cleanup story on this side to get wrong.
"""

from rag import ingest
from tests.fake_db import FakeConn


def blocks(*entries):
    return [
        (index, start, end, text)
        for index, (start, end, text) in enumerate(entries)
    ]


def test_the_asr_blocks_are_re_cut_rather_than_used_as_chunks():
    """A five-minute block is far too coarse to retrieve.

    One embedding would stand for several unrelated ideas, and a citation
    would seek the student to the top of a five-minute stretch.
    """

    words = " ".join(f"كلمة{i}" for i in range(600))

    chunks = ingest.chunks_from_blocks(blocks((0, 300, words)))

    assert len(chunks) > 1
    assert all(len(chunk.text.split()) <= 200 for chunk in chunks)


def test_timestamps_stay_inside_the_block_they_came_from():
    """What makes a citation land on the sentence, not the block."""

    words = " ".join(f"كلمة{i}" for i in range(400))

    chunks = ingest.chunks_from_blocks(blocks((300, 600, words)))

    assert all(300 <= chunk.start_ts <= 600 for chunk in chunks)
    assert all(chunk.end_ts > chunk.start_ts for chunk in chunks)


def test_chunks_come_out_in_video_order():
    a = " ".join(f"أ{i}" for i in range(200))
    b = " ".join(f"ب{i}" for i in range(200))

    chunks = ingest.chunks_from_blocks(blocks((0, 300, a), (300, 600, b)))

    assert chunks == sorted(chunks, key=lambda chunk: chunk.start_ts)


def test_an_empty_block_is_dropped_rather_than_chunked():
    chunks = ingest.chunks_from_blocks(blocks((0, 300, "   "), (300, 600, "نص")))

    assert all(chunk.text.strip() for chunk in chunks)


def test_stored_chunks_carry_the_video_id(monkeypatch):
    """Without it, course-video retrieval cannot see the transcript at all."""

    written = {}

    monkeypatch.setattr(ingest, "require_video", lambda conn, video_id: (video_id, "t"))
    monkeypatch.setattr(ingest, "load_existing_embeddings", lambda conn, video_id: {})
    monkeypatch.setattr(
        ingest, "Embedder",
        lambda: type("E", (), {"embed_documents": lambda self, texts, progress=None:
                               [[0.0] * 4 for _ in texts]})(),
    )
    monkeypatch.setattr(
        ingest, "replace_chunks",
        lambda conn, video_id, chunks, embeddings: written.update(
            video_id=video_id, count=len(chunks)
        ),
    )

    words = " ".join(f"كلمة{i}" for i in range(300))
    count = ingest.ingest_blocks(FakeConn(), 77, blocks((0, 300, words)))

    assert written["video_id"] == 77
    assert count == written["count"] > 0


def test_replacing_chunks_deletes_before_inserting():
    """A retried job must replace its chunks, not add a second copy."""

    import inspect

    source = inspect.getsource(ingest.replace_chunks)

    assert source.index("DELETE FROM transcript_chunks") < source.index("INSERT INTO")


def test_an_empty_transcript_raises_rather_than_storing_nothing():
    """Storing zero chunks would mark the video transcribed and unsearchable."""

    import pytest

    with pytest.raises(RuntimeError, match="no chunks"):
        ingest.ingest_blocks(FakeConn(), 11, [])
