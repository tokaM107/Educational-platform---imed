"""Signed Bunny media URLs, and what happens when the CDN still says no.

Video 28 was submitted to RunPod as

    https://vz-….b-cdn.net/<guid>/play_240p.mp4

and Bunny answered 403. The pull zone has token authentication switched on, so
it refuses every unsigned request before it looks at what was asked for — a
request for a guid that does not exist is refused identically, which is how the
cause was identified rather than guessed at.

These tests cover the signing, that the token is a token and not the key, that
nothing secret reaches a log, and that the SSRF allowlist is unchanged by any
of it.
"""

import hashlib
import re
from base64 import b64encode

import pytest

from app.config import get_settings
from rag import bunny, media_url


HOST = "vz-37045c9a-1e8.b-cdn.net"

GUID = "78cf0406-87dc-4186-be87-2af6e5699cca"

URL = f"https://{HOST}/{GUID}/play_240p.mp4"

KEY = "a-token-authentication-key-from-the-bunny-dashboard"


@pytest.fixture(autouse=True)
def bunny_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "bunny_cdn_hostname", HOST)
    monkeypatch.setattr(settings, "bunny_token_auth_key", KEY)
    monkeypatch.setattr(settings, "bunny_token_auth_algorithm", "sha256")
    monkeypatch.setattr(settings, "bunny_media_url_ttl_seconds", 7200)
    return settings


def query(url):
    from urllib.parse import parse_qs, urlparse

    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# -------------------------
# The token itself
# -------------------------


def test_a_signed_url_carries_a_token_and_an_expiry():
    signed = bunny.sign_url(URL)

    assert set(query(signed)) == {"token", "expires"}


def test_the_token_matches_bunnys_documented_construction():
    """Base64(SHA256(key + path + expires)), made URL-safe. Not invented here."""

    signed = bunny.sign_url(URL, ttl_seconds=3600, now=1_000_000)

    path = f"/{GUID}/play_240p.mp4"
    expires = 1_000_000 + 3600

    expected = (
        b64encode(hashlib.sha256(f"{KEY}{path}{expires}".encode()).digest())
        .decode().replace("+", "-").replace("/", "_").replace("=", "")
    )

    assert query(signed) == {"token": expected, "expires": str(expires)}


def test_the_legacy_md5_form_is_available_for_zones_that_use_it(monkeypatch):
    monkeypatch.setattr(get_settings(), "bunny_token_auth_algorithm", "md5")

    signed = bunny.sign_url(URL, ttl_seconds=3600, now=1_000_000)

    path = f"/{GUID}/play_240p.mp4"
    expected = (
        b64encode(hashlib.md5(f"{KEY}{path}1003600".encode()).digest())
        .decode().replace("+", "-").replace("/", "_").replace("=", "")
    )

    assert query(signed)["token"] == expected


def test_an_unknown_algorithm_is_refused_rather_than_guessed(monkeypatch):
    monkeypatch.setattr(get_settings(), "bunny_token_auth_algorithm", "sha1")

    with pytest.raises(bunny.BunnyError):
        bunny.sign_url(URL)


def test_the_url_is_left_alone_when_no_key_is_configured(monkeypatch):
    """A zone without token authentication is a working deployment."""

    monkeypatch.setattr(get_settings(), "bunny_token_auth_key", "")

    assert bunny.sign_url(URL) == URL


def test_the_token_is_specific_to_one_video():
    """A token lifted from one lecture must not open another."""

    other = f"https://{HOST}/00000000-0000-0000-0000-000000000000/play_240p.mp4"

    assert query(bunny.sign_url(URL))["token"] != query(bunny.sign_url(other))["token"]


def test_the_security_key_never_appears_in_the_url():
    signed = bunny.sign_url(URL)

    assert KEY not in signed


# -------------------------
# Lifetime
# -------------------------


def test_the_expiry_is_in_the_future_and_bounded():
    signed = bunny.sign_url(URL, now=1_000_000)

    expires = int(query(signed)["expires"])

    assert expires == 1_000_000 + 7200


