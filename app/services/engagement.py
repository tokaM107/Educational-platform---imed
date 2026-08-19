"""Engagement numbers reconstructed from the rows in `video_events`.

Three quantities get confused constantly, so they are kept strictly apart:

    watch time        seconds the video was actually playing
    session duration  wall clock from the session's first event to its last
    time away         wall clock the lecture page spent hidden

A student can have a 180-minute session on a 75-minute lecture with 68 minutes
of watch time and 42 minutes away from the page. All four numbers are true
together, and none is a substitute for another.

Watch time cannot be read off the video positions. `last video_ts - first
video_ts` looks like the obvious answer and is wrong: jumping from 00:01:40 to
00:08:20 adds six minutes of video position and zero seconds of watching, and
rewatching a stretch adds nothing at all. So playback is replayed instead —
`created_at` says how much real time passed between two events, and the event
types say whether the video was running through it.

The replay is deliberately pure: it takes a list of rows and returns numbers,
with the database reads kept in separate functions, so every case below is
testable without Postgres (tests/test_engagement.py).
"""

from datetime import datetime
from typing import NamedTuple


# Must match HEARTBEAT_MS in app/static/app.js.
HEARTBEAT_SECONDS = 30

# While the video plays, a heartbeat lands every HEARTBEAT_SECONDS, so two
# consecutive events cannot legitimately be much further apart than that. A
# longer gap means events went missing — the laptop slept, the network dropped,
# the tab was killed — and those seconds were almost certainly not spent
# watching. One gap is credited at most this much, which tolerates two dropped
# heartbeats before it starts under-counting.
MAX_PLAYING_GAP = 3 * HEARTBEAT_SECONDS

# Stretches of lecture shorter than this are noise, not a finding: a two-second
# hole between two spans is rounding, and reporting it as "you skipped this"
# would bury the real gaps.
MIN_SPAN_SECONDS = 30


class Event(NamedTuple):
    """One row of `video_events`, only the columns the replay needs."""

    event_type: str
    video_ts: float
    created_at: datetime
    session_id: str = ""


class Span(NamedTuple):
    """A stretch of the lecture, measured in video seconds."""

    start: float
    end: float

    @property
    def seconds(self):
        return max(self.end - self.start, 0.0)


class Engagement(NamedTuple):
    """Everything the replay can establish from the events themselves."""

    watch_time_seconds: float
    time_away_seconds: float
    session_duration_seconds: float
    pause_count: int
    seek_count: int
    rewatch_count: int
    completed: bool

    # Where in the lecture the watching happened, in playback order and
    # deliberately not merged — watching 10:00-15:00 twice is two spans, which
    # is what makes rewatching visible.
    watched_spans: tuple = ()


def replay(events, max_gap=MAX_PLAYING_GAP):
    """Reconstruct one session's playback intervals and add them up.

    Walks the events in time order holding two pieces of state: whether the
    video was playing, and when the interval being measured started. Every
    event closes the stretch since the previous one — credited as watch time
    only if the video was running through it — and then opens a new one.

    That falls out naturally for each transition:

    play/heartbeat  the video is running from here. A heartbeat also repairs a
                    session whose `play` never reached the server.
    pause/complete  stop counting.
    seek/skip       the interval before the jump has just been credited; a new
                    one starts here, at a different point in the video. The
                    jump itself contributes nothing.
    tab_hidden      the video element usually keeps playing in a hidden tab,
                    but the student is not watching it, so this closes the
                    interval and starts the away clock.
    tab_visible     ends the away clock, and resumes playback if the page was
                    hidden mid-play — coming back does not re-fire `play` when
                    the element never actually paused.

    A session that was never closed off (browser shut mid-playback, laptop
    slept) is credited only up to its last recorded event, so watch time is a
    lower bound by at most one heartbeat interval. A page that went hidden and
    never came back adds nothing to time away either: there is no second
    timestamp to measure the absence against, and inventing one would be a
    guess dressed up as data.

    Real elapsed time is what gets counted, so the numbers assume 1x playback.

    `max_gap` caps how much a single gap between events may contribute; pass
    None to credit gaps in full.
    """

    # Stable sort, so events sharing a timestamp keep the order they arrived in
    # (the caller reads them ordered by created_at, id).
    ordered = sorted(events, key=lambda event: event.created_at)

    watch = 0.0
    away = 0.0
    spans = []

    playing = False
    anchor = None            # start of the stretch currently being measured
    anchor_ts = 0.0          # where the playhead was when that stretch began
    hidden_at = None         # when the page went away
    playing_when_hidden = False

    for event in ordered:

        moment = event.created_at

        if playing and anchor is not None:

            elapsed = max((moment - anchor).total_seconds(), 0.0)

            if max_gap is not None:
                elapsed = min(elapsed, max_gap)

            watch += elapsed

            # The same seconds, located in the lecture: the playhead started at
            # anchor_ts and ran forward for `elapsed`. Taken from real time
            # rather than from the next event's video_ts, because that one may
            # have jumped (a seek) or drifted (a hidden tab).
            if elapsed > 0:
                spans.append(Span(anchor_ts, anchor_ts + elapsed))

        anchor = moment

        # Always the position of the event just read, so the next stretch is
        # measured from where the playhead actually is — after a seek, and
        # after a hidden tab let the video run on without us.
        anchor_ts = max(event.video_ts, 0.0)

        if event.event_type in ("play", "heartbeat"):
            playing = True

        elif event.event_type in ("pause", "complete"):
            playing = False

        elif event.event_type == "tab_hidden":
            playing_when_hidden = playing
            playing = False
            hidden_at = moment

        elif event.event_type == "tab_visible":

            if hidden_at is not None:
                away += max((moment - hidden_at).total_seconds(), 0.0)
                hidden_at = None

            playing = playing_when_hidden
            playing_when_hidden = False

        # seek / skip / rewatch_segment: the state carries over unchanged, and
        # the re-anchor above has already started the next interval.

    types = [event.event_type for event in ordered]

    span = 0.0

    if len(ordered) >= 2:
        span = (ordered[-1].created_at - ordered[0].created_at).total_seconds()

    return Engagement(
        watch_time_seconds=round(watch, 1),
        time_away_seconds=round(away, 1),
        session_duration_seconds=round(span, 1),
        pause_count=types.count("pause"),
        seek_count=types.count("seek"),
        rewatch_count=types.count("rewatch_segment"),
        completed="complete" in types,
        watched_spans=tuple(spans),
    )


