"""Audio is streamed, not stored.

The pipeline reads an hour of lecture off a CDN and transcribes it five minutes
at a time. The thing to hold it to is not that it produces the right transcript
— that is the model's job — but that it does so without the video or the
full-length audio ever existing on this machine, and without leaving anything
behind when it fails.

These tests use real ffmpeg on small generated tones, because the properties
under test (how many files exist at once, what survives an exception) are
properties of the process and the filesystem, not of anything that can be
usefully faked. The ASR model is stubbed: it is several gigabytes and has no
bearing on where the bytes go.
"""

import subprocess
import wave
from pathlib import Path

import pytest

from rag import audio


CHUNK = 300


def tone(path, seconds, rate=16000):
    """A small audio file to stand in for a lecture."""

    subprocess.run(
        [
            "ffmpeg", "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={seconds}",
            "-c:a", "aac", "-b:a", "48k", "-ar", str(rate),
            "-y", str(path),
        ],
        capture_output=True,
        check=True,
    )

    return path


@pytest.fixture(scope="module")
def twelve_minutes(tmp_path_factory):

    return tone(tmp_path_factory.mktemp("src") / "lecture.m4a", 720)


@pytest.fixture(scope="module")
def one_minute(tmp_path_factory):

    return tone(tmp_path_factory.mktemp("src") / "short.m4a", 60)


# -------------------------
# Global timestamps
# -------------------------


def test_a_twelve_minute_video_yields_the_expected_global_ranges(twelve_minutes):
    """0–300, 300–600, 600–720. The last chunk is short and says so."""

    ranges = [
        (chunk.start_seconds, round(chunk.end_seconds))
        for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK)
    ]

    assert ranges == [(0, 300), (300, 600), (600, 720)]


def test_chunks_arrive_in_order(twelve_minutes):

    indexes = [
        chunk.index
        for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK)
    ]

    assert indexes == [0, 1, 2]


def test_a_short_lecture_is_one_short_chunk(one_minute):
    """Nothing pads it out to five minutes, and the duration is the real one."""

    chunks = list(audio.iter_audio_chunks(one_minute, chunk_seconds=CHUNK))

    assert len(chunks) == 1
    assert chunks[0].start_seconds == 0
    assert 59 <= chunks[0].duration_seconds <= 61


def test_start_seconds_come_from_the_index_not_from_measurement(twelve_minutes):
    """A chunk that decodes fractionally short must not shift the next one."""

    for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):
        assert chunk.start_seconds == chunk.index * CHUNK


# -------------------------
# Nothing is stored
# -------------------------


def test_only_one_chunk_exists_on_disk_at_a_time(twelve_minutes):
    """The whole point. A 74-minute lecture used to leave 274 MB behind."""

    counts = []

    for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):
        counts.append(len(list(chunk.path.parent.glob("chunk_*.wav"))))

    assert counts == [1, 1, 1]


def test_a_chunk_is_deleted_before_the_next_one_appears(twelve_minutes):

    previous = None

    for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):

        if previous is not None:
            assert not previous.exists(), f"{previous.name} outlived its turn"

        previous = chunk.path

    assert not previous.exists()


def test_the_workspace_is_removed_when_the_run_finishes(twelve_minutes):

    directories = {
        chunk.path.parent
        for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK)
    }

    assert directories
    assert all(not directory.exists() for directory in directories)


def test_no_full_length_audio_is_ever_written(twelve_minutes):
    """Every file in the workspace is one chunk's worth or less.

    The old pipeline wrote the entire lecture as a wav first and cut it
    afterwards. Nothing here may hold more than `chunk_seconds` of audio.
    """

    limit = audio.bytes_per_second() * CHUNK + 1024      # + wav header slack

    for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):
        for present in chunk.path.parent.glob("*"):
            if present.suffix == ".wav":
                assert present.stat().st_size <= limit


def test_the_source_is_never_copied_into_the_workspace(twelve_minutes):
    """The video stays on Bunny. Only wavs are written here."""

    for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):
        written = {p.suffix for p in chunk.path.parent.iterdir()}
        assert written <= {".wav", ".log"}


