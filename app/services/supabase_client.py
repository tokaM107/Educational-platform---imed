"""Supabase clients.

Two of them, and the difference matters:

    supabase        the publishable key. Safe in a browser, and used here for
                    signing in and for verifying tokens.

    admin_client()  the secret key. Full authority over every user in the
                    project, bypassing every policy. Server-side only — it must
                    never be sent to a browser, embedded in a page, or logged.

The admin client is built on demand rather than at import, so the application
still boots when the secret key is absent, and only the one endpoint that needs
it fails.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from supabase import Client, create_client

from app.config import BASE_DIR


load_dotenv(BASE_DIR / ".env", override=True)


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_PUBLISHABLE_KEY = os.environ["SUPABASE_PUBLISHABLE_KEY"]


# One client for the process. `sign_in_with_password` stores the resulting
# session inside it, which would be a problem if anything here read that stored
# session — two simultaneous logins would overwrite each other. Nothing does:
# every call takes the token it operates on as an argument, and login reads the
# token off the response object it was handed.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)


@lru_cache
def admin_client() -> Client:
    """A client holding the service-role key. Never expose this to a browser."""

    secret = os.getenv("SUPABASE_SECRET_KEY", "").strip()

    if not secret:
        raise RuntimeError("SUPABASE_SECRET_KEY is not set (see .env)")

    return create_client(SUPABASE_URL, secret)


def fresh_client() -> Client:
    """A throwaway publishable client with no session of its own.

    For calls that authenticate somebody and thereby *store* a session on the
    client that made them — `verify_otp` is one. Doing that on the shared client
    above would leave it holding whichever user most recently proved an OTP,
    which two simultaneous password resets would race over.

    Nothing reads the shared client's stored session today, so this is cheap
    insurance rather than a fix for a live bug. It costs a new connection pool,
    which is why it is only used on the rare paths that need it.
    """

    return create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
