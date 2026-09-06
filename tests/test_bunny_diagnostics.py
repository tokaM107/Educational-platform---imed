"""The four-probe diagnostic that separates the two Bunny protections.

Production reports 403 for unsigned, advanced and basic alike. Signing cannot
fix a referer requirement and a Referer cannot fix a bad token, so the way to
tell which mechanism is refusing is to vary both independently over one URL:

    1  unsigned, no Referer        what RunPod sends today
    2  unsigned, allowed Referer   isolates the referer allowlist
    3  signed,   no Referer        what the worker would send
    4  signed,   allowed Referer   both at once

These tests hold the requests still and assert on what was asked for, and on
what reached stdout — a diagnostic that printed the key or a token would leak a
credential into a terminal, a scrollback and probably a support ticket.
"""

import pytest

from app.config import get_settings
from rag import bunny


HOST = "vz-37045c9a-1e8.b-cdn.net"

GUID = "78cf0406-87dc-4186-be87-2af6e5699cca"

KEY = "the-pull-zone-url-token-authentication-key"

REFERER = "app.example.com"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "bunny_cdn_hostname", HOST)
    monkeypatch.setattr(settings, "bunny_token_auth_key", KEY)
    monkeypatch.setattr(settings, "bunny_token_auth_algorithm", "advanced")
    monkeypatch.setattr(settings, "bunny_media_url_ttl_seconds", 1800)
    monkeypatch.setattr(settings, "bunny_media_hosts_extra", "")
    monkeypatch.setattr(
        bunny, "get_video",
        lambda guid: {"guid": guid, "status": 4, "hasMP4Fallback": True,
                      "availableResolutions": "240p"},
    )
    return settings


class Response:
    def __init__(self, status):
        self.status_code = status

    def close(self):
        pass


def fake_cdn(monkeypatch, statuses):
    """Answer each probe by (signed?, referer?). Records every request."""

    seen = []

    def get(url, headers=None, timeout=None, stream=None):
        headers = headers or {}
        signed = "token=" in url
        refered = "Referer" in headers

        seen.append({
            "url": url, "headers": headers,
            "signed": signed, "referer": refered,
        })

        return Response(statuses[(signed, refered)])

    monkeypatch.setattr(bunny.requests, "get", get)

    return seen


ALL_REFUSED = {(False, False): 403, (False, True): 403,
               (True, False): 403, (True, True): 403}

TOKEN_IS_ENOUGH = {(False, False): 403, (False, True): 206,
                   (True, False): 206, (True, True): 206}

REFERER_ALSO_REQUIRED = {(False, False): 403, (False, True): 206,
                         (True, False): 403, (True, True): 206}


# -------------------------
# The probes themselves
# -------------------------


def test_exactly_four_probes_are_made(monkeypatch, capsys):
    seen = fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    assert len(seen) == 4


def test_the_four_probes_are_the_four_combinations(monkeypatch, capsys):
    seen = fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    assert [(p["signed"], p["referer"]) for p in seen] == [
        (False, False), (False, True), (True, False), (True, True),
    ]


def test_every_probe_asks_for_the_same_url(monkeypatch, capsys):
    """Varying two things at once would prove nothing about either."""

    seen = fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    paths = {p["url"].split("?")[0] for p in seen}

    assert paths == {f"https://{HOST}/{GUID}/play_240p.mp4"}


def test_every_probe_asks_for_one_byte(monkeypatch, capsys):
    seen = fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    assert all(p["headers"].get("Range") == "bytes=0-0" for p in seen)


def test_the_referer_is_sent_as_a_browser_would(monkeypatch, capsys):
    seen = fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    referers = {p["headers"]["Referer"] for p in seen if p["referer"]}

    assert referers == {f"https://{REFERER}/"}


def test_a_referer_given_with_a_scheme_is_not_doubled(monkeypatch, capsys):
    seen = fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer="https://app.example.com")

    assert {p["headers"]["Referer"] for p in seen if p["referer"]} == {
        "https://app.example.com/"
    }


# -------------------------
# What it concludes
# -------------------------


def test_it_reports_when_the_token_alone_is_enough(monkeypatch, capsys):
    fake_cdn(monkeypatch, TOKEN_IS_ENOUGH)

    assert bunny.check_direct_access(GUID, referer=REFERER) == 0

    assert "token is sufficient on its own" in capsys.readouterr().out


