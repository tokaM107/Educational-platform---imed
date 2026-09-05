"""Which URLs the GPU worker may fetch.

The worker downloads whatever it is handed, on a machine with a GPU, a Hugging
Face token and a RunPod identity. These are the cases an attacker who can
submit a job would try.
"""

import pytest

from app.config import get_settings
from rag.media_url import UntrustedMediaURL, check


ALLOWED = {"cdn.example", "video.bunnycdn.com"}


def test_a_bunny_rendition_is_allowed():
    url = "https://cdn.example/guid/play_240p.mp4"

    assert check(url, ALLOWED) == url


def test_an_unrelated_host_is_refused():
    with pytest.raises(UntrustedMediaURL, match="allowed Bunny hosts"):
        check("https://evil.test/payload.mp4", ALLOWED)


def test_the_cloud_metadata_endpoint_is_refused():
    """The canonical SSRF target: credentials for the whole account."""

    with pytest.raises(UntrustedMediaURL):
        check("http://169.254.169.254/latest/meta-data/", ALLOWED)


def test_a_file_url_is_refused():
    with pytest.raises(UntrustedMediaURL, match="https"):
        check("file:///etc/passwd", ALLOWED)


def test_plain_http_is_refused_even_on_an_allowed_host():
    """Otherwise the host that was checked need not be the host reached."""

    with pytest.raises(UntrustedMediaURL, match="https"):
        check("http://cdn.example/guid/play_240p.mp4", ALLOWED)


def test_a_host_smuggled_through_userinfo_is_refused():
    """`https://cdn.example@evil.test/` has hostname evil.test."""

    with pytest.raises(UntrustedMediaURL):
        check("https://cdn.example@evil.test/x.mp4", ALLOWED)


def test_a_lookalike_suffix_host_is_refused():
    """Why the match is exact: "endswith" would accept this."""

    with pytest.raises(UntrustedMediaURL):
        check("https://evil-cdn.example.attacker.test/x.mp4", ALLOWED)


def test_another_pull_zone_on_bunny_is_refused():
    """Ours, not every customer's — "endswith .b-cdn.net" would allow theirs."""

    with pytest.raises(UntrustedMediaURL):
        check("https://vz-someone-else.b-cdn.net/x.mp4", {"vz-ours.b-cdn.net"})


def test_an_empty_url_is_refused():
    with pytest.raises(UntrustedMediaURL):
        check("", ALLOWED)


def test_a_non_string_is_refused():
    with pytest.raises(UntrustedMediaURL):
        check(None, ALLOWED)


def test_the_configured_pull_zone_is_in_the_allowed_set(monkeypatch):
    """What the application server actually enforces at submit time."""

    settings = get_settings()
    monkeypatch.setattr(settings, "bunny_cdn_hostname", "vz-ours.b-cdn.net/")

    hosts = settings.bunny_media_hosts()

    assert "vz-ours.b-cdn.net" in hosts
    assert "video.bunnycdn.com" in hosts