def merge_spans(spans, join_gap=1.0):
    """Union of overlapping spans, earliest first.

    `join_gap` closes hairline cracks: two spans a fraction of a second apart
    are one continuous stretch of lecture, not two.
    """

    merged = []

    for span in sorted(spans):

        if merged and span.start <= merged[-1].end + join_gap:
            merged[-1] = Span(merged[-1].start, max(merged[-1].end, span.end))
        else:
            merged.append(span)

    return merged


def covered_seconds(spans):
    """How much of the lecture was seen at least once, rewatching counted once.

    Not the same as watch time: watching one minute five times is five minutes
    of watching and one minute of coverage.
    """

    return round(sum(span.seconds for span in merge_spans(spans)), 1)


def missing_spans(spans, duration, min_length=MIN_SPAN_SECONDS):
    """Stretches of the lecture that were never watched.

    The holes left by the merged coverage, which is where "what should I go
    back to" actually comes from — a skipped ten minutes is a real gap in the
    material, however long the student sat in front of the rest.
    """

    if not duration or duration <= 0:
        return []

    gaps = []
    cursor = 0.0

    for span in merge_spans(spans):

        if span.start - cursor >= min_length:
            gaps.append(Span(cursor, span.start))

        cursor = max(cursor, span.end)

    if duration - cursor >= min_length:
        gaps.append(Span(cursor, duration))

    return gaps


def repeated_spans(spans, min_length=MIN_SPAN_SECONDS):
    """Stretches watched more than once — where the student had to go back.

    A sweep over the span boundaries: depth counts how many playthroughs cover
    the current position, and anything at depth two or more was replayed.
    Rewinding to hear an explanation again is the most useful signal in the
    whole table, so it is reported as a location rather than a count.
    """

    edges = []

    for span in spans:

        if span.seconds > 0:
            edges.append((span.start, 1))
            edges.append((span.end, -1))

    # Starts before ends at the same position. One playthrough arrives as spans
    # that *touch* — a heartbeat every 30 s cuts it into 0-30, 30-60, 60-90 —
    # and closing 0-30 before opening 30-60 drops the depth to 1 for an instant
    # at every boundary. Under a second playthrough that instant splits the
    # replayed region into sub-`min_length` fragments, all of which are then
    # discarded, and a genuine rewind reports nothing at all. Ordering starts
    # first keeps a continuous viewing continuous; a lone pass still cannot
    # reach depth 2, so nothing is invented.
    edges.sort(key=lambda edge: (edge[0], -edge[1]))

    repeated = []
    depth = 0
    opened_at = None

    for position, change in edges:

        was_repeat = depth >= 2
        depth += change

        if depth >= 2 and not was_repeat:
            opened_at = position

        elif was_repeat and depth < 2 and opened_at is not None:

            # The length test is `> 0` as well as `>= min_length`: at a boundary
            # shared by an end and a start the depth touches 2 and drops again
            # at the same position, which is an instant and not a stretch. The
            # default minimum hides that anyway; the explicit guard means it
            # stays true for any minimum a caller passes.
            if position > opened_at and position - opened_at >= min_length:
                repeated.append(Span(opened_at, position))

            opened_at = None

    return merge_spans(repeated)


def watch_time_seconds(events, max_gap=MAX_PLAYING_GAP):
    """Just the watch time, for callers that need nothing else."""

    return replay(events, max_gap=max_gap).watch_time_seconds