def test_it_reports_when_a_referer_is_required_as_well(monkeypatch, capsys):
    """The question this diagnostic exists to answer."""

    fake_cdn(monkeypatch, REFERER_ALSO_REQUIRED)

    assert bunny.check_direct_access(GUID, referer=REFERER) == 1

    out = capsys.readouterr().out

    assert "Token AND Referer are both required" in out
    assert "signing alone cannot fix RunPod" in out


def test_it_points_at_the_key_when_only_the_referer_works(monkeypatch, capsys):
    fake_cdn(monkeypatch, {(False, False): 403, (False, True): 206,
                           (True, False): 403, (True, True): 403})

    bunny.check_direct_access(GUID, referer=REFERER)

    assert "points at the key or the mode" in capsys.readouterr().out


def test_it_says_so_when_even_an_allowed_referer_is_refused(monkeypatch, capsys):
    fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    assert "neither the token nor the referer" in capsys.readouterr().out


# -------------------------
# Secrets
# -------------------------


def test_the_key_is_never_printed(monkeypatch, capsys):
    fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    assert KEY not in capsys.readouterr().out


def test_no_token_is_ever_printed(monkeypatch, capsys):
    """A token is a bearer credential for the video it names."""

    seen = fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    out = capsys.readouterr().out

    tokens = [
        p["url"].split("token=")[1].split("&")[0]
        for p in seen if p["signed"]
    ]

    assert tokens
    assert all(token not in out for token in tokens)


def test_no_full_signed_url_is_printed(monkeypatch, capsys):
    seen = fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    out = capsys.readouterr().out

    assert not any(p["url"] in out for p in seen if p["signed"])


def test_it_prints_probe_names_and_statuses(monkeypatch, capsys):
    fake_cdn(monkeypatch, ALL_REFUSED)

    bunny.check_direct_access(GUID, referer=REFERER)

    out = capsys.readouterr().out

    for label in ("unsigned, no Referer", "unsigned, allowed Referer",
                  "signed, no Referer", "signed, allowed Referer"):
        assert label in out

    assert "HTTP 403" in out


# -------------------------
# Guards
# -------------------------


def test_a_missing_referer_is_refused_with_an_explanation(monkeypatch):
    fake_cdn(monkeypatch, ALL_REFUSED)

    with pytest.raises(bunny.BunnyError) as caught:
        bunny.check_direct_access(GUID)

    assert "Allowed domains" in str(caught.value)


def test_a_missing_key_is_refused_rather_than_probing_half_the_matrix(
    monkeypatch,
):
    monkeypatch.setattr(get_settings(), "bunny_token_auth_key", "")
    fake_cdn(monkeypatch, ALL_REFUSED)

    with pytest.raises(bunny.BunnyError):
        bunny.check_direct_access(GUID, referer=REFERER)


def test_the_diagnostic_obeys_the_ssrf_allowlist(monkeypatch):
    """It fetches too, so it is held to the same rule as the worker.

    In practice the URL is always built from the configured hostname, which is
    also what the allowlist is derived from, so the two cannot drift apart on
    their own. The guard is here for the case where they are made to: a
    resolver that ever returned a host from somewhere less trustworthy than
    configuration must not be fetched by a tool running with production
    credentials.
    """

    from rag import media_url

    monkeypatch.setattr(
        bunny, "diagnostic_url",
        lambda guid: ("https://evil.test/x/play_240p.mp4", "yes"),
    )
    probes = fake_cdn(monkeypatch, ALL_REFUSED)

    with pytest.raises(media_url.UntrustedMediaURL):
        bunny.check_direct_access(GUID, referer=REFERER)

    assert probes == []


def test_a_private_address_is_refused_before_any_request(monkeypatch):
    from rag import media_url

    monkeypatch.setattr(
        bunny, "diagnostic_url",
        lambda guid: ("https://127.0.0.1/x/play_240p.mp4", "yes"),
    )
    probes = fake_cdn(monkeypatch, ALL_REFUSED)

    with pytest.raises(media_url.UntrustedMediaURL):
        bunny.check_direct_access(GUID, referer=REFERER)

    assert probes == []


def test_production_signing_is_untouched_by_this_command():
    """A diagnostic must not change what the worker sends."""

    import inspect

    source = inspect.getsource(bunny.check_direct_access)

    for forbidden in ("bunny_token_auth_key =", "settings.bunny_token_auth",
                      "os.environ["):
        assert f"{forbidden}" not in source.replace(
            "key = settings.bunny_token_auth_key", ""
        )
