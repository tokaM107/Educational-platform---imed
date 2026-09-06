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
    monkeypatch.setattr(settings, "bunny_token_auth_algorithm", "advanced")
    monkeypatch.setattr(settings, "bunny_media_url_ttl_seconds", 1800)
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


def test_an_unknown_mode_is_refused_rather_than_guessed(monkeypatch):
    monkeypatch.setattr(get_settings(), "bunny_token_auth_algorithm", "sha1")

    with pytest.raises(bunny.BunnyError):
        bunny.sign_url(URL)


def test_plain_sha256_is_refused_because_bunny_has_no_such_format(monkeypatch):
    """The bug this file exists for: it was the default, and it never worked.

    Bunny signs with MD5 (basic) or HMAC-SHA256 (advanced). A bare
    SHA256(key + path + expires) is neither, so a zone refuses it exactly as it
    refuses an unsigned URL — which is why every mode appeared to fail at once.
    """

    monkeypatch.setattr(get_settings(), "bunny_token_auth_algorithm", "sha256")

    with pytest.raises(bunny.BunnyError) as caught:
        bunny.sign_url(URL)

    assert "not a Bunny token format" in str(caught.value)


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

    assert expires == 1_000_000 + 1800


def test_the_shipped_default_lifetime_is_thirty_minutes(monkeypatch):
    """Short, because the URL is a bearer credential for one lecture."""

    from app.config import Settings

    monkeypatch.delenv("BUNNY_MEDIA_URL_TTL_SECONDS", raising=False)

    assert Settings().bunny_media_url_ttl_seconds == 1800


def test_the_lifetime_covers_a_cold_start_and_a_late_reconnect():
    """It has to still be valid at the *end* of the job, not the start.

    ffmpeg runs with -reconnect, so a dropped CDN connection is re-opened with
    the same URL part-way through a lecture; a token that expired by then would
    turn a recoverable blip into a failed transcription. The budget it has to
    cover is queue wait plus cold start plus download plus transcription.
    """

    settings = get_settings()

    generous_cold_start = 10 * 60
    generous_transcription = 10 * 60

    assert settings.bunny_media_url_ttl_seconds >= (
        generous_cold_start + generous_transcription
    )


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


# -------------------------
# The advanced (HMAC) form
# -------------------------


def test_the_advanced_form_matches_bunnys_construction(monkeypatch):
    """HS256- + Base64URL(HMAC-SHA256(key, path + expires))."""

    import hmac as hmac_module

    monkeypatch.setattr(get_settings(), "bunny_token_auth_algorithm", "advanced")

    signed = bunny.sign_url(URL, ttl_seconds=1800, now=1_000_000)

    path = f"/{GUID}/play_240p.mp4"
    expires = 1_000_000 + 1800

    digest = hmac_module.new(
        KEY.encode(), f"{path}{expires}".encode(), hashlib.sha256
    ).digest()

    expected = "HS256-" + (
        b64encode(digest).decode()
        .replace("+", "-").replace("/", "_").replace("=", "")
    )

    assert query(signed)["token"] == expected


def test_the_advanced_form_is_marked_so_bunny_recognises_it(monkeypatch):
    monkeypatch.setattr(get_settings(), "bunny_token_auth_algorithm", "advanced")

    assert query(bunny.sign_url(URL))["token"].startswith("HS256-")


@pytest.mark.parametrize("algorithm", ["advanced", "basic"])
def test_every_supported_form_signs_the_path_and_not_the_query(algorithm):
    """A token must not be widenable by appending parameters."""

    a = bunny.sign_url(URL, algorithm=algorithm, ttl_seconds=60, now=0)
    b = bunny.sign_url(URL, algorithm=algorithm, ttl_seconds=60, now=0)

    assert query(a)["token"] == query(b)["token"]


@pytest.mark.parametrize("algorithm", ["advanced", "basic"])
def test_no_form_leaks_the_key(algorithm):
    assert KEY not in bunny.sign_url(URL, algorithm=algorithm)


# -------------------------
# Expiry
# -------------------------


def test_an_expired_url_carries_a_past_expiry():
    """What Bunny compares against. An expired token is refused, by design."""

    import time

    signed = bunny.sign_url(URL, ttl_seconds=1, now=time.time() - 3600)

    assert int(query(signed)["expires"]) < time.time()


def test_a_fresh_url_carries_a_future_expiry():
    import time

    assert int(query(bunny.sign_url(URL))["expires"]) > time.time()


def test_an_expired_url_is_still_refused_by_the_ssrf_check_if_the_host_is_wrong():
    """Expiry and host are independent; neither substitutes for the other."""

    import time

    hosts = media_url.bunny_hosts(HOST)

    stale = bunny.sign_url(
        "https://evil.test/x.mp4", key=KEY, ttl_seconds=1, now=time.time() - 3600
    )

    with pytest.raises(media_url.UntrustedMediaURL):
        media_url.check(stale, hosts)


def test_a_bunny_403_from_an_expired_token_is_classified_permanent():
    """Retrying with the same expired URL would fail identically."""

    from rag import worker

    assert worker.is_media_refusal("ffmpeg: HTTP error 403 Forbidden")


