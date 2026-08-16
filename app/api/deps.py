"""Shared FastAPI dependencies."""

from functools import lru_cache

from app.db import connection
from app.services.tutor import TutorService


def get_conn():
    """A pooled database connection for the duration of one request."""

    with connection() as conn:
        yield conn


@lru_cache
def get_tutor():
    """One TutorService for the process.

    Built on first use rather than at import time, so the app still boots (and
    /health still answers) when GEMINI_API_KEY is missing.
    """

    return TutorService()
