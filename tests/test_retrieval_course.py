"""Course fallback retrieval never widens beyond authorized video IDs."""

from app.services import retrieval
from tests.fake_db import FakeConn


def test_course_fallback_search_uses_only_explicit_video_ids():
    conn = FakeConn(lambda sql, params: [
        (21, 8, "Earlier video", "source", 10, 20, .2)
    ] if "c.video_id = ANY" in sql else [])

    passages = retrieval.search_videos(
        conn, [0.1, 0.2], video_ids=[8, 9], top_k=5
    )

    assert passages[0].video_id == 8
    assert passages[0].video_title == "Earlier video"
    params = conn.params_for("c.video_id = ANY")
    assert params["video_ids"] == [8, 9]
    assert params["top_k"] == 5


def test_citation_continuity_can_follow_another_authorized_course_video():
    conn = FakeConn(lambda sql, params: [
        (21, 8, "Earlier video", "source", 10, 20, 0.0)
    ] if "array_position" in sql else [])

    passages = retrieval.by_chunk_ids(conn, chunk_ids=[21], video_ids=[7, 8])

    assert passages[0].video_id == 8
    assert conn.params_for("array_position") == ([7, 8], [21], [21])