# ----------------------------------------
# Minted late, and never stored
# ----------------------------------------


def test_the_worker_checks_an_unsigned_url_and_submits_a_signed_one(monkeypatch):
    """The TTL must not be spent on our own lookups, or on the queue."""

    from app.services import transcription_jobs
    from rag import worker
    from tests.fake_db import FakeConn

    submitted = []

    monkeypatch.setattr(worker.bunny, "get_video", lambda guid: video(guid))
    monkeypatch.setattr(
        worker.transcribe_runpod, "submit",
        lambda url, video_id=None, chunk_seconds=None: submitted.append(url) or "rp-1",
    )
    monkeypatch.setattr(get_settings(), "asr_backend", "runpod")
    monkeypatch.setattr(get_settings(), "bunny_media_hosts_extra", "")

    claims = iter([{
        "id": 1, "bunny_guid": GUID, "video_id": 17,
        "attempt_count": 1, "max_attempts": 3, "runpod_job_id": None,
    }])
    monkeypatch.setattr(transcription_jobs, "claim_for_submission",
                        lambda conn: next(claims, None))
    for name in ("mark_submitted", "mark_failed"):
        monkeypatch.setattr(transcription_jobs, name, lambda *a, **k: None)

    worker.submit_next(FakeConn())

    assert len(submitted) == 1
    assert "token=" in submitted[0] and "expires=" in submitted[0]


def test_no_signed_url_is_written_to_the_queue(monkeypatch):
    """Requirement of a short TTL: the row must not outlive the token."""

    from app.services import transcription_jobs
    from rag import worker
    from tests.fake_db import FakeConn

    conn = FakeConn()

    monkeypatch.setattr(worker.bunny, "get_video", lambda guid: video(guid))
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
    monkeypatch.setattr(transcription_jobs, "mark_submitted",
                        lambda *a, **k: None)

    worker.submit_next(conn)

    assert not any("token=" in str(params) for _, params in conn.calls)


def test_a_video_with_no_mp4_fallback_is_refused_before_a_gpu_starts(monkeypatch):
    """A query-string token signs the playlist, not the segments it points at."""

    from rag import worker

    hls = dict(video(), hasMP4Fallback=False)

    monkeypatch.setattr(worker.bunny, "get_video", lambda guid: hls)

    with pytest.raises(worker.PermanentJobError) as caught:
        worker.media_url_for(GUID)

    assert "MP4 fallback" in str(caught.value)


# ------------------------------------------------------------------
# Fixed vectors
# ------------------------------------------------------------------
#
# Literal expected tokens, not recomputed from the same formula the code uses.
# A test that re-derives the answer the same way passes even when both sides
# change together, which is exactly the failure that shipped: the module signed
# with a scheme Bunny has never accepted, and every test agreed with it.

VECTOR_KEY = "bunny-url-token-authentication-key"

VECTOR_PATH = f"/{GUID}/play_240p.mp4"

VECTOR_EXPIRES = 1800000000

VECTORS = {
    "advanced": "HS256-mCafhnEEIsyfBwI_0RvBm8-_QTHwM5NoWdwOmnpRemA",
    "basic": "BYSlLeb4H5FJD-eQ8hOpsg",
}


@pytest.mark.parametrize("mode", sorted(VECTORS))
def test_the_token_matches_a_fixed_vector(mode):
    signed = bunny.sign_url(
        f"https://{HOST}{VECTOR_PATH}",
        key=VECTOR_KEY,
        algorithm=mode,
        ttl_seconds=1,
        now=VECTOR_EXPIRES - 1,
    )

    assert query(signed)["token"] == VECTORS[mode]


def test_the_advanced_vector_is_an_hmac_and_the_basic_one_is_not():
    """They must not be the same construction under two names."""

    assert VECTORS["advanced"].startswith("HS256-")
    assert not VECTORS["basic"].startswith("HS256-")
    assert VECTORS["advanced"] != VECTORS["basic"]


# ------------------------------------------------------------------
# The exact URL Bunny is asked for
# ------------------------------------------------------------------


def test_the_signed_path_is_the_rendition_path_exactly():
    """/<video-guid>/play_240p.mp4 — what is signed and what is requested."""

    from urllib.parse import urlparse

    signed = bunny.audio_source_url(video())

    assert urlparse(signed).path == f"/{GUID}/play_240p.mp4"


def test_the_token_covers_that_path_and_no_other():
    """Signing the wrong path is indistinguishable from not signing at all."""

    signed = bunny.sign_url(URL, ttl_seconds=1, now=VECTOR_EXPIRES - 1,
                            key=VECTOR_KEY, algorithm="advanced")

    assert query(signed)["token"] == VECTORS["advanced"]

    # The same file one directory up must not produce the same token.
    other = bunny.sign_url(
        f"https://{HOST}/play_240p.mp4",
        ttl_seconds=1, now=VECTOR_EXPIRES - 1,
        key=VECTOR_KEY, algorithm="advanced",
    )

    assert query(other)["token"] != VECTORS["advanced"]


