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
"""

from urllib.parse import urlparse


class UntrustedMediaURL(Exception):
    """The URL is not one we are willing to fetch."""


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
