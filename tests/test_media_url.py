"""Which URLs the GPU worker may fetch.

The worker downloads whatever it is handed, on a machine with a GPU, a Hugging
Face token and a RunPod identity. These are the cases an attacker who can
submit a job would try.
"""

import pytest

from app.config import get_settings
from rag import media_url
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


# ------------------------------------------------------------------
# One allowlist for both sides
# ------------------------------------------------------------------
#
# Production symptom this covers:
#
#   media URL host 'vz-37045c9a-1e8.b-cdn.net' is not one of the allowed
#   Bunny hosts
#
# raised by the GPU worker on a job the application server had accepted. The
# server built its allowlist from BUNNY_STREAM_CDN_HOSTNAME and the worker
# built its own from BUNNY_MEDIA_HOSTS, so an endpoint configured the
# documented way produced two different answers and the disagreement only
# surfaced once a GPU was running.

PULL_ZONE = "vz-37045c9a-1e8.b-cdn.net"


def test_the_configured_pull_zone_is_accepted():
    hosts = media_url.bunny_hosts(PULL_ZONE)

    assert media_url.check(f"https://{PULL_ZONE}/guid/play_240p.mp4", hosts)


def test_a_different_pull_zone_on_bunny_is_refused():
    """Anyone can create a b-cdn.net pull zone; only ours may be fetched."""

    hosts = media_url.bunny_hosts(PULL_ZONE)

    with pytest.raises(media_url.UntrustedMediaURL):
        media_url.check("https://vz-someone-else.b-cdn.net/x.mp4", hosts)


def test_a_hostname_that_only_starts_with_ours_is_refused():
    """`vz-….b-cdn.net.evil.com` is an attacker's domain, not a pull zone."""

    hosts = media_url.bunny_hosts(PULL_ZONE)

    with pytest.raises(media_url.UntrustedMediaURL):
        media_url.check(f"https://{PULL_ZONE}.evil.com/x.mp4", hosts)


def test_a_hostname_that_only_ends_with_ours_is_refused():
    hosts = media_url.bunny_hosts(PULL_ZONE)

    with pytest.raises(media_url.UntrustedMediaURL):
        media_url.check(f"https://evil.com.{PULL_ZONE.replace('.', '-')}/x.mp4", hosts)


@pytest.mark.parametrize(
    "configured",
    [
        PULL_ZONE,
        f"  {PULL_ZONE}  ",
        PULL_ZONE.upper(),
        f"https://{PULL_ZONE}",
        f"https://{PULL_ZONE}/",
        f"http://{PULL_ZONE}",
        f"https://{PULL_ZONE}/some/path",
        f"{PULL_ZONE}:443",
    ],
)
def test_the_configured_value_is_normalised_to_one_hostname(configured):
    """Operators paste what they have; every form must name the same host."""

    assert media_url.normalise_host(configured) == PULL_ZONE

    assert PULL_ZONE in media_url.bunny_hosts(configured)


@pytest.mark.parametrize("configured", ["", "   ", None, 17, "https://"])
def test_an_unusable_configured_value_adds_nothing(configured):
    """An empty entry would sit in the set matching a URL with no host."""

    hosts = media_url.bunny_hosts(configured)

    assert hosts == {"video.bunnycdn.com"}
    assert "" not in hosts


def test_a_url_with_no_host_is_still_refused_when_config_was_empty():
    """The reason empties are dropped rather than added."""

    hosts = media_url.bunny_hosts("")

    with pytest.raises(media_url.UntrustedMediaURL):
        media_url.check("https:///guid/play.mp4", hosts)


def test_extra_pull_zones_are_accepted_as_a_comma_separated_list():
    hosts = media_url.bunny_hosts(PULL_ZONE, "vz-second.b-cdn.net, vz-third.b-cdn.net")

    assert hosts == {
        "video.bunnycdn.com", PULL_ZONE,
        "vz-second.b-cdn.net", "vz-third.b-cdn.net",
    }


def test_a_blank_entry_in_the_extra_list_is_dropped():
    hosts = media_url.bunny_hosts(PULL_ZONE, "vz-second.b-cdn.net,,  ,")

    assert hosts == {"video.bunnycdn.com", PULL_ZONE, "vz-second.b-cdn.net"}


def test_the_environment_builder_reads_the_documented_variable():
    """BUNNY_STREAM_CDN_HOSTNAME alone must be enough for the GPU worker."""

    hosts = media_url.bunny_hosts_from_env(
        {"BUNNY_STREAM_CDN_HOSTNAME": PULL_ZONE}
    )

    assert PULL_ZONE in hosts


def test_the_server_and_the_worker_agree_on_the_same_configuration(monkeypatch):
    """The regression itself: one configuration, one allowlist, both sides."""

    settings = get_settings()
    monkeypatch.setattr(settings, "bunny_cdn_hostname", PULL_ZONE)
    monkeypatch.setattr(settings, "bunny_media_hosts_extra", "")

    worker_hosts = media_url.bunny_hosts_from_env(
        {"BUNNY_STREAM_CDN_HOSTNAME": PULL_ZONE}
    )

    assert settings.bunny_media_hosts() == worker_hosts


def test_both_sides_agree_when_extra_zones_are_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "bunny_cdn_hostname", PULL_ZONE)
    monkeypatch.setattr(settings, "bunny_media_hosts_extra", "vz-second.b-cdn.net")

    worker_hosts = media_url.bunny_hosts_from_env({
        "BUNNY_STREAM_CDN_HOSTNAME": PULL_ZONE,
        "BUNNY_MEDIA_HOSTS": "vz-second.b-cdn.net",
    })

    assert settings.bunny_media_hosts() == worker_hosts


def test_the_gpu_worker_still_refuses_the_metadata_address(monkeypatch):
    """Normalisation must not have opened a door to the classic SSRF target."""

    hosts = media_url.bunny_hosts_from_env(
        {"BUNNY_STREAM_CDN_HOSTNAME": PULL_ZONE}
    )

    for target in (
        "https://169.254.169.254/latest/meta-data/",
        "https://localhost/x.mp4",
        "https://127.0.0.1/x.mp4",
        "https://10.0.0.5/x.mp4",
        "https://[::1]/x.mp4",
        "file:///etc/passwd",
        f"http://{PULL_ZONE}/x.mp4",
    ):
        with pytest.raises(media_url.UntrustedMediaURL):
            media_url.check(target, hosts)
