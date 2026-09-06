"""Bunny Stream: put a lecture video there, and find its audio again.

The transcription pipeline used to start at a file on this laptop. It now
starts at a video in a Stream library, which is where the videos are actually
going to live — so `data/videos/` stops being a place the pipeline depends on
and becomes, at most, the staging area a file passes through on its way up.

Two things this module deliberately does *not* do.

It does not download the video. ffmpeg reads an HTTP URL as happily as a path,
so the audio is extracted from Bunny's copy in one pass with nothing written to
disk but the .wav — see rag/audio.py.

And it asks for the *smallest* rendition, not the best one. Every rendition
carries the same soundtrack, so for a job that throws the picture away
immediately, fetching 1080p instead of 240p is several hundred megabytes spent
on pixels that go straight to /dev/null.
"""

import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode, urlparse, urlunparse

import requests

from app.config import get_settings


API_ROOT = "https://video.bunnycdn.com"

# Encoding is not instant and a video is not readable until it finishes.
FINISHED = 4
FAILED = {5, 6}

STATUS_NAMES = {
    0: "Created",
    1: "Uploaded",
    2: "Processing",
    3: "Transcoding",
    4: "Finished",
    5: "Error",
    6: "UploadFailed",
    7: "JitSegmenting",
    8: "JitPlaylistsCreated",
}


class BunnyError(Exception):
    """Bunny refused, or returned something the pipeline cannot use."""


def _config():

    library_id, api_key, hostname = get_settings().require_bunny()

    return library_id, api_key, hostname


def _headers(api_key, content_type=None):

    headers = {"AccessKey": api_key, "accept": "application/json"}

    if content_type:
        headers["Content-Type"] = content_type

    return headers


def _check(response, what):

    if response.status_code >= 400:
        # The body usually says more than the status does, and this is an
        # operator-facing CLI, so it is worth printing rather than hiding.
        raise BunnyError(
            f"{what} failed: HTTP {response.status_code} {response.text[:300]}"
        )

    return response


# -------------------------
# Videos
# -------------------------


def create_video(title, collection_id=None, timeout=30):
    """Reserve a video and return its guid. Uploads nothing."""

    library_id, api_key, _ = _config()

    body = {"title": title}

    if collection_id:
        body["collectionId"] = collection_id

    response = requests.post(
        f"{API_ROOT}/library/{library_id}/videos",
        headers=_headers(api_key, "application/json"),
        json=body,
        timeout=timeout,
    )

    guid = _check(response, "create_video").json().get("guid")

    if not guid:
        raise BunnyError("create_video returned no guid")

    return guid


def upload_video(guid, path, timeout=None):
    """Send the file itself, streamed from disk.

    Handed an open file rather than its contents: a lecture is comfortably
    larger than it is polite to hold in memory, and requests will read it in
    chunks off the handle.

    No timeout by default. A read timeout on an upload measured in gigabytes
    interrupts a transfer that was going perfectly well.
    """

    library_id, api_key, _ = _config()

    with open(path, "rb") as handle:

        response = requests.put(
            f"{API_ROOT}/library/{library_id}/videos/{guid}",
            headers=_headers(api_key, "application/octet-stream"),
            data=handle,
            timeout=timeout,
        )

    _check(response, "upload_video")

    return guid


def get_video(guid, timeout=30):
    """Everything Bunny knows about one video."""

    library_id, api_key, _ = _config()

    response = requests.get(
        f"{API_ROOT}/library/{library_id}/videos/{guid}",
        headers=_headers(api_key),
        timeout=timeout,
    )

    return _check(response, "get_video").json()


def is_finished(video):
    """Whether the video is encoded and readable.

    Worth checking before doing anything expensive: a video still transcoding
    has no renditions, so a pipeline that skipped this would fail at ffmpeg
    with a 404 rather than saying "it is not ready yet".
    """

    return video.get("status") == FINISHED


