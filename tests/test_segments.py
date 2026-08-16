"""Turning retrieved passages into the video segment the player jumps to."""

from app.services import retrieval


def passage(chunk_id, start, end, distance, lecture_id=1):

    return retrieval.Passage(
        chunk_id=chunk_id,
        lecture_id=lecture_id,
        lecture_title="Lecture",
        text="…",
        start_ts=start,
        end_ts=end,
        distance=distance,
    )


def test_neighbouring_hits_become_one_segment():
    """Overlapping chunks must not turn into three buttons on the same minute."""

    segments = retrieval.to_segments(
        [
            passage(1, 1000, 1050, 0.25),
            passage(2, 1040, 1090, 0.28),
            passage(3, 1080, 1130, 0.30),
        ],
        merge_gap=20,
        lead_in=0,
    )

    assert len(segments) == 1
    assert (segments[0].start_ts, segments[0].end_ts) == (1000, 1130)
    assert segments[0].distance == 0.25


def test_far_apart_hits_stay_separate_and_are_ranked_by_distance():

    segments = retrieval.to_segments(
        [
            passage(1, 1000, 1050, 0.31),
            passage(2, 3000, 3050, 0.22),
        ],
        merge_gap=20,
        lead_in=0,
    )

    assert [segment.start_ts for segment in segments] == [3000, 1000]


def test_different_lectures_never_merge():

    segments = retrieval.to_segments(
        [
            passage(1, 1000, 1050, 0.25, lecture_id=1),
            passage(2, 1010, 1060, 0.26, lecture_id=2),
        ],
        merge_gap=20,
        lead_in=0,
    )

    assert len(segments) == 2


def test_lead_in_backs_up_playback_without_going_negative():

    segments = retrieval.to_segments(
        [passage(1, 2, 60, 0.25)],
        merge_gap=20,
        lead_in=3,
    )

    assert segments[0].start_ts == 0

    segments = retrieval.to_segments(
        [passage(1, 100, 160, 0.25)],
        merge_gap=20,
        lead_in=3,
    )

    assert segments[0].start_ts == 97

    # The flag stays exactly where the explanation ends
    assert segments[0].end_ts == 160


def test_keep_relevant_drops_far_hits():

    passages = [passage(1, 0, 50, 0.20), passage(2, 100, 150, 0.80)]

    kept = retrieval.keep_relevant(passages, max_distance=0.45)

    assert [item.chunk_id for item in kept] == [1]