def test_nothing_is_written_under_the_project_data_directory(twelve_minutes, tmp_path):
    """Temporary audio must not land in data/audios, which is not temporary."""

    for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):
        assert "data/audios" not in str(chunk.path)
        assert Path(chunk.path).is_absolute()


# -------------------------
# Cleanup on failure
# -------------------------


def test_the_workspace_is_removed_when_the_consumer_raises(twelve_minutes):
    """A model error must not leave audio behind."""

    directory = None

    with pytest.raises(RuntimeError, match="model exploded"):
        for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):
            directory = chunk.path.parent
            raise RuntimeError("model exploded")

    assert directory is not None
    assert not directory.exists()


def test_the_workspace_is_removed_when_the_consumer_stops_early(twelve_minutes):
    """Cancellation, in the form the generator actually sees it."""

    stream = audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK)

    first = next(stream)
    directory = first.path.parent

    stream.close()          # GeneratorExit -> the finally block runs

    assert not directory.exists()


def test_ffmpeg_is_not_left_running_after_an_early_stop(twelve_minutes):
    """An abandoned reader would otherwise hold a CDN connection open."""

    import psutil

    before = len([p for p in psutil.process_iter(["name"]) if p.info["name"] == "ffmpeg"])

    stream = audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK)
    next(stream)
    stream.close()

    after = len([p for p in psutil.process_iter(["name"]) if p.info["name"] == "ffmpeg"])

    assert after <= before


def test_an_unreadable_source_raises_audio_error_and_cleans_up():

    stream = audio.iter_audio_chunks("no-such-file.mp4", chunk_seconds=CHUNK)

    with pytest.raises(audio.AudioError):
        list(stream)


def test_a_missing_ffmpeg_is_reported_clearly(monkeypatch, twelve_minutes):

    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(audio.subprocess, "Popen", missing)

    with pytest.raises(audio.AudioError, match="not installed"):
        list(audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK))


# -------------------------
# Format the ASR expects
# -------------------------


def test_chunks_are_mono_16khz_16bit_wav(twelve_minutes):
    """Unchanged from what the transcription model was already given."""

    for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):

        with wave.open(str(chunk.path), "rb") as handle:
            assert handle.getnchannels() == 1
            assert handle.getframerate() == 16000
            assert handle.getsampwidth() == 2

        break


def test_a_chunk_holds_the_length_it_claims(twelve_minutes):

    for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):

        with wave.open(str(chunk.path), "rb") as handle:
            frames = handle.getnframes() / handle.getframerate()

        assert frames == pytest.approx(chunk.duration_seconds, abs=0.01)


# -------------------------
# Sources
# -------------------------


def test_a_local_file_still_works(one_minute):
    """The pre-Bunny path is unchanged: ffmpeg does not care which it is."""

    chunks = list(audio.iter_audio_chunks(one_minute, chunk_seconds=CHUNK))

    assert len(chunks) == 1


def test_the_source_is_handed_to_ffmpeg_untouched():
    """MP4 and HLS alike: neither is downloaded, both are opened in place."""

    for url in (
        "https://vz-test.b-cdn.net/abc-123/play_240p.mp4",
        "https://vz-test.b-cdn.net/abc-123/playlist.m3u8",
    ):
        command = audio._pcm_command(url, 16000)

        assert command[command.index("-i") + 1] == url
        assert "pipe:1" in command


def test_http_sources_stream_to_a_pipe_rather_than_a_file():
    """`pipe:1` is what makes this a stream. A filename here would be a file."""

    command = audio._pcm_command("https://vz-test.b-cdn.net/a/play_240p.mp4", 16000)

    assert command[-1] == "pipe:1"
    assert not any(str(part).endswith(".wav") for part in command)


def test_the_picture_is_never_decoded():
    """-vn, because only the soundtrack is wanted."""

    assert "-vn" in audio._pcm_command("x.mp4", 16000)