def test_the_default_lifetime_outlives_a_whole_runpod_job():
    """It must survive queue wait, cold start, and an ffmpeg reconnect.

    ffmpeg runs with -reconnect, so a dropped CDN connection is re-opened with
    the same URL part-way through a lecture. A token that expired by then would
    turn a recoverable blip into a failed transcription.
    """

    settings = get_settings()

    assert settings.bunny_media_url_ttl_seconds > settings.runpod_job_timeout_seconds


# ------------------------------------
# What the rest of the pipeline sees
# ------------------------------------


def video(guid=GUID):
    return {
        "guid": guid, "status": 4, "length": 3600,
        "availableResolutions": "240p,720p", "hasMP4Fallback": True,
    }


def test_the_url_the_worker_submits_is_signed():
    signed = bunny.audio_source_url(video())

    assert "token=" in signed and "expires=" in signed


def test_the_signed_url_uses_the_configured_cdn_host():
    from urllib.parse import urlparse

    assert urlparse(bunny.audio_source_url(video())).hostname == HOST


def test_the_playback_url_is_signed_too():
    """The browser is refused by the same zone for the same reason."""

    assert "token=" in bunny.rendition_url(video(), prefer="highest")


def test_the_hls_playlist_is_signed():
    assert "token=" in bunny.playlist_url(video())


def test_a_signed_url_still_passes_the_ssrf_allowlist():
    """Adding a query string must not change which host is checked."""

    hosts = media_url.bunny_hosts(HOST)

    assert media_url.check(bunny.audio_source_url(video()), hosts)


def test_signing_does_not_admit_another_host():
    """The token says nothing about where the URL points."""

    hosts = media_url.bunny_hosts(HOST)

    forged = bunny.sign_url("https://evil.test/x.mp4", key=KEY, ttl_seconds=60)

    with pytest.raises(media_url.UntrustedMediaURL):
        media_url.check(forged, hosts)


@pytest.mark.parametrize("target", [
    "https://169.254.169.254/latest/meta-data/",
    "https://127.0.0.1/x.mp4",
    "https://localhost/x.mp4",
    "https://10.0.0.5/x.mp4",
    "file:///etc/passwd",
])
def test_signing_did_not_weaken_the_private_address_refusals(target):
    hosts = media_url.bunny_hosts(HOST)

    with pytest.raises(media_url.UntrustedMediaURL):
        media_url.check(bunny.sign_url(target, key=KEY, ttl_seconds=60), hosts)


# -------------------------
# Secrets and logs
# -------------------------


def test_redact_removes_the_token_from_a_url():
    signed = bunny.sign_url(URL)

    assert "token=" not in bunny.redact(signed)
    assert GUID in bunny.redact(signed)


def test_nothing_secret_reaches_the_log_when_a_job_is_submitted(
    monkeypatch, caplog
):
    """The worker logs a submission; neither the key nor a token may be in it."""

    from app.services import transcription_jobs
    from rag import worker
    from tests.fake_db import FakeConn

    signed = bunny.sign_url(URL)

    monkeypatch.setattr(worker.bunny, "get_video", lambda guid: video(guid))
    monkeypatch.setattr(worker.bunny, "audio_source_url", lambda v: signed)
    monkeypatch.setattr(
        worker.transcribe_runpod, "submit",
        lambda url, video_id=None, chunk_seconds=None: "rp-1",
    )
    monkeypatch.setattr(get_settings(), "asr_backend", "runpod")

    claims = iter([{
        "id": 1, "bunny_guid": GUID, "video_id": 17,
        "attempt_count": 1, "max_attempts": 3, "runpod_job_id": None,
    }])
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: next(claims, None))
    for name in ("mark_submitted", "mark_failed"):
        monkeypatch.setattr(transcription_jobs, name,
                            lambda *a, **k: None)

    with caplog.at_level("DEBUG"):
        worker.submit_next(FakeConn())

    logged = "\n".join(record.getMessage() for record in caplog.records)

    assert KEY not in logged
    assert query(signed)["token"] not in logged


def test_the_api_key_is_never_used_as_the_signing_key(monkeypatch):
    """They are different credentials; the API key is account-wide."""

    settings = get_settings()
    monkeypatch.setattr(settings, "bunny_api_key", "the-account-api-key")

    signed = bunny.sign_url(URL)

    assert "the-account-api-key" not in signed


