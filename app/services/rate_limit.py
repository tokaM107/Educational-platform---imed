"""A small fixed-budget rate limiter for the endpoints that need one.

Deliberately in-process and dependency-free. That is a real limitation and worth
stating plainly: the budget is per worker, so N uvicorn workers allow N times the
traffic, and a restart forgets everything. For slowing down password guessing and
stopping somebody using the reset form as a way to send mail, that is enough. It
is not enough to be the only thing between the service and a determined attacker
— a shared store (Redis) or a limiter at the proxy is what that needs.

Why it exists at all: login and password reset go to Supabase *from this server*,
so every attempt arrives there from one IP. Supabase's own per-IP limits
therefore see the whole user base as a single client — they cannot tell an
attacker from everybody else, and tripping them would lock out everybody at once.
The distinction between callers only exists here, so the limit has to be here.
"""

import time
from collections import defaultdict
from threading import Lock


_hits = defaultdict(list)
_lock = Lock()

# Bounds the dictionary. Without it a run of unique keys — one per forged email
# address — grows it forever, which is its own denial of service.
_MAX_KEYS = 10_000


class RateLimited(Exception):
    """Too many attempts. Carries the seconds until the next one is allowed."""

    def __init__(self, retry_after):
        super().__init__(f"Rate limited, retry in {retry_after}s")
        self.retry_after = retry_after


def check(key, limit, window_seconds):
    """Record an attempt against `key`, or raise RateLimited.

    A fixed window rather than a token bucket: the failure mode of a fixed
    window is allowing up to twice the limit across a boundary, which for
    "6 password guesses a minute" is a distinction without a difference.
    """

    now = time.monotonic()
    cutoff = now - window_seconds

    with _lock:

        if len(_hits) > _MAX_KEYS:
            _hits.clear()

        recent = [stamp for stamp in _hits[key] if stamp > cutoff]

        if len(recent) >= limit:
            # How long until the oldest attempt in the window falls out of it.
            retry_after = max(1, int(recent[0] + window_seconds - now) + 1)
            _hits[key] = recent
            raise RateLimited(retry_after)

        recent.append(now)
        _hits[key] = recent


def reset():
    """Forget every recorded attempt. For tests."""

    with _lock:
        _hits.clear()
