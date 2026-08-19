"""Turning video events into watch time, time away and session duration."""

from datetime import datetime, timedelta, timezone

from app.services import engagement


START = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)


def event(kind, at, video_ts, session_id="s1"):
    """One row of video_events.

    `at` is seconds of real time into the session, `video_ts` the position in
    the video. Keeping the two apart is the whole subject of these tests.
    """

    return engagement.Event(
        event_type=kind,
        video_ts=float(video_ts),
        created_at=START + timedelta(seconds=at),
        session_id=session_id,
    )


def test_play_to_pause_counts_real_elapsed_time():

    totals = engagement.replay(
        [
            event("play", 0, 100),
            event("pause", 30, 130),
        ]
    )

    assert totals.watch_time_seconds == 30
    assert totals.pause_count == 1
    assert totals.completed is False


def test_a_seek_is_not_watch_time():
    """Jumping forward moves the playhead, not the clock."""

    totals = engagement.replay(
        [
            event("play", 0, 100),
            event("seek", 20, 500),      # skipped 380 s of video instantly
            event("pause", 30, 510),
        ]
    )

    # 20 s before the jump, 10 s after. The naive last-minus-first video_ts
    # would have claimed 410.
    assert totals.watch_time_seconds == 30
    assert totals.seek_count == 1


def test_seeking_backwards_counts_the_stretch_twice():
    """Rewatching is real watching, even though the positions do not advance."""

    totals = engagement.replay(
        [
            event("play", 0, 0),
            event("seek", 60, 0),        # back to the beginning
            event("pause", 120, 60),
        ]
    )

    # The student only ever saw the first minute of video, but sat through it
    # twice; last-minus-first video_ts would have claimed 60.
    assert totals.watch_time_seconds == 120


def test_hidden_page_is_time_away_not_watch_time():
    """The element keeps playing in a hidden tab; the student is not watching."""

    totals = engagement.replay(
        [
            event("play", 0, 0),
            event("tab_hidden", 30, 30),
            event("tab_visible", 630, 630),   # ten minutes of video went past
            event("pause", 660, 660),
        ]
    )

    # 30 s before leaving, 30 s after coming back. The ten minutes that played
    # to an invisible tab count for nothing.
    assert totals.watch_time_seconds == 60
    assert totals.time_away_seconds == 600
    assert totals.session_duration_seconds == 660


def test_coming_back_resumes_playback_without_a_play_event():
    """visibilitychange does not re-fire `play` when the element never paused."""

    totals = engagement.replay(
        [
            event("play", 0, 0),
            event("tab_hidden", 10, 10),
            event("tab_visible", 40, 40),
            event("heartbeat", 70, 70),
        ]
    )

    assert totals.watch_time_seconds == 40


def test_a_pause_before_leaving_is_not_resumed_on_return():

    totals = engagement.replay(
        [
            event("play", 0, 0),
            event("pause", 10, 10),
            event("tab_hidden", 20, 10),
            event("tab_visible", 320, 10),
            event("play", 350, 10),
            event("pause", 380, 40),
        ]
    )

    assert totals.watch_time_seconds == 40      # 10 + 30, nothing in between
    assert totals.time_away_seconds == 300


def test_heartbeats_carry_a_long_uninterrupted_playback():

    events = [event("play", 0, 0)]
    events += [event("heartbeat", n, n) for n in range(30, 301, 30)]
    events.append(event("pause", 310, 310))

    assert engagement.watch_time_seconds(events) == 310


def test_a_gap_with_no_heartbeat_is_not_credited_in_full():
    """Play, an hour of silence, pause: the laptop slept, nobody watched."""

    totals = engagement.replay(
        [
            event("play", 0, 0),
            event("pause", 3600, 3600),
        ]
    )

    assert totals.watch_time_seconds == engagement.MAX_PLAYING_GAP
    assert totals.session_duration_seconds == 3600


def test_repeated_play_pause_cycles_and_completion():

    totals = engagement.replay(
        [
            event("play", 0, 0),
            event("pause", 30, 30),
            event("play", 100, 30),
            event("pause", 130, 60),
            event("play", 200, 60),
            event("complete", 230, 90),
        ]
    )

    assert totals.watch_time_seconds == 90      # the idle gaps do not count
    assert totals.pause_count == 2
    assert totals.completed is True


def test_a_session_that_was_never_closed_stops_at_its_last_event():
    """Browser shut mid-playback: credit what was recorded, guess nothing."""

    totals = engagement.replay(
        [
            event("play", 0, 0),
            event("heartbeat", 30, 30),
            event("heartbeat", 60, 60),
        ]
    )

    assert totals.watch_time_seconds == 60
    assert totals.completed is False


def test_a_page_hidden_and_never_returned_adds_no_time_away():
    """There is no second timestamp to measure the absence against."""

    totals = engagement.replay(
        [
            event("play", 0, 0),
            event("tab_hidden", 30, 30),
        ]
    )

    assert totals.watch_time_seconds == 30
    assert totals.time_away_seconds == 0


