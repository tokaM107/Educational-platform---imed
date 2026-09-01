"""Shared FastAPI dependencies."""

from functools import lru_cache

from fastapi import Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import Error as DatabaseError

from app.config import get_settings
from app.db import connection
from app.services import llm_quota
from app.services.security import InvalidToken, decode_access_token
from app.services.tutor import TutorService


def get_conn():
    """A pooled database connection for the duration of one request."""

    with connection() as conn:
        yield conn


# auto_error=False on purpose. Left to itself HTTPBearer answers a missing
# Authorization header with 403, which says "you may not do this" when the true
# answer is "you have not said who you are". The difference matters to the
# browser: 401 sends it to the login page, 403 tells it logging in would not
# help. Handing back None lets the check below raise the right one.
bearer = HTTPBearer(auto_error=False)


# Selected by name rather than *: the row is turned into a dict positionally,
# and a column added to the table later would otherwise shift every field.
_USER_COLUMNS = "id, name, email, role, auth_user_id"


def _unauthenticated(detail):

    # The header tells a browser this endpoint takes a bearer token; without it
    # a 401 is just a failure, with it it is an invitation to log in.
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _user_for_token(conn, token):
    """The application user a verified Supabase or Nest token resolves to.

    Three things have to hold, and each failure is a 401 because from the
    caller's side they are one situation — they are not logged in:

        the token verifies      signature, expiry, audience and issuer/secret
        it names a subject      a Supabase UUID or Nest integer user id
        that subject is linked  to the corresponding public.users row

    The last one is not a formality. A Supabase account can exist with no
    application user behind it — someone signed up but was never provisioned —
    and such a request must not be allowed to continue as "some user".

    The effective role is always read from public.users. Nest's signed role is
    additionally required to match it, so a token issued before a role change
    cannot retain stale privileges.
    """

    try:
        identity = decode_access_token(token)
    except InvalidToken:
        raise _unauthenticated("Invalid or expired token")

    with conn.cursor() as cur:
        if identity.source == "supabase":
            cur.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE auth_user_id = %s",
                (identity.subject,),
            )
        else:
            cur.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s",
                (identity.subject,),
            )
        row = cur.fetchone()

    if row is None:
        raise _unauthenticated("User is not linked to an application account")

    if identity.source == "nest" and row[3] != identity.role:
        raise _unauthenticated("Invalid or expired token")

    return {
        "id": row[0],
        "name": row[1],
        "email": row[2],
        "role": row[3],
        "auth_user_id": str(row[4]) if row[4] is not None else None,
    }


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    conn=Depends(get_conn),
):
    """The authenticated user from an accepted bearer access token.

    This is the only thing in the application allowed to answer "who is asking".
    An id in a query string or a request body is a claim the caller typed, and
    the difference between the two is the whole of the authorization model.
    """

    if credentials is None or not credentials.credentials:
        raise _unauthenticated("Not authenticated")

    return _user_for_token(conn, credentials.credentials)


def get_current_user_streaming(
    access_token: str | None = Query(
        None,
        description="Access token, for requests a browser cannot put "
                    "a header on (a <video> element). Same token, same checks.",
    ),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    conn=Depends(get_conn),
):
    """`get_current_user`, plus the one transport a <video> tag can manage.

    An HTML media element fetches its own source and there is no way to add an
    Authorization header to that request, so a streaming endpoint guarded by the
    header alone cannot be played at all. The token therefore travels in the
    query string here, and only here.

    It is the same access token, verified by the same code — this widens how the
    token arrives, never what counts as a valid one. The cost is that URLs
    are quotable in a way headers are not: this one can land in server logs or a
    Referer header, so it stays confined to the video route rather than becoming
    a general way to authenticate.
    """

    token = credentials.credentials if credentials else access_token

    if not token:
        raise _unauthenticated("Not authenticated")

    return _user_for_token(conn, token)


def require_role(*roles):
    """A dependency that admits only the named application roles.

    403 rather than 401: the caller proved who they are and the answer is still
    no, so logging in again would change nothing.
    """

    def dependency(current_user=Depends(get_current_user)):

        if current_user["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of: {', '.join(roles)}",
            )

        return current_user

    return dependency


# public.users.role is constrained to exactly these two (db/schema.sql), so
# these are the only helpers that can ever match. No admin role exists; add one
# to the CHECK constraint first if that changes.
require_student = require_role("student")
require_doctor = require_role("doctor")


def _quota_headers(usage):
    return {
        "X-RateLimit-Limit": str(usage.limit),
        "X-RateLimit-Remaining": str(usage.remaining),
        "X-RateLimit-Reset": str(usage.retry_after),
    }


def consume_llm_quota(response, current_user, conn, feature, *, units=1):
    """Reserve quota and translate the service result into HTTP semantics."""

    try:
        usage = llm_quota.consume(
            conn,
            current_user["id"],
            feature,
            limit=get_settings().llm_daily_query_limit,
            units=units,
        )
    except llm_quota.QuotaExceeded as error:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "daily_llm_limit_reached",
                "message": "Daily AI question limit reached. Try again tomorrow.",
                "limit": error.usage.limit,
                "used": error.usage.used,
            },
            headers={
                **_quota_headers(error.usage),
                "Retry-After": str(error.usage.retry_after),
            },
        ) from error
    except DatabaseError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "llm_quota_unavailable",
                "message": "AI requests are temporarily unavailable.",
            },
        ) from error

    for name, value in _quota_headers(usage).items():
        response.headers[name] = value
    return usage


def llm_quota_dependency(feature, *, units=1):
    """Build a quota dependency layered on the shared authenticated identity."""

    def dependency(
        response: Response,
        current_user=Depends(get_current_user),
        conn=Depends(get_conn),
    ):
        effective_units = (
            get_settings().llm_daily_query_limit if units is None else units
        )
        return consume_llm_quota(
            response, current_user, conn, feature, units=effective_units
        )

    return dependency


chat_llm_quota = llm_quota_dependency("chat")
search_llm_quota = llm_quota_dependency("search")
grading_llm_quota = llm_quota_dependency("grading")
grading_dataset_llm_quota = llm_quota_dependency("grading_dataset", units=None)


@lru_cache
def get_tutor():
    """One TutorService for the process.

    Built on first use rather than at import time, so the app still boots (and
    /health still answers) when GEMINI_API_KEY is missing.
    """

    return TutorService()