def list_videos(page=1, per_page=100, search=None, collection_id=None, timeout=30):
    """One page of the library. Returns (items, total_items).

    Bunny pages this endpoint and the page size is capped, so the total is
    returned alongside the items — a caller that ignored it would silently
    process the first hundred lectures and believe that was all of them.
    """

    library_id, api_key, _ = _config()

    params = {"page": page, "itemsPerPage": per_page}

    if search:
        params["search"] = search

    if collection_id:
        params["collection"] = collection_id

    response = requests.get(
        f"{API_ROOT}/library/{library_id}/videos",
        headers=_headers(api_key),
        params=params,
        timeout=timeout,
    )

    body = _check(response, "list_videos").json()

    return body.get("items", []), body.get("totalItems", 0)


def iter_videos(per_page=100, **filters):
    """Every video in the library, page by page.

    A generator so a library of hundreds is not assembled in memory before the
    first one can be looked at, and so a batch job that stops early stops
    fetching too.
    """

    page = 1
    seen = 0

    while True:

        items, total = list_videos(page=page, per_page=per_page, **filters)

        if not items:
            return

        for item in items:
            yield item

        seen += len(items)

        if seen >= total:
            return

        page += 1


def status_name(video):

    return STATUS_NAMES.get(video.get("status"), f"Unknown({video.get('status')})")


def wait_until_encoded(guid, timeout=3600, poll=15, on_progress=None):
    """Block until the video is playable, or give up saying why.

    Encoding a lecture takes minutes, and until it finishes there are no
    renditions to read audio from — a pipeline that went straight from upload to
    ffmpeg would reliably fail on its own upload.
    """

    deadline = time.monotonic() + timeout

    while True:

        video = get_video(guid)
        status = video.get("status")

        if status == FINISHED:
            return video

        if status in FAILED:
            raise BunnyError(
                f"encoding failed for {guid}: {status_name(video)}"
            )

        if time.monotonic() > deadline:
            raise BunnyError(
                f"gave up waiting for {guid} after {timeout}s "
                f"(still {status_name(video)})"
            )

        if on_progress:
            on_progress(status_name(video), video.get("encodeProgress", 0))

        time.sleep(poll)


# -------------------------
# Finding the audio
# -------------------------


def resolutions(video):
    """The rendition heights Bunny produced, smallest first.

    `availableResolutions` arrives as a string like "240p,360p,720p", and is
    null until encoding has got far enough to know.
    """

    raw = video.get("availableResolutions") or ""

    heights = []

    for item in raw.split(","):

        item = item.strip().lower().removesuffix("p")

        if item.isdigit():
            heights.append(int(item))

    return sorted(heights)


# -------------------------
# Token authentication
# -------------------------
#
# The Stream pull zone has token authentication switched on, so an unsigned URL
# is refused before Bunny looks at what was asked for -- a request for a guid
# that does not exist answers 403 exactly like a real one. That is the whole of
# why the GPU worker could not read video 28.
#
# Signing happens here, once, so everything that hands a Bunny URL to anything
# gets a signed one: the transcription pipeline, and the playback redirect in
# app/api/videos.py, which would otherwise be refused the same way.
#
# The security key never leaves this process. What travels is the token, which
# is a hash -- RunPod is given a URL that expires and nothing else.

# Bunny's two documented CDN token-authentication modes, named as Bunny names
# them. This is *pull zone* token authentication, which is what protects direct
# file URLs on vz-….b-cdn.net — MP4 fallbacks, HLS playlists and segments,
# thumbnails and previews. It is not the Stream embed token, which signs
# SHA256_HEX(key + video_id + expires) for iframe.mediadelivery.net and has no
# effect on a direct file URL.
#
#   basic      Base64(MD5(key + path + expires))
#   advanced   "HS256-" + Base64URL(HMAC-SHA256(key, path + expires))
#
# A plain SHA256(key + path + expires) is NOT a Bunny format. An earlier
# version of this module offered one and defaulted to it, which is why every
# form was refused: two of the three being wrong is expected, but the default
# was a scheme Bunny has never accepted.
#
# No client IP is signed. Bunny's advanced mode can bind a token to an address
# and marks it with a "1-" flag when it does, but RunPod's serverless workers
# have no stable egress address, so a token bound to one would be refused for
# the machine that actually downloads the lecture.
TOKEN_MODES = ("advanced", "basic")

