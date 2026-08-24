"""The transcription loop: order, timestamps, and what is left behind.

The ASR model is stubbed. It is several gigabytes, it needs a GPU to be quick,
and none of what is being checked here depends on what it returns — only on
when it is called, with what, and what exists on disk around it.

Bunny is stubbed too, so none of this needs credentials or a network.
"""

import subprocess
import wave
from pathlib import Path

import pytest

from rag import audio, bunny, transcribe_cohere


CHUNK = 300


@pytest.fixture(scope="module")
def twelve_minutes(tmp_path_factory):

    path = tmp_path_factory.mktemp("src") / "lecture.m4a"

    subprocess.run(
        ["ffmpeg", "-f", "lavfi", "-i", "sine=frequency=440:duration=720",
         "-c:a", "aac", "-b:a", "48k", "-ar", "16000", "-y", str(path)],
        capture_output=True,
        check=True,
    )

    return path


@pytest.fixture
def model(monkeypatch):
    """A stand-in ASR that records what it was given, in order."""

    calls = []

    def transcribe_chunk(chunk_path):
        chunk_path = Path(chunk_path)

        with wave.open(str(chunk_path), "rb") as handle:
            duration = handle.getnframes() / handle.getframerate()

        calls.append(
            {
                "name": chunk_path.name,
                "existed": chunk_path.exists(),
                # What else was on disk while this chunk was being transcribed.
                "siblings": len(list(chunk_path.parent.glob("chunk_*.wav"))),
            }
        )

        return f"نص {chunk_path.stem}", duration

    monkeypatch.setattr(transcribe_cohere, "transcribe_chunk", transcribe_chunk)

    return calls


# -------------------------
# Order and timestamps
# -------------------------


def test_chunks_are_transcribed_in_order(twelve_minutes, model, tmp_path):

    transcribe_cohere.transcribe_chunks(
        audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK),
        output_path=tmp_path / "t.txt",
        verbose=False,
    )

    assert [call["name"] for call in model] == [
        "chunk_000.wav",
        "chunk_001.wav",
        "chunk_002.wav",
    ]


def test_the_transcript_carries_the_global_ranges(twelve_minutes, model, tmp_path):
    """The header is the only record of where a passage sits in the lecture."""

    output = tmp_path / "t.txt"

    transcribe_cohere.transcribe_chunks(
        audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK),
        output_path=output,
        verbose=False,
    )

    headers = [
        line for line in output.read_text(encoding="utf-8").splitlines()
        if line.startswith("=====")
    ]

    assert headers == [
        "===== chunk_000.wav | 00:00:00 --> 00:05:00 =====",
        "===== chunk_001.wav | 00:05:00 --> 00:10:00 =====",
        "===== chunk_002.wav | 00:10:00 --> 00:12:00 =====",
    ]


def test_the_transcript_still_parses_as_chunking_expects(
    twelve_minutes, model, tmp_path
):
    """The schema is unchanged — rag/chunking.py must read it as before."""

    from rag.chunking import read_transcript

    output = tmp_path / "t.txt"

    transcribe_cohere.transcribe_chunks(
        audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK),
        output_path=output,
        verbose=False,
    )

    blocks = read_transcript(output)

    assert [(b.start_ts, b.end_ts) for b in blocks] == [(0, 300), (300, 600), (600, 720)]


# -------------------------
# One chunk at a time
# -------------------------


def test_only_one_chunk_is_on_disk_during_each_transcription(
    twelve_minutes, model, tmp_path
):
    """The chunk being transcribed, and nothing else."""

    transcribe_cohere.transcribe_chunks(
        audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK),
        output_path=tmp_path / "t.txt",
        verbose=False,
    )

    assert all(call["existed"] for call in model)
    assert [call["siblings"] for call in model] == [1, 1, 1]


