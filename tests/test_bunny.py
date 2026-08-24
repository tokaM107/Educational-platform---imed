"""Choosing what to read from Bunny, and how the audio stage is built.

No network and no ffmpeg. What is worth pinning here is the reasoning: which
rendition gets picked, what happens when there is no MP4 fallback, and that the
HTTP-only ffmpeg flags never reach a local file — the last of which was a real
failure, not a hypothetical one.
"""

import pytest

from rag import audio, bunny


LIBRARY = ("1234", "secret-key", "vz-test.b-cdn.net")


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Pretend Bunny is set up, without needing real credentials."""

    monkeypatch.setattr(bunny, "_config", lambda: LIBRARY)


def video(**overrides):

    base = {
        "guid": "abc-123",
        "status": 4,
        "hasMP4Fallback": True,
        "availableResolutions": "240p,360p,720p",
        "length": 4482,
    }
    base.update(overrides)

    return base


# -------------------------
# Which rendition
# -------------------------


def test_resolutions_are_parsed_smallest_first():

    assert bunny.resolutions(video()) == [240, 360, 720]


def test_resolutions_survive_an_absent_field():
    """Null until encoding has got far enough to know."""

    assert bunny.resolutions(video(availableResolutions=None)) == []


def test_resolutions_ignore_junk_entries():

    assert bunny.resolutions(video(availableResolutions="240p, ,720p,auto")) == [
        240,
        720,
    ]


def test_the_smallest_rendition_is_chosen():
    """Every rendition has the same soundtrack.

    The picture is discarded immediately, so picking 720p over 240p buys
    nothing and costs the difference in bytes — on an hour-long lecture, most
    of the download.
    """

    assert bunny.audio_source_url(video()).endswith("/play_240p.mp4")


def test_the_url_is_built_from_the_pull_zone_and_guid():

    assert (
        bunny.audio_source_url(video())
        == "https://vz-test.b-cdn.net/abc-123/play_240p.mp4"
    )


def test_without_mp4_fallback_it_falls_back_to_hls():
    """MP4 fallback has to be switched on *before* a video is uploaded, so a
    library will often hold videos that never got one."""

    url = bunny.audio_source_url(video(hasMP4Fallback=False))

    assert url == "https://vz-test.b-cdn.net/abc-123/playlist.m3u8"


def test_mp4_fallback_with_no_known_resolutions_also_falls_back():

    url = bunny.audio_source_url(video(availableResolutions=""))

    assert url.endswith("playlist.m3u8")


def test_a_video_with_no_guid_is_refused():

    with pytest.raises(bunny.BunnyError):
        bunny.audio_source_url(video(guid=None))


# -------------------------
# Encoding status
# -------------------------


def test_status_names_cover_the_documented_codes():

    assert bunny.status_name(video(status=0)) == "Created"
    assert bunny.status_name(video(status=4)) == "Finished"
    assert bunny.status_name(video(status=5)) == "Error"


def test_an_unknown_status_is_reported_rather_than_crashing():

    assert "99" in bunny.status_name(video(status=99))


def test_waiting_returns_as_soon_as_encoding_finishes(monkeypatch):

    monkeypatch.setattr(bunny, "get_video", lambda guid, timeout=30: video())

    assert bunny.wait_until_encoded("abc-123", timeout=1)["status"] == 4


def test_waiting_gives_up_on_a_failed_encode(monkeypatch):
    """Polling a video that will never finish until the timeout is not useful."""

    monkeypatch.setattr(bunny, "get_video", lambda guid, timeout=30: video(status=5))

    with pytest.raises(bunny.BunnyError, match="encoding failed"):
        bunny.wait_until_encoded("abc-123", timeout=1)


def test_waiting_times_out_rather_than_hanging(monkeypatch):

    monkeypatch.setattr(bunny, "get_video", lambda guid, timeout=30: video(status=3))
    monkeypatch.setattr(bunny.time, "sleep", lambda seconds: None)

    with pytest.raises(bunny.BunnyError, match="gave up waiting"):
        bunny.wait_until_encoded("abc-123", timeout=0, poll=0)


# -------------------------
# Upload only when there is nothing there
# -------------------------


def test_an_existing_finished_video_is_not_uploaded_again(monkeypatch):
    """Re-running the pipeline should cost the transcription, not the upload."""

    uploads = []

    monkeypatch.setattr(bunny, "get_video", lambda guid, timeout=30: video())
    monkeypatch.setattr(
        bunny, "upload_video", lambda *a, **k: uploads.append(a)
    )

    guid, found = bunny.ensure_uploaded("Lecture", guid="abc-123")

    assert guid == "abc-123"
    assert uploads == []


def test_uploading_with_neither_a_file_nor_a_guid_is_refused():

    with pytest.raises(bunny.BunnyError, match="either an existing guid"):
        bunny.ensure_uploaded("Lecture")


def test_a_new_video_is_created_then_uploaded_then_waited_for(monkeypatch):

    calls = []

    monkeypatch.setattr(
        bunny, "create_video", lambda title, **k: calls.append("create") or "new-guid"
    )
    monkeypatch.setattr(
        bunny, "upload_video", lambda guid, path, **k: calls.append("upload")
    )
    monkeypatch.setattr(
        bunny, "wait_until_encoded", lambda guid, **k: calls.append("wait") or video()
    )

    guid, _ = bunny.ensure_uploaded("Lecture", path="/tmp/x.mp4")

    assert guid == "new-guid"
    assert calls == ["create", "upload", "wait"]


# -------------------------
# The ffmpeg flags that broke on a local file
# -------------------------


def test_http_sources_get_the_reconnect_flags():
    """An hour of audio across a CDN must survive a dropped connection."""

    assert "-reconnect" in audio._reconnect_options("https://vz.b-cdn.net/a/b.mp4")


def test_local_sources_get_none_of_them():
    """`-reconnect` belongs to ffmpeg's http protocol.

    Passed alongside a local path it does not degrade — it fails the entire
    command with "Option reconnect not found" before opening anything.
    """

    assert audio._reconnect_options("data/videos/sample1.mp4") == []
    assert audio._reconnect_options("/absolute/path.mp4") == []


def test_the_scheme_check_is_case_insensitive():

    assert audio._reconnect_options("HTTPS://vz.b-cdn.net/a.mp4") != []


def test_chunk_positions_come_from_the_index():
    """Each chunk's place in the lecture is its index times the length, so one
    failed chunk cannot shift every timestamp after it."""

    assert audio.chunk_start_seconds(0) == 0
    assert audio.chunk_start_seconds(3) == 900
    assert audio.chunk_start_seconds(3, chunk_seconds=60) == 180


# -------------------------
# Listing the library (batch)
# -------------------------


def fake_pages(monkeypatch, pages, total):
    """Serve `pages` one call at a time, recording which pages were asked for."""

    asked = []

    def list_videos(page=1, per_page=100, **filters):
        asked.append(page)
        index = page - 1
        return (pages[index] if index < len(pages) else []), total

    monkeypatch.setattr(bunny, "list_videos", list_videos)

    return asked


def test_iter_videos_walks_every_page(monkeypatch):
    """A library larger than one page must not be silently truncated."""

    pages = [[video(guid=f"a{i}") for i in range(3)],
             [video(guid=f"b{i}") for i in range(2)]]

    asked = fake_pages(monkeypatch, pages, total=5)

    guids = [v["guid"] for v in bunny.iter_videos(per_page=3)]

    assert guids == ["a0", "a1", "a2", "b0", "b1"]
    assert asked == [1, 2]


def test_iter_videos_stops_once_the_total_is_reached(monkeypatch):
    """The total is what ends it — otherwise it pages forever against a server
    that keeps returning the last page."""

    asked = fake_pages(monkeypatch, [[video()]], total=1)

    list(bunny.iter_videos(per_page=100))

    assert asked == [1]


def test_iter_videos_stops_on_an_empty_page(monkeypatch):
    """Belt and braces: a wrong total must not become an infinite loop."""

    asked = fake_pages(monkeypatch, [[]], total=999)

    assert list(bunny.iter_videos()) == []
    assert asked == [1]


def test_iter_videos_is_lazy(monkeypatch):
    """Stopping early stops fetching."""

    pages = [[video(guid="a")], [video(guid="b")]]
    asked = fake_pages(monkeypatch, pages, total=2)

    first = next(iter(bunny.iter_videos(per_page=1)))

    assert first["guid"] == "a"
    assert asked == [1]          # page 2 was never requested


# -------------------------
# Choosing a rendition
# -------------------------


def test_lowest_and_highest():

    assert bunny.pick_height(video(), "lowest") == 240
    assert bunny.pick_height(video(), "highest") == 720


def test_an_exact_request_is_honoured():

    assert bunny.pick_height(video(), 360) == 360


def test_a_request_above_the_source_falls_to_what_exists():
    """Bunny does not upscale: asking 1080 of a 720p source cannot be met."""

    assert bunny.pick_height(video(), 1080) == 720


def test_a_request_below_everything_gets_the_smallest():

    assert bunny.pick_height(video(), 100) == 240


def test_pick_height_with_no_renditions_is_none():

    assert bunny.pick_height(video(availableResolutions=None)) is None


def test_rendition_url_honours_the_preference():

    url = bunny.rendition_url(video(), prefer="highest")

    assert url == "https://vz-test.b-cdn.net/abc-123/play_720p.mp4"


def test_audio_uses_the_smallest_rendition_and_video_analysis_can_ask_otherwise():
    """The two names exist so the choice is explicit at the call site."""

    assert bunny.audio_source_url(video()).endswith("play_240p.mp4")
    assert bunny.rendition_url(video(), "highest").endswith("play_720p.mp4")


def test_playlist_url_is_always_available():

    assert bunny.playlist_url(video()) == (
        "https://vz-test.b-cdn.net/abc-123/playlist.m3u8"
    )


# -------------------------
# Readiness
# -------------------------


def test_is_finished_only_for_status_four():

    assert bunny.is_finished(video(status=4)) is True
    assert bunny.is_finished(video(status=3)) is False
    assert bunny.is_finished(video(status=5)) is False