# What people write in configuration, mapped onto the two real modes. The hash
# names are accepted because that is what this module called them before.
TOKEN_MODE_ALIASES = {
    "advanced": "advanced",
    "hmac": "advanced",
    "hmac_sha256": "advanced",
    "hs256": "advanced",
    "basic": "basic",
    "md5": "basic",
}


def _urlsafe(digest):
    """Base64, transformed the way Bunny specifies: + -> -, / -> _, = dropped."""

    return (
        base64.b64encode(digest)
        .decode()
        .replace("+", "-")
        .replace("/", "_")
        .replace("=", "")
    )


def token_mode(name=None):
    """The Bunny mode a configured value names, or an error saying what is valid."""

    name = (name or get_settings().bunny_token_auth_algorithm or "").strip().lower()

    if name in TOKEN_MODE_ALIASES:
        return TOKEN_MODE_ALIASES[name]

    if name == "sha256":
        raise BunnyError(
            "BUNNY_TOKEN_AUTH_ALGORITHM=sha256 is not a Bunny token format. "
            "Bunny signs with MD5 (basic) or HMAC-SHA256 (advanced). Set "
            "'advanced' or 'basic' — `python -m rag.bunny check-token-auth "
            "<guid>` will say which this pull zone accepts."
        )

    raise BunnyError(
        f"unknown Bunny token mode {name!r} (expected one of "
        f"{', '.join(TOKEN_MODES)})"
    )


def _token(key, path, expires, mode):
    """Bunny's token for one path and expiry.

    The path is signed and the query string is not, so a token is bound to one
    file: it cannot be lifted from one lecture's URL and replayed against
    another, and it cannot be widened by appending parameters.
    """

    mode = token_mode(mode)

    if mode == "advanced":
        # The key is the HMAC key rather than part of the message, which is the
        # difference between this and the basic mode below.
        digest = hmac.new(
            key.encode(), f"{path}{expires}".encode(), hashlib.sha256
        ).digest()

        return "HS256-" + _urlsafe(digest)

    return _urlsafe(hashlib.md5(f"{key}{path}{expires}".encode()).digest())


def sign_url(url, ttl_seconds=None, key=None, algorithm=None, now=None):
    """Add Bunny's `token` and `expires` to a media URL. Returns it unchanged
    if no security key is configured.

    Unconfigured means unsigned rather than an error: a deployment whose pull
    zone does not use token authentication is a working deployment, and this
    must not start refusing to build URLs for it.

    Only the path is signed, so the token is worthless for any other video --
    it cannot be lifted from one lecture's URL and replayed against another.
    """

    settings = get_settings()

    key = settings.bunny_token_auth_key if key is None else key

    if not key:
        return url

    ttl = ttl_seconds or settings.bunny_media_url_ttl_seconds
    mode = token_mode(algorithm)

    parsed = urlparse(url)

    expires = int((time.time() if now is None else now)) + int(ttl)

    token = _token(key, parsed.path, expires, mode)

    # Appended rather than merged: these URLs are built by this module and
    # carry no query string of their own, and Bunny's basic scheme signs the
    # path only, so a pre-existing query would not be covered by the token.
    query = urlencode({"token": token, "expires": expires})

    return urlunparse(parsed._replace(query=query))


def redact(url):
    """A media URL safe to log: the token and expiry removed.

    The token is not the security key and cannot be reversed into it, but it is
    a bearer credential for one video for as long as it lives, and job logs are
    read by more people and kept for longer than that.
    """

    if not url or not isinstance(url, str):
        return url

    parsed = urlparse(url)

    return urlunparse(parsed._replace(query="")) + ("?…" if parsed.query else "")


def playlist_url(video, sign=True):
    """The HLS master playlist. Always available; slower to read than an MP4."""

    _, _, hostname = _config()

    url = f"https://{hostname}/{_require_guid(video)}/playlist.m3u8"

    return sign_url(url) if sign else url


def _require_guid(video):

    guid = video.get("guid")

    if not guid:
        raise BunnyError("video has no guid")

    return guid