def test_session_duration_watch_time_and_time_away_stay_separate():
    """The three clocks measure different things and must not collapse."""

    events = [event("play", 0, 0)]

    # Ten minutes of real watching, heartbeat every 30 s.
    events += [event("heartbeat", n, n) for n in range(30, 600, 30)]
    events.append(event("pause", 600, 600))

    # Then the tab was left alone, hidden for half an hour.
    events.append(event("tab_hidden", 900, 600))
    events.append(event("tab_visible", 2700, 600))

    totals = engagement.replay(events)

    assert totals.watch_time_seconds == 600
    assert totals.time_away_seconds == 1800
    assert totals.session_duration_seconds == 2700


def test_each_session_is_replayed_on_its_own():
    """Two tabs open at once must not be stitched into one long playback."""

    events = [
        event("play", 0, 0, "a"),
        event("play", 10, 0, "b"),
        event("pause", 30, 30, "a"),
        event("pause", 40, 30, "b"),
    ]

    totals = engagement.replay_sessions(events)

    assert totals.watch_time_seconds == 60          # 30 in each session
    assert totals.session_duration_seconds == 60    # summed, not spanned
    assert totals.pause_count == 2


def test_watched_spans_locate_the_watching_in_the_lecture():

    spans = engagement.replay(
        [
            event("play", 0, 100),
            event("seek", 30, 600),      # skipped 100-130 -> 600
            event("pause", 90, 660),
        ]
    ).watched_spans

    assert [(span.start, span.end) for span in spans] == [
        (100.0, 130.0),
        (600.0, 660.0),
    ]


def test_coverage_counts_a_rewatched_stretch_once():
    """Watch time and coverage are different questions about the same minute."""

    events = [
        event("play", 0, 0),
        event("seek", 60, 0),        # back to the start
        event("pause", 120, 60),
    ]

    totals = engagement.replay(events)

    assert totals.watch_time_seconds == 120                     # sat through it twice
    assert engagement.covered_seconds(totals.watched_spans) == 60   # saw one minute


def test_missing_spans_are_the_parts_never_watched():

    spans = [engagement.Span(0, 600), engagement.Span(1800, 2400)]

    gaps = engagement.missing_spans(spans, duration=3000)

    assert [(gap.start, gap.end) for gap in gaps] == [(600.0, 1800.0), (2400.0, 3000.0)]


def test_a_hairline_gap_is_not_reported_as_skipped():
    """A two-second hole is rounding, not a finding."""

    spans = [engagement.Span(0, 1200), engagement.Span(1202, 2400)]

    assert engagement.missing_spans(spans, duration=2400) == []


def test_repeated_spans_find_where_the_student_went_back():

    spans = [
        engagement.Span(0, 1200),        # first pass
        engagement.Span(600, 900),       # rewound and replayed five minutes
    ]

    repeated = engagement.repeated_spans(spans)

    assert [(span.start, span.end) for span in repeated] == [(600.0, 900.0)]


def test_touching_heartbeat_intervals_do_not_hide_a_rewind():
    """The shape real player data always has, and the one that used to fail.

    A heartbeat every 30 s cuts one viewing into spans that *touch*: 1200-1230,
    1230-1260, 1260-1270. Sorting the sweep's edges naively closes 1200-1230
    before it opens 1230-1260, so the depth dips to 1 for an instant at every
    boundary. Under a second playthrough those instants chopped the replayed
    stretch into sub-30 s fragments, every one of which was then discarded as
    noise — so a genuine 60-second rewind reported nothing at all.
    """

    first_pass = [
        engagement.Span(1200, 1230),
        engagement.Span(1230, 1260),
        engagement.Span(1260, 1270),
    ]
    second_pass = [engagement.Span(1210, 1240), engagement.Span(1240, 1300)]

    repeated = engagement.repeated_spans(first_pass + second_pass)

    assert [(span.start, span.end) for span in repeated] == [(1210.0, 1270.0)]


def test_a_boundary_shared_by_three_spans_is_still_continuous():
    """An end, a start and another start landing on the same second."""

    spans = [
        engagement.Span(0, 600),        # ends exactly where the next two begin
        engagement.Span(600, 1200),
        engagement.Span(600, 1200),     # the replay
    ]

    repeated = engagement.repeated_spans(spans)

    assert [(span.start, span.end) for span in repeated] == [(600.0, 1200.0)]


def test_touching_spans_alone_never_look_like_a_repeat():
    """Ordering starts first must not invent an overlap out of one viewing.

    At each shared boundary the depth briefly reads 2, which is exactly the
    condition being tested for — but it opens and closes at the same position,
    so the stretch has zero length and cannot survive the minimum.
    """

    one_viewing = [engagement.Span(i, i + 30) for i in range(0, 3600, 30)]

    assert engagement.repeated_spans(one_viewing) == []
    assert engagement.repeated_spans(one_viewing, min_length=0) == []


def test_a_rewind_shorter_than_the_minimum_is_still_ignored():
    """The fix must not lower the bar for what counts as a replayed stretch."""

    spans = [engagement.Span(0, 600), engagement.Span(580, 1200)]

    assert engagement.repeated_spans(spans) == []                      # 20 s
    assert len(engagement.repeated_spans(spans, min_length=10)) == 1


def test_a_single_pass_repeats_nothing():

    assert engagement.repeated_spans([engagement.Span(0, 3600)]) == []


def test_no_events_is_all_zeroes():

    totals = engagement.replay([])

    assert totals.watch_time_seconds == 0
    assert totals.session_duration_seconds == 0
    assert totals.completed is False