def test_every_chunk_is_gone_when_the_run_finishes(twelve_minutes, model, tmp_path):

    directories = set()

    def watching():
        for chunk in audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK):
            directories.add(chunk.path.parent)
            yield chunk

    transcribe_cohere.transcribe_chunks(
        watching(), output_path=tmp_path / "t.txt", verbose=False
    )

    assert directories
    assert all(not directory.exists() for directory in directories)


# -------------------------
# Failure
# -------------------------


def test_audio_is_cleaned_up_when_the_model_fails(
    twelve_minutes, monkeypatch, tmp_path
):
    """A model error mid-lecture must not strand a chunk on disk."""

    directories = set()

    def explode(chunk_path):
        directories.add(Path(chunk_path).parent)
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(transcribe_cohere, "transcribe_chunk", explode)

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        transcribe_cohere.transcribe_chunks(
            audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK),
            output_path=tmp_path / "t.txt",
            verbose=False,
        )

    assert directories
    assert all(not directory.exists() for directory in directories)


def test_a_failure_partway_keeps_what_was_already_transcribed(
    twelve_minutes, monkeypatch, tmp_path
):
    """Losing chunk 3 should not lose chunks 1 and 2 as well."""

    seen = []

    def fail_on_third(chunk_path):
        seen.append(Path(chunk_path).name)

        if len(seen) == 3:
            raise RuntimeError("model died")

        return "نص", 300.0

    monkeypatch.setattr(transcribe_cohere, "transcribe_chunk", fail_on_third)

    output = tmp_path / "t.txt"

    with pytest.raises(RuntimeError):
        transcribe_cohere.transcribe_chunks(
            audio.iter_audio_chunks(twelve_minutes, chunk_seconds=CHUNK),
            output_path=output,
            verbose=False,
        )

    # "=====" opens and closes each header, so count the headers themselves.
    assert output.read_text(encoding="utf-8").count("===== chunk_") == 2


def test_a_source_with_no_audio_is_reported(tmp_path, model):

    silent = tmp_path / "empty.wav"

    with wave.open(str(silent), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)

    with pytest.raises(RuntimeError, match="no audio chunks"):
        transcribe_cohere.transcribe_chunks(
            audio.iter_audio_chunks(silent, chunk_seconds=CHUNK),
            output_path=tmp_path / "t.txt",
            verbose=False,
        )


# -------------------------
# The Bunny URL reaches ffmpeg
# -------------------------


def test_the_bunny_url_is_what_gets_streamed(monkeypatch):
    """End to end in intent: metadata -> URL -> ffmpeg input, no download."""

    monkeypatch.setattr(
        bunny, "_config", lambda: ("1234", "key", "vz-test.b-cdn.net")
    )

    video = {
        "guid": "abc-123",
        "status": 4,
        "hasMP4Fallback": True,
        "availableResolutions": "240p,720p",
    }

    assert bunny.is_finished(video)

    url = bunny.audio_source_url(video)
    assert url == "https://vz-test.b-cdn.net/abc-123/play_240p.mp4"

    command = audio._pcm_command(url, 16000)
    assert command[command.index("-i") + 1] == url


def test_an_hls_source_is_streamed_the_same_way(monkeypatch):
    """No MP4 fallback on the library — still read in place, still a pipe."""

    monkeypatch.setattr(
        bunny, "_config", lambda: ("1234", "key", "vz-test.b-cdn.net")
    )

    url = bunny.audio_source_url(
        {"guid": "abc-123", "status": 4, "hasMP4Fallback": False}
    )

    assert url.endswith("playlist.m3u8")

    command = audio._pcm_command(url, 16000)
    assert command[command.index("-i") + 1] == url
    assert command[-1] == "pipe:1"


def test_an_unfinished_video_has_no_audio_source(monkeypatch):
    """Status must be checked before anything expensive is attempted."""

    monkeypatch.setattr(
        bunny, "_config", lambda: ("1234", "key", "vz-test.b-cdn.net")
    )

    processing = {"guid": "abc-123", "status": 3, "availableResolutions": None}

    assert not bunny.is_finished(processing)
    assert bunny.status_name(processing) == "Transcoding"