def pick_height(video, prefer="lowest"):
    """Which rendition height to ask for, out of the ones that exist.

    `prefer` is "lowest", "highest", or a height in pixels. A number is a
    request, not a demand: Bunny does not upscale, so asking for 1080 from a
    720p source has to resolve to something that is actually there. The nearest
    available height at or below the request is used, and the smallest one if
    the request is below everything.
    """

    heights = resolutions(video)

    if not heights:
        return None

    if prefer == "lowest":
        return heights[0]

    if prefer == "highest":
        return heights[-1]

    wanted = int(prefer)
    at_or_below = [h for h in heights if h <= wanted]

    return at_or_below[-1] if at_or_below else heights[0]


def rendition_url(video, prefer="lowest", sign=True):
    """A URL ffmpeg can read this video from.

    Falls back to the HLS playlist when the library has no MP4 fallback, since
    that has to be enabled *before* a video is uploaded and older videos in a
    library will not have it. ffmpeg reads HLS fine; it is just slower, because
    it collects the segments one at a time.
    """

    guid = _require_guid(video)
    _, _, hostname = _config()

    height = pick_height(video, prefer)

    if video.get("hasMP4Fallback") and height:
        url = f"https://{hostname}/{guid}/play_{height}p.mp4"
        return sign_url(url) if sign else url

    return playlist_url(video, sign=sign)


def audio_source_url(video, sign=True):
    """Where to read this video's soundtrack from.

    The smallest rendition, deliberately. Every rendition carries the same
    audio, and the transcription pipeline throws the picture away the moment
    ffmpeg has read it — so fetching 1080p instead of 240p spends several
    hundred megabytes on pixels that go straight to /dev/null.

    Anything that actually looks at the image wants `rendition_url(video,
    prefer="highest")` instead, which is why the two are separate names rather
    than one function with a default.
    """

    return rendition_url(video, prefer="lowest", sign=sign)


# -------------------------
# The whole of the upload half
# -------------------------


def ensure_uploaded(title, path=None, guid=None, on_progress=None):
    """The guid of a finished video, uploading it first if there is not one.

    Idempotent on purpose: `guid` is what the caller already has stored, and
    passing it makes this a status check rather than a second upload. Re-running
    the pipeline on a lecture should cost nothing but the transcription.
    """

    if guid:
        video = get_video(guid)

        if video.get("status") == FINISHED:
            return guid, video

        return guid, wait_until_encoded(guid, on_progress=on_progress)

    if path is None:
        raise BunnyError("need either an existing guid or a file to upload")

    guid = create_video(title)
    upload_video(guid, path)

    return guid, wait_until_encoded(guid, on_progress=on_progress)


# -------------------------
# CLI
# -------------------------
#
#   python -m rag.bunny list                 every video, with status and id
#   python -m rag.bunny show <guid>          one video in detail
#
# Mostly for finding a guid to hand to `python -m rag.transcribe`, and for
# answering "is it done encoding yet" without opening the dashboard.


def _format_length(seconds):

    seconds = int(seconds or 0)

    return f"{seconds // 60}m{seconds % 60:02d}s"


def main(argv=None):

    import argparse

    parser = argparse.ArgumentParser(description="Inspect the Bunny Stream library.")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="every video in the library")
    listing.add_argument("--search", help="filter by title")
    listing.add_argument("--unfinished", action="store_true",
                         help="only videos that are not encoded yet")

    show = sub.add_parser("show", help="one video in detail")
    show.add_argument("guid")

    check = sub.add_parser(
        "check-token-auth",
        help="ask the CDN which token form this pull zone accepts",
    )
    check.add_argument("guid")

    args = parser.parse_args(argv)

    # A missing key or a rejected request is an ordinary thing to get wrong
    # here, and a traceback tells an operator nothing they can act on.
    try:
        return _run(args)
    except (BunnyError, RuntimeError) as error:
        raise SystemExit(f"bunny: {error}")