def by_session(events):
    """Group events by session id, keeping each session in arrival order."""

    grouped = {}

    for event in events:
        grouped.setdefault(event.session_id, []).append(event)

    return grouped


def session_totals(events, max_gap=MAX_PLAYING_GAP):
    """Each session replayed on its own, keyed by session id.

    Useful when the numbers need attributing to something finer than the whole
    week — which day the studying happened on, for instance.
    """

    return {
        session_id: replay(rows, max_gap=max_gap)
        for session_id, rows in by_session(events).items()
    }


def replay_sessions(events, max_gap=MAX_PLAYING_GAP):
    """Totals across every session present in `events`.

    Each session_id is replayed on its own. Two sessions that overlap in time —
    the same lecture open in two tabs, or a phone and a laptop — must not be
    stitched into one long playback. The per-session numbers are then added, so
    `session_duration_seconds` is total time spent in sessions rather than the
    span from the very first event to the very last.
    """

    totals = list(session_totals(events, max_gap=max_gap).values())

    return Engagement(
        watch_time_seconds=round(sum(item.watch_time_seconds for item in totals), 1),
        time_away_seconds=round(sum(item.time_away_seconds for item in totals), 1),
        session_duration_seconds=round(
            sum(item.session_duration_seconds for item in totals), 1
        ),
        pause_count=sum(item.pause_count for item in totals),
        seek_count=sum(item.seek_count for item in totals),
        rewatch_count=sum(item.rewatch_count for item in totals),
        completed=any(item.completed for item in totals),
        # Coverage is a property of the lecture, not of one sitting: finishing
        # on Tuesday what you started on Monday covers the lecture once.
        watched_spans=tuple(
            span for item in totals for span in item.watched_spans
        ),
    )


FETCH_SQL = """
    SELECT
        event_type,
        video_ts,
        created_at,
        session_id
    FROM video_events
    WHERE student_id = %(student_id)s
      AND lecture_id = %(lecture_id)s
      AND (%(session_id)s::varchar IS NULL OR session_id = %(session_id)s)
      AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
      AND (%(until)s::timestamptz IS NULL OR created_at < %(until)s)
    ORDER BY created_at, id
"""


DURATION_SQL = """
    SELECT COALESCE(MAX(end_ts), 0)
    FROM transcript_chunks
    WHERE lecture_id = %s
"""


def fetch_events(conn, student_id, lecture_id, session_id=None, since=None, until=None):
    """This student's events on this lecture, oldest first.

    `since` / `until` bound the window on `created_at` (half-open, so a weekly
    report and the next one cannot both claim the same event). A session that
    straddles the boundary is cut at it.
    """

    with conn.cursor() as cur:

        cur.execute(
            FETCH_SQL,
            {
                "student_id": student_id,
                "lecture_id": lecture_id,
                "session_id": session_id,
                "since": since,
                "until": until,
            },
        )

        # video_ts and session_id are NOT NULL in db/schema.sql, but a database
        # created before that was added may hold rows without them; a legacy row
        # should not take the endpoint down.
        return [
            Event(
                event_type=row[0],
                video_ts=float(row[1] if row[1] is not None else 0.0),
                created_at=row[2],
                session_id=row[3] or "",
            )
            for row in cur.fetchall()
        ]


def lecture_duration(conn, lecture_id):
    """Lecture length in seconds, or 0 when it is not known yet.

    Taken from the transcript's last timestamp — the same value /api/lectures
    already reports as `duration_ts`, so the two never disagree. It is the end
    of the transcribed audio rather than the container duration, so a lecture
    with a silent tail reads a little short, and a lecture that has not been
    ingested reads 0.
    """

    with conn.cursor() as cur:
        cur.execute(DURATION_SQL, (lecture_id,))
        row = cur.fetchone()

    return float(row[0]) if row else 0.0


def summarise(conn, student_id, lecture_id, session_id=None):
    """Everything the analytics endpoint returns, as a plain dict.

    With a `session_id`, one browser session. Without one, every session this
    student has had on the lecture, each replayed separately and then added up.
    """

    events = fetch_events(conn, student_id, lecture_id, session_id)

    duration = lecture_duration(conn, lecture_id)

    totals = (
        replay(events) if session_id is not None else replay_sessions(events)
    )

    return {
        "student_id": student_id,
        "lecture_id": lecture_id,
        "session_id": session_id,
        "lecture_duration": duration,
        "watch_time_seconds": totals.watch_time_seconds,
        "watch_percentage": (
            round(totals.watch_time_seconds / duration * 100, 1)
            if duration
            else None
        ),
        "time_away_seconds": totals.time_away_seconds,
        "session_duration_seconds": totals.session_duration_seconds,
        "pause_count": totals.pause_count,
        "seek_count": totals.seek_count,
        "rewatch_count": totals.rewatch_count,
        "completed": totals.completed,
    }