# ----------------------------------------
# A refusal must not burn every attempt
# ----------------------------------------


@pytest.mark.parametrize("message", [
    "AudioError: ffmpeg failed: HTTP error 403 Forbidden",
    "Server returned 403 Forbidden (access denied)",
    "HTTP error 401 Unauthorized",
    "HTTP error 404 Not Found",
])
def test_a_media_refusal_is_recognised_as_permanent(message):
    from rag import worker

    assert worker.is_media_refusal(message)


@pytest.mark.parametrize("message", [
    "CUDA out of memory",
    "RunPod TIMED_OUT",
    "connection reset by peer",
    "the transcript mentions 403 somewhere in the lecture text",
    "",
    None,
])
def test_an_ordinary_failure_is_still_retried(message):
    from rag import worker

    assert not worker.is_media_refusal(message)


def settle(monkeypatch, error, state="FAILED"):
    """Run one settle pass over a job RunPod says failed. Returns the marks."""

    from app.services import transcription_jobs
    from rag import worker
    from tests.fake_db import FakeConn

    monkeypatch.setattr(
        worker.transcribe_runpod, "status",
        lambda job_id: {"state": state, "error": error},
    )

    recorded = []
    monkeypatch.setattr(
        transcription_jobs, "mark_failed",
        lambda conn, job_id, err, permanent=False: recorded.append(permanent),
    )

    worker.settle_one(FakeConn(), {
        "id": 1, "bunny_guid": GUID, "video_id": 17, "attempt_count": 1,
        "max_attempts": 3, "runpod_job_id": "rp-1",
        "submitted_seconds_ago": 10,
    })

    return recorded


def test_a_gpu_that_could_not_read_the_media_does_not_get_two_more_tries(
    monkeypatch,
):
    """Three cold starts to be told 403 three times is the thing to avoid."""

    assert settle(monkeypatch, "AudioError: HTTP error 403 Forbidden") == [True]


def test_an_ordinary_gpu_failure_keeps_its_remaining_attempts(monkeypatch):
    assert settle(monkeypatch, "CUDA out of memory") == [False]


# ---- against a real PostgreSQL ----


def insert(conn, job_id, status="failed", attempt_count=1):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO transcription_jobs
                (id, bunny_guid, video_id, status, attempt_count, max_attempts)
            VALUES (%s, %s, %s, %s, %s, 3)
            """,
            (job_id, f"guid-{job_id}", job_id, status, attempt_count),
        )
    conn.commit()


def test_a_permanent_failure_stops_the_job_being_claimed_again(jobs_db):
    from app.services import transcription_jobs

    insert(jobs_db, 1, status="pending", attempt_count=1)

    transcription_jobs.mark_failed(
        jobs_db, 1, "Bunny refused the media: HTTP error 403", permanent=True
    )

    assert transcription_jobs.claim_for_submission(jobs_db) is None


def test_an_ordinary_failure_is_still_claimed_again(jobs_db):
    """The change must not make every failure terminal."""

    from app.services import transcription_jobs

    insert(jobs_db, 1, status="pending", attempt_count=1)

    transcription_jobs.mark_failed(jobs_db, 1, "CUDA out of memory")

    claimed = transcription_jobs.claim_for_submission(jobs_db)

    assert claimed is not None and claimed["id"] == 1


def test_a_permanent_failure_keeps_the_error_and_the_identifiers(jobs_db):
    from app.services import transcription_jobs

    insert(jobs_db, 1, status="pending")

    transcription_jobs.mark_failed(jobs_db, 1, "HTTP error 403", permanent=True)

    with jobs_db.cursor() as cur:
        cur.execute(
            "SELECT status, attempt_count, max_attempts, bunny_guid, last_error "
            "FROM transcription_jobs WHERE id = 1"
        )
        status, attempts, max_attempts, guid, error = cur.fetchone()

    assert status == "failed"
    assert attempts == max_attempts
    assert guid == "guid-1"
    assert "403" in error