def check_token_auth(guid):
    """Ask the CDN which token mode this pull zone accepts.

    Bunny's two modes are indistinguishable from outside — the wrong one is
    refused with the same 403 as no token at all — so this asks for one byte
    with each and reports what came back.

    Prints modes and HTTP statuses only. The security key is never printed, and
    neither is a token: a token is a bearer credential for the video it names.
    """

    settings = get_settings()

    # The Stream API is asked first, because it knows which renditions exist.
    # It is not required though: this tool is most useful when something is
    # misconfigured, and refusing to run because the API key is also wrong
    # would be refusing exactly when it is wanted.
    try:
        video = get_video(guid)
        unsigned = rendition_url(video, prefer="lowest", sign=False)
        mp4 = "yes" if video.get("hasMP4Fallback") else "NO — HLS only"

    except (BunnyError, RuntimeError, requests.RequestException) as error:
        hostname = settings.bunny_cdn_hostname.strip().rstrip("/")

        if not hostname:
            raise BunnyError(
                "BUNNY_STREAM_CDN_HOSTNAME is not set, and the Stream API "
                f"could not be reached either: {error}"
            ) from error

        unsigned = f"https://{hostname}/{guid}/play_240p.mp4"
        mp4 = f"unknown (Stream API unavailable: {error})"

    key = settings.bunny_token_auth_key

    print(f"video    {guid}")
    print(f"path     {urlparse(unsigned).path}")
    print(f"host     {urlparse(unsigned).hostname}")
    print(f"mp4      {mp4}")
    print(f"key      {'set' if key else 'NOT SET (BUNNY_STREAM_TOKEN_AUTH_KEY)'}")
    print()

    def probe(label, url):
        try:
            response = requests.get(
                url, headers={"Range": "bytes=0-0"}, timeout=30, stream=True
            )
            response.close()
            status = response.status_code
        except requests.RequestException as error:
            print(f"  {label:10s} could not reach the CDN: {error}")
            return None

        print(f"  {label:10s} HTTP {status}  "
              f"{'ACCEPTED' if status < 400 else 'refused'}")
        return status

    probe("unsigned", unsigned)

    if not key:
        print(
            "\nSet BUNNY_STREAM_TOKEN_AUTH_KEY to test the signed modes.\n"
            + _WHERE_THE_KEY_IS
        )
        return 1

    accepted = [
        mode for mode in TOKEN_MODES
        if (probe(mode, sign_url(unsigned, algorithm=mode)) or 599) < 400
    ]

    print()

    if accepted:
        print(f"Working mode: {accepted[0]}")
        print(f"Set BUNNY_TOKEN_AUTH_ALGORITHM={accepted[0]}")
        return 0

    print(
        "Neither mode was accepted, so the key is almost certainly the wrong "
        "one — signing is only two formulas and both were tried.\n"
        + _WHERE_THE_KEY_IS
        + "\nIf the key is definitely the pull zone's and both modes are still "
        "refused, the remaining possibility is that Block Direct URL File "
        "Access refuses a request with no Referer even when its token is "
        "valid. Bunny does not document either way; their support can confirm "
        "it for this library in one question."
    )

    return 1


_WHERE_THE_KEY_IS = """
The key that signs direct file URLs is the pull zone's:

    CDN  >  the pull zone for this Stream library  >  Security
         >  Token Authentication  >  URL Token Authentication Key

NOT the one on the Stream library's own Security page. That one is the embed
view token key, it signs iframe.mediadelivery.net URLs as
SHA256_HEX(key + video_id + expires), and a direct file URL signed with it is
refused exactly like an unsigned one.
"""


def _run(args):

    if args.command == "check-token-auth":

        return check_token_auth(args.guid)

    if args.command == "show":

        video = get_video(args.guid)

        print(f"title       {video.get('title')}")
        print(f"guid        {video.get('guid')}")
        print(f"status      {status_name(video)} ({video.get('status')})")
        print(f"length      {_format_length(video.get('length'))}")
        print(f"resolutions {resolutions(video) or '—'}")
        print(f"mp4         {bool(video.get('hasMP4Fallback'))}")

        if is_finished(video):
            print(f"audio from  {audio_source_url(video)}")
        else:
            print("audio from  — not encoded yet")

        return

    rows = 0

    for video in iter_videos(search=args.search):

        if args.unfinished and is_finished(video):
            continue

        rows += 1

        print(
            f"{video.get('guid')}  {status_name(video):<12} "
            f"{_format_length(video.get('length')):>8}  "
            f"{(video.get('title') or '')[:48]}"
        )

    print(f"\n{rows} video(s)")


if __name__ == "__main__":
    main()
