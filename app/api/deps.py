"""Shared FastAPI dependencies."""

from functools import lru_cache

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import connection
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
    """The application user a Supabase access token resolves to.

    Three things have to hold, and each failure is a 401 because from the
    caller's side they are one situation — they are not logged in:

        the token verifies      signature, expiry, issued by our project
        it names a subject      the Supabase auth.users UUID, in `sub`
        that subject is linked  a public.users row carries it in auth_user_id

    The last one is not a formality. A Supabase account can exist with no
    application user behind it — someone signed up but was never provisioned —
    and such a request must not be allowed to continue as "some user".

    The role is read from public.users here, never from the token. Supabase puts
    its own `role` claim in the JWT and it says "authenticated", meaning a
    Postgres role; taking that as the application role would make every user a
    stranger to the permission checks that follow.
    """

    try:
        claims = decode_access_token(token)
    except InvalidToken:
        raise _unauthenticated("Invalid or expired token")

    auth_user_id = claims.get("sub")

    if not auth_user_id:
        raise _unauthenticated("Token carries no subject")

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE auth_user_id = %s",
            (str(auth_user_id),),
        )
        row = cur.fetchone()

    if row is None:
        raise _unauthenticated("User is not linked to an application account")

    return {
        "id": row[0],
        "name": row[1],
        "email": row[2],
        "role": row[3],
        "auth_user_id": str(row[4]),
    }


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    conn=Depends(get_conn),
):
    """The authenticated user, from `Authorization: Bearer <supabase jwt>`.

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
        description="Supabase access token, for requests a browser cannot put "
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

    It is the same Supabase token, verified by the same code — this widens how
    the token arrives, never what counts as a valid one. The cost is that URLs
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


@lru_cache
def get_tutor():
    """One TutorService for the process.

    Built on first use rather than at import time, so the app still boots (and
    /health still answers) when GEMINI_API_KEY is missing.
    """

    return TutorService()