def test_the_query_string_is_exactly_token_and_expires():
    """Bunny names these two parameters; anything else is not part of the spec."""

    from urllib.parse import parse_qsl, urlparse

    pairs = parse_qsl(urlparse(bunny.sign_url(URL)).query)

    assert [name for name, _ in pairs] == ["token", "expires"]


def test_expires_is_whole_seconds_since_the_epoch():
    """Not milliseconds, and not a float — Bunny compares it as an integer."""

    value = query(bunny.sign_url(URL, now=1_000_000.75))["expires"]

    assert value.isdigit()
    assert int(value) == 1_000_000 + 1800


def test_no_token_path_or_ip_parameters_are_sent():
    """Neither is signed, so sending either would invalidate the token.

    No client IP in particular: RunPod's workers have no stable egress address,
    so a token bound to one would be refused for the machine that downloads it.
    """

    signed = bunny.sign_url(URL)

    assert "token_path" not in signed
    assert "token_ip" not in signed


# ------------------------------------------------------------------
# Which environment variable holds which key
# ------------------------------------------------------------------
#
# Bunny hands out several similarly named credentials and refuses the wrong one
# with the same 403 as no credential at all. These pin which variable this code
# signs with, because the symptom cannot tell you.

PULL_ZONE_KEY = "the-pull-zone-url-token-authentication-key"

EMBED_KEY = "the-stream-library-embed-view-token-key"


def settings_with(monkeypatch, **environment):
    from app.config import Settings

    for name in (
        "BUNNY_STREAM_PULL_ZONE_TOKEN_KEY",
        "BUNNY_STREAM_TOKEN_AUTH_KEY",
        "BUNNY_STREAM_TOKEN_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    return Settings()


def test_the_pull_zone_key_is_what_signs_media_urls(monkeypatch):
    settings = settings_with(
        monkeypatch,
        BUNNY_STREAM_PULL_ZONE_TOKEN_KEY=PULL_ZONE_KEY,
        BUNNY_STREAM_TOKEN_KEY=EMBED_KEY,
    )

    assert settings.bunny_token_auth_key == PULL_ZONE_KEY


def test_the_embed_key_is_never_used_for_signing(monkeypatch):
    """It signs iframe views; a file URL signed with it is refused."""

    settings = settings_with(monkeypatch, BUNNY_STREAM_TOKEN_KEY=EMBED_KEY)

    assert settings.bunny_token_auth_key == ""


def test_the_older_variable_name_still_works(monkeypatch):
    """A deployment that already set it must not break on this rename."""

    settings = settings_with(
        monkeypatch, BUNNY_STREAM_TOKEN_AUTH_KEY=PULL_ZONE_KEY
    )

    assert settings.bunny_token_auth_key == PULL_ZONE_KEY


def test_the_pull_zone_variable_wins_over_the_older_one(monkeypatch):
    settings = settings_with(
        monkeypatch,
        BUNNY_STREAM_PULL_ZONE_TOKEN_KEY=PULL_ZONE_KEY,
        BUNNY_STREAM_TOKEN_AUTH_KEY="something-stale",
    )

    assert settings.bunny_token_auth_key == PULL_ZONE_KEY


# -------------------------
# Saying so out loud
# -------------------------


def test_having_only_the_embed_key_is_called_out(monkeypatch):
    """The exact mix-up that produced 403 for every signing mode."""

    settings = settings_with(monkeypatch, BUNNY_STREAM_TOKEN_KEY=EMBED_KEY)

    warning = settings.bunny_key_warning()

    assert "BUNNY_STREAM_PULL_ZONE_TOKEN_KEY is not set" in warning
    assert "embed" in warning


def test_the_two_keys_holding_one_value_is_called_out(monkeypatch):
    settings = settings_with(
        monkeypatch,
        BUNNY_STREAM_PULL_ZONE_TOKEN_KEY=EMBED_KEY,
        BUNNY_STREAM_TOKEN_KEY=EMBED_KEY,
    )

    assert "same value" in settings.bunny_key_warning()


def test_no_key_at_all_says_urls_go_unsigned(monkeypatch):
    settings = settings_with(monkeypatch)

    assert "unsigned" in settings.bunny_key_warning()


def test_a_correct_configuration_warns_about_nothing(monkeypatch):
    settings = settings_with(
        monkeypatch,
        BUNNY_STREAM_PULL_ZONE_TOKEN_KEY=PULL_ZONE_KEY,
        BUNNY_STREAM_TOKEN_KEY=EMBED_KEY,
    )

    assert settings.bunny_key_warning() is None


def test_no_key_is_ever_printed_in_a_warning(monkeypatch):
    """A warning about a credential must not quote the credential."""

    settings = settings_with(
        monkeypatch,
        BUNNY_STREAM_PULL_ZONE_TOKEN_KEY=EMBED_KEY,
        BUNNY_STREAM_TOKEN_KEY=EMBED_KEY,
    )

    assert EMBED_KEY not in settings.bunny_key_warning()
