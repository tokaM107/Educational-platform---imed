"""The Referer ffmpeg sends when it fetches a lecture from Bunny.

Bunny's "Block Direct URL File Access" checks the referring domain. A browser
sends one; a serverless GPU browsing nothing sends none, so a correctly signed
URL can still be refused on the RunPod worker and nowhere else. BUNNY_MEDIA_REFERER
is what closes that gap, and it is read in rag/audio.py because that is the
module the GPU image carries — the worker has no application settings.

Unset must mean exactly the previous behaviour: a deployment whose pull zone
does not check referrers should not start sending one.
"""

import pytest

from rag import audio, media_url


HTTP_SOURCE = "https://vz-37045c9a-1e8.b-cdn.net/guid/play_240p.mp4?token=T&expires=9"

REFERER = "eduguide-eg.com"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("BUNNY_MEDIA_REFERER", raising=False)


def options(source=HTTP_SOURCE):
    return audio._reconnect_options(source)


# -------------------------
# The header itself
# -------------------------


def test_the_ffmpeg_command_carries_the_referer(monkeypatch):
    monkeypatch.setenv("BUNNY_MEDIA_REFERER", REFERER)

    flags = options()

    assert "-referer" in flags
    assert flags[flags.index("-referer") + 1] == f"https://{REFERER}/"


def test_the_referer_is_a_url_as_a_browser_would_send_it(monkeypatch):
    monkeypatch.setenv("BUNNY_MEDIA_REFERER", REFERER)

    assert audio.media_referer() == "https://eduguide-eg.com/"


@pytest.mark.parametrize("configured", [
    "eduguide-eg.com",
    "https://eduguide-eg.com",
    "https://eduguide-eg.com/",
    "  eduguide-eg.com  ",
])
def test_the_domain_may_be_written_any_of_the_usual_ways(monkeypatch, configured):
    monkeypatch.setenv("BUNNY_MEDIA_REFERER", configured)

    assert audio.media_referer() == "https://eduguide-eg.com/"


def test_ffmpegs_own_option_is_used_rather_than_a_raw_header(monkeypatch):
    """`-headers` takes raw text; a newline in it would inject a second header."""

    monkeypatch.setenv("BUNNY_MEDIA_REFERER", REFERER)

    assert "-headers" not in options()


@pytest.mark.parametrize("hostile", [
    "eduguide-eg.com\r\nX-Injected: 1",
    "eduguide-eg.com\nX-Injected: 1",
    "eduguide eg.com",
])
def test_a_value_that_could_forge_a_header_is_refused(monkeypatch, hostile):
    monkeypatch.setenv("BUNNY_MEDIA_REFERER", hostile)

    with pytest.raises(audio.AudioError):
        audio.media_referer()


# -------------------------
# Unset means unchanged
# -------------------------


def test_no_referer_is_sent_when_it_is_not_configured():
    assert "-referer" not in options()


def test_the_reconnect_flags_are_untouched_either_way(monkeypatch):
    before = options()

    monkeypatch.setenv("BUNNY_MEDIA_REFERER", REFERER)

    after = options()

    assert before == after[:len(before)]
    assert "-reconnect" in before


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_setting_is_the_same_as_unset(monkeypatch, value):
    monkeypatch.setenv("BUNNY_MEDIA_REFERER", value)

    assert audio.media_referer() == ""
    assert "-referer" not in options()


# -------------------------
# Local files are not HTTP
# -------------------------


@pytest.mark.parametrize("source", ["/tmp/lecture.wav", "lecture.wav"])
def test_a_local_path_gets_no_http_options_at_all(monkeypatch, source):
    """Passing them with a local input fails the whole command."""

    monkeypatch.setenv("BUNNY_MEDIA_REFERER", REFERER)

    assert options(source) == []


# -------------------------
# The signed URL is not leaked
# -------------------------


def test_ffmpeg_stderr_has_its_token_removed():
    """ffmpeg quotes the URL it could not read, straight into our error."""

    stderr = (
        "[https @ 0x1] HTTP error 403 Forbidden\n"
        f"{HTTP_SOURCE}: Server returned 403 Forbidden"
    )

    cleaned = media_url.redact(stderr)

    assert "token=T" not in cleaned
    assert "expires=9" not in cleaned
    assert "403 Forbidden" in cleaned


def test_redaction_keeps_the_part_that_identifies_the_video():
    """The path is what makes an error actionable; the token is not."""

    cleaned = media_url.redact(HTTP_SOURCE)

    assert "/guid/play_240p.mp4" in cleaned


def test_a_url_with_no_credentials_is_left_alone():
    plain = "https://vz-37045c9a-1e8.b-cdn.net/guid/play_240p.mp4"

    assert media_url.redact(plain) == plain


def test_an_error_from_a_failed_fetch_is_redacted(monkeypatch, tmp_path):
    """The end-to-end path: ffmpeg fails, and what we raise carries no token."""

    log = tmp_path / "ffmpeg.log"
    log.write_text(
        f"{HTTP_SOURCE}: Server returned 403 Forbidden\n", encoding="utf-8"
    )

    assert "token=T" not in audio._tail(log)


def test_the_referer_is_not_a_secret_but_the_token_is(monkeypatch):
    """A Referer is a public domain name; it may appear in an error."""

    monkeypatch.setenv("BUNNY_MEDIA_REFERER", REFERER)

    assert REFERER in " ".join(options())


# -------------------------
# Nothing else moved
# -------------------------


def test_the_gpu_image_already_carries_the_module_this_imports():
    """rag/audio.py now imports rag/media_url.py; both must be in the image."""

    from pathlib import Path

    dockerfile = Path(__file__).resolve().parent.parent / "gpu" / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")

    assert "rag/audio.py" in text and "rag/media_url.py" in text
