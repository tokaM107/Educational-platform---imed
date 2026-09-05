"""The RunPod Serverless client: submitting, reading status, and giving up.

The distinction these exist to protect is between "RunPod says this failed" and
"we could not ask RunPod". The first is a retry; the second is a wait. Getting
them the wrong way round either double-bills a running lecture or abandons one
that was about to succeed.
"""

import pytest
import requests

from app.config import get_settings
from rag import transcribe_runpod
from rag.transcribe_runpod import RunPodError, RunPodUnavailable


@pytest.fixture(autouse=True)
def endpoint(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "runpod_api_key", "rp-key")
    monkeypatch.setattr(settings, "runpod_endpoint_id", "endpoint-1")
    monkeypatch.setattr(settings, "runpod_api_base", "https://api.runpod.ai/v2")
    monkeypatch.setattr(settings, "bunny_cdn_hostname", "cdn.example")


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def respond_with(monkeypatch, response, record=None):
    def request(method, url, **kwargs):
        if record is not None:
            record.append((method, url, kwargs))
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(transcribe_runpod.requests, "request", request)


BUNNY_URL = "https://cdn.example/guid-1/play_240p.mp4"


# -------------------------
# Submitting
# -------------------------


def test_submitting_returns_the_runpod_job_id(monkeypatch):
    calls = []
    respond_with(monkeypatch,
                 FakeResponse(body={"id": "rp-1", "status": "IN_QUEUE"}), calls)

    assert transcribe_runpod.submit(BUNNY_URL, video_id=11) == "rp-1"

    method, url, kwargs = calls[0]
    assert (method, url) == ("POST", "https://api.runpod.ai/v2/endpoint-1/run")
    assert kwargs["json"]["input"]["bunny_url"] == BUNNY_URL
    assert kwargs["json"]["input"]["video_id"] == 11


def test_the_api_key_travels_as_a_bearer_token(monkeypatch):
    calls = []
    respond_with(monkeypatch, FakeResponse(body={"id": "rp-1"}), calls)

    transcribe_runpod.submit(BUNNY_URL)

    assert calls[0][2]["headers"]["Authorization"] == "Bearer rp-key"


def test_a_non_bunny_url_is_never_submitted(monkeypatch):
    """Checked here so a bad catalog row cannot start a GPU at all."""

    respond_with(monkeypatch, FakeResponse(body={"id": "rp-1"}))

    with pytest.raises(Exception, match="allowed Bunny hosts"):
        transcribe_runpod.submit("https://evil.test/x.mp4")


def test_a_submit_with_no_job_id_is_an_error(monkeypatch):
    respond_with(monkeypatch, FakeResponse(body={"status": "IN_QUEUE"}))

    with pytest.raises(RunPodError, match="no job id"):
        transcribe_runpod.submit(BUNNY_URL)


# -------------------------
# Unreachable vs failed
# -------------------------


def test_a_connection_error_is_unavailable_not_failure(monkeypatch):
    respond_with(monkeypatch, requests.ConnectionError("refused"))

    with pytest.raises(RunPodUnavailable):
        transcribe_runpod.status("rp-1")


def test_a_read_timeout_is_unavailable_not_failure(monkeypatch):
    """The GPU may be transcribing right now — this says nothing about it."""

    respond_with(monkeypatch, requests.ReadTimeout("timed out"))

    with pytest.raises(RunPodUnavailable):
        transcribe_runpod.status("rp-1")


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_runpods_own_trouble_is_unavailable_not_failure(monkeypatch, code):
    respond_with(monkeypatch, FakeResponse(status_code=code, text="busy"))

    with pytest.raises(RunPodUnavailable):
        transcribe_runpod.status("rp-1")


def test_a_client_error_is_a_real_failure(monkeypatch):
    """401/404 will not fix themselves; retrying the poll is pointless."""

    respond_with(monkeypatch, FakeResponse(status_code=401, text="bad key"))

    with pytest.raises(RunPodError) as raised:
        transcribe_runpod.status("rp-1")

    assert not isinstance(raised.value, RunPodUnavailable)


# -------------------------
# Reading status
# -------------------------


def test_a_completed_job_yields_blocks_and_metrics(monkeypatch):
    respond_with(monkeypatch, FakeResponse(body={
        "status": "COMPLETED",
        "executionTime": 2550,
        "output": {
            "blocks": [{"index": 0, "start_ts": 0, "end_ts": 300, "text": "نص"}],
            "audio_duration_seconds": 300.0,
            "gpu_processing_seconds": 2.55,
            "rtfx": 117.6,
        },
    }))

    result = transcribe_runpod.status("rp-1")

    assert result["state"] == "COMPLETED"
    assert result["blocks"][0]["text"] == "نص"
    assert result["metrics"]["rtfx"] == 117.6


def test_a_failed_job_carries_its_error(monkeypatch):
    respond_with(monkeypatch, FakeResponse(body={
        "status": "FAILED", "error": "AudioError: ffmpeg not found"}))

    result = transcribe_runpod.status("rp-1")

    assert result["state"] == "FAILED"
    assert "ffmpeg" in result["error"]


@pytest.mark.parametrize("state", ["IN_QUEUE", "IN_PROGRESS"])
def test_a_pending_job_reports_its_state_without_blocks(monkeypatch, state):
    respond_with(monkeypatch, FakeResponse(body={"status": state}))

    result = transcribe_runpod.status("rp-1")

    assert result["state"] == state
    assert "blocks" not in result


def test_cancelling_a_finished_job_does_not_raise(monkeypatch):
    """Best effort: failing to cancel must not fail whatever asked."""

    respond_with(monkeypatch, FakeResponse(status_code=404, text="gone"))

    assert transcribe_runpod.cancel("rp-1") is False


# -------------------------
# Block validation
# -------------------------


def test_blocks_come_back_in_video_order():
    out = transcribe_runpod.to_block_tuples([
        {"index": 1, "start_ts": 300, "end_ts": 600, "text": "ب"},
        {"index": 0, "start_ts": 0, "end_ts": 300, "text": "أ"},
    ])

    assert [index for index, _, _, _ in out] == [0, 1]


def test_a_block_missing_its_timestamps_is_refused():
    """Defaulting to zero would silently point citations at the video's start."""

    with pytest.raises(RunPodError, match="malformed block"):
        transcribe_runpod.to_block_tuples([{"index": 0, "text": "أ"}])


def test_a_zero_length_block_is_widened():
    """chunk_block divides by the block's duration."""

    (_, start_ts, end_ts, _), = transcribe_runpod.to_block_tuples(
        [{"index": 0, "start_ts": 10, "end_ts": 10, "text": "أ"}]
    )

    assert end_ts > start_ts


# -------------------------
# Configuration
# -------------------------


def test_missing_configuration_names_what_is_absent(monkeypatch):
    monkeypatch.setattr(get_settings(), "runpod_endpoint_id", "")

    with pytest.raises(RuntimeError, match="RUNPOD_ENDPOINT_ID"):
        get_settings().require_runpod()
