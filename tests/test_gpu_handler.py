"""The RunPod Serverless handler: what it fetches, and what it leaves behind.

No GPU and no network. The two properties worth protecting here are the ones
that cost money or leak: temporary media must never survive a job, whatever
went wrong, and the worker must refuse to fetch a URL that is not Bunny's.
"""

import pytest

from gpu import handler


@pytest.fixture(autouse=True)
def work_root(tmp_path, monkeypatch):
    """Keep every job's scratch directory inside the test's tmp_path."""

    monkeypatch.setattr(handler, "WORK_ROOT", tmp_path / "transcription_jobs")
    monkeypatch.setattr(handler, "ALLOWED_MEDIA_HOSTS", {"cdn.example"})
    return tmp_path / "transcription_jobs"


def job(url="https://cdn.example/guid/play_240p.mp4", **extra):
    return {"id": "job-1", "input": {"bunny_url": url, **extra}}


def fake_chunks(monkeypatch, count=2, on_chunk=None):
    """Replace ffmpeg and the ASR with something that writes real files."""

    class FakeChunk:
        def __init__(self, index, directory):
            self.index = index
            self.path = directory / f"chunk_{index:03d}.wav"
            self.path.write_bytes(b"RIFF")
            self.start_seconds = index * 300.0
            self.duration_seconds = 300.0

        @property
        def end_seconds(self):
            return self.start_seconds + self.duration_seconds

    def iter_audio_chunks(url, chunk_seconds, workspace):
        from pathlib import Path

        for index in range(count):
            if on_chunk:
                on_chunk(index)
            yield FakeChunk(index, Path(workspace))

    monkeypatch.setattr(handler, "iter_audio_chunks", iter_audio_chunks)
    monkeypatch.setitem(
        __import__("sys").modules, "rag.transcribe_cohere",
        type("M", (), {"transcribe_chunk": staticmethod(
            lambda path: ("مرحبا بكم", 300.0))})(),
    )


# -------------------------
# The happy path
# -------------------------


def test_a_lecture_comes_back_as_timestamped_blocks(monkeypatch):
    fake_chunks(monkeypatch)

    result = handler.handler(job())

    assert "error" not in result
    assert [block["start_ts"] for block in result["blocks"]] == [0, 300]
    assert [block["end_ts"] for block in result["blocks"]] == [300, 600]
    assert result["blocks"][0]["text"] == "مرحبا بكم"


def test_the_run_reports_metrics_for_comparing_gpus(monkeypatch):
    """audio / gpu seconds is the RTFx this project benchmarks on."""

    fake_chunks(monkeypatch)

    result = handler.handler(job())

    assert result["audio_duration_seconds"] == 600.0
    assert result["gpu_processing_seconds"] >= 0
    assert "rtfx" in result


# -------------------------
# Temporary files
# -------------------------


def test_temporary_media_is_deleted_after_success(monkeypatch, work_root):
    fake_chunks(monkeypatch)

    handler.handler(job())

    assert not (work_root / "job-1").exists()


def test_temporary_media_is_deleted_after_a_transcription_failure(
    monkeypatch, work_root
):
    """The lecture is still in Bunny, so a failed job has nothing to preserve."""

    def explode(index):
        if index == 1:
            raise RuntimeError("CUDA out of memory")

    fake_chunks(monkeypatch, count=3, on_chunk=explode)

    result = handler.handler(job())

    assert "CUDA out of memory" in result["error"]
    assert not (work_root / "job-1").exists()


def test_temporary_media_is_deleted_after_an_ffmpeg_failure(
    monkeypatch, work_root
):
    def broken(url, chunk_seconds, workspace):
        from pathlib import Path

        Path(workspace).mkdir(parents=True, exist_ok=True)
        (Path(workspace) / "partial.wav").write_bytes(b"RIFF")
        raise handler.AudioError("ffmpeg is not installed or not on PATH")
        yield  # pragma: no cover - generator marker

    monkeypatch.setattr(handler, "iter_audio_chunks", broken)

    result = handler.handler(job())

    assert "ffmpeg" in result["error"]
    assert not (work_root / "job-1").exists()


def test_a_source_with_no_audio_fails_rather_than_returning_nothing(monkeypatch):
    """An empty transcript would mark the video done and unsearchable."""

    fake_chunks(monkeypatch, count=0)

    assert "no audio" in handler.handler(job())["error"]


# -------------------------
# What the worker refuses to fetch
# -------------------------


def test_a_url_outside_the_bunny_hosts_is_refused(monkeypatch, work_root):
    """SSRF: this container holds a GPU, an HF token and a RunPod identity."""

    fake_chunks(monkeypatch)

    result = handler.handler(job(url="https://evil.test/payload.mp4"))

    assert "not one of the allowed Bunny hosts" in result["error"]
    assert not (work_root / "job-1").exists()


def test_the_cloud_metadata_address_is_refused(monkeypatch):
    fake_chunks(monkeypatch)

    result = handler.handler(job(url="http://169.254.169.254/latest/meta-data/"))

    assert "error" in result


def test_a_host_smuggled_through_userinfo_is_refused(monkeypatch):
    """`https://cdn.example@evil.test/` has hostname evil.test, not cdn.example."""

    fake_chunks(monkeypatch)

    result = handler.handler(job(url="https://cdn.example@evil.test/x.mp4"))

    assert "not one of the allowed Bunny hosts" in result["error"]


def test_a_job_with_no_url_is_refused(monkeypatch):
    fake_chunks(monkeypatch)

    assert "error" in handler.handler({"id": "job-1", "input": {}})


def test_a_bad_chunk_seconds_is_refused(monkeypatch):
    fake_chunks(monkeypatch)

    assert "error" in handler.handler(job(chunk_seconds=0))


def test_the_error_is_returned_not_raised(monkeypatch):
    """RunPod should record a readable FAILED, not an opaque traceback."""

    fake_chunks(monkeypatch, count=1, on_chunk=lambda i: 1 / 0)

    result = handler.handler(job())

    assert result["error"].startswith("ZeroDivisionError")


def test_a_missing_cached_model_stops_the_worker(monkeypatch, tmp_path):
    """Requirement of the Cached Model setup: say what is wrong, then stop.

    A worker whose endpoint has no Cached Model attached can never transcribe
    anything. Coming up anyway would mean accepting lectures and failing every
    one of them on a billing GPU, so this is the one load failure that is fatal.
    """

    from rag import transcribe_cohere

    monkeypatch.setenv("RUNPOD_MODEL_CACHE", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.delenv("COHERE_TRANSCRIBE_MODEL_PATH", raising=False)

    with pytest.raises(transcribe_cohere.ModelNotCached) as caught:
        handler.preload_model()

    assert "Cached Model" in str(caught.value)


def test_a_transient_load_failure_lets_the_worker_start(monkeypatch, tmp_path):
    """An OOM may pass on the next start; crash-looping only bills for it."""

    from rag import transcribe_cohere

    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("COHERE_TRANSCRIBE_MODEL_PATH", str(snapshot))

    def explode():
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(transcribe_cohere, "load_model", explode)

    assert handler.preload_model() is False
