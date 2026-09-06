"""Is this URL one the GPU worker may fetch?

The serverless worker is handed a URL and downloads whatever is at the other
end, on a machine that holds a Hugging Face token, a RunPod API key and a GPU.
An unvalidated URL there is server-side request forgery with expensive
hardware attached — `http://169.254.169.254/…` is the classic one, and a
`file://` is worse.

So the check runs twice, deliberately. The application server refuses to submit
a URL that is not Bunny's, and the worker refuses to fetch one, because the two
trust different things: the server trusts its own database, and the worker
trusts nobody — anyone who can reach the RunPod endpoint can put a URL in a
job, and the endpoint id is not a secret.

Kept dependency-free (stdlib only, no app.config import) so the GPU image can
vendor this one module without pulling the application's settings in with it.

Both sides build the allowlist here too, with `bunny_hosts`. They used to build
it separately — the application server from BUNNY_STREAM_CDN_HOSTNAME, the GPU
worker from BUNNY_MEDIA_HOSTS — and neither read the other's variable, so a
deployment that configured only the documented one gave the two checks
different answers. The server would submit a lecture and the worker would
refuse to fetch it, after the GPU had already started. One builder, used by
both, is what stops the two allowlists from drifting again.
"""

import os
from urllib.parse import urlparse


# Bunny issues direct links on its own domain as well as through the pull zone,
# so this is allowed everywhere and is not configuration.
BUNNY_API_HOST = "video.bunnycdn.com"


class UntrustedMediaURL(Exception):
    """The URL is not one we are willing to fetch."""


def normalise_host(value):
    """A configured value -> the bare hostname it names, or None.

    Operators paste what they have, and what they have is often a URL. All of

        vz-37045c9a-1e8.b-cdn.net
        https://vz-37045c9a-1e8.b-cdn.net
        https://vz-37045c9a-1e8.b-cdn.net/
        VZ-37045C9A-1E8.B-CDN.NET

    name one host, and all four must produce the same entry — a stray scheme in
    the environment variable otherwise puts a string in the allowlist that no
    hostname can ever equal, and the failure appears at the far end as a
    refused media URL rather than as a configuration mistake.

    Parsed rather than string-trimmed. `urlparse(...).hostname` drops any port
    and any userinfo, so `cdn.example@evil.test` normalises to `evil.test` —
    the host that would actually be contacted — instead of being stored whole
    and matching nothing, or worse, being split on the wrong character.
    """

    if not value or not isinstance(value, str):
        return None

    value = value.strip()

    if not value:
        return None

    # urlparse only finds a host in something with an authority, so give a bare
    # hostname the "//" that makes it one.
    if "//" not in value:
        value = "//" + value

    host = (urlparse(value).hostname or "").strip().lower()

    return host or None


def bunny_hosts(cdn_hostname=None, extra_hosts=None):
    """The exact hostnames this deployment may fetch media from.

    Exact hostnames only, and never a suffix rule: `*.b-cdn.net` would be every
    pull zone on Bunny, including one an attacker can create for themselves,
    and this URL is fetched by a machine holding a GPU and two API keys.

    `extra_hosts` is a comma-separated string or an iterable, for a deployment
    serving more than one pull zone.
    """

    hosts = {BUNNY_API_HOST}

    configured = normalise_host(cdn_hostname)

    if configured:
        hosts.add(configured)

    if isinstance(extra_hosts, str):
        extra_hosts = extra_hosts.split(",")

    for host in extra_hosts or ():
        normalised = normalise_host(host)

        # Empty entries are dropped rather than added. An empty string in the
        # allowlist would sit there matching nothing useful, and a URL whose
        # host failed to parse would compare equal to it.
        if normalised:
            hosts.add(normalised)

    return hosts


def bunny_hosts_from_env(environ=None):
    """The allowlist for a process configured only by environment variables.

    What the GPU worker uses: it has no app.config, and it must arrive at the
    same set the application server did or it will refuse work the server
    accepted.
    """

    environ = os.environ if environ is None else environ

    return bunny_hosts(
        environ.get("BUNNY_STREAM_CDN_HOSTNAME"),
        environ.get("BUNNY_MEDIA_HOSTS"),
    )


def check(url, allowed_hosts):
    """Return the URL if it is fetchable, else raise UntrustedMediaURL.

    `allowed_hosts` is an exact-match set of hostnames. Suffix matching is
    deliberately not used: "endswith b-cdn.net" would accept
    `evil-b-cdn.net`, and "endswith .b-cdn.net" would accept every pull zone
    on Bunny rather than only ours.
    """

    if not url or not isinstance(url, str):
        raise UntrustedMediaURL("no media URL was given")

    parsed = urlparse(url)

    if parsed.scheme != "https":
        # http is refused as well as file/gopher/data: a plaintext fetch of a
        # lecture is not a security hole by itself, but allowing it removes
        # the guarantee that the host we checked is the host we reached.
        raise UntrustedMediaURL(
            f"media URL must be https, got {parsed.scheme or 'no'} scheme"
        )

    # `hostname` rather than `netloc`: netloc carries any port and userinfo, so
    # comparing it would let `https://cdn.example@evil.test/` through.
    host = (parsed.hostname or "").lower()

    if host not in {h.lower() for h in allowed_hosts}:
        raise UntrustedMediaURL(
            f"media URL host {host!r} is not one of the allowed Bunny hosts"
        )

    return url
