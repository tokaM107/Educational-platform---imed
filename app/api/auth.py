"""Sign in, refresh, sign out, and "who am I".

**This API is the session owner.** The browser talks only to FastAPI and never
to Supabase directly, so there is exactly one place a session is created and one
place it is ended. The alternative — the page holding its own supabase-js
session while the API held another — is two session systems that can disagree
about whether somebody is logged in, and the frontend here has no build step to
hang a client library on anyway.

What that costs is that refresh is ours to arrange, hence /refresh below.
Supabase still owns everything that makes a session real: password hashing,
credential checks, token issuance and expiry, refresh-token rotation, email
verification and recovery. Nothing in this file hashes, compares or signs
anything.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import get_conn, get_current_user
from app.services import rate_limit
from app.services.supabase_client import admin_client, fresh_client, supabase


log = logging.getLogger(__name__)

# Supabase's own default minimum is 6. Asking for 8 here is a deliberate step up
# and the number the page shows; raise the project setting to match so the two
# cannot disagree about what is acceptable.
MIN_PASSWORD_LENGTH = 8


router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"]
)


LINKED_USER_SQL = """
    SELECT id, name, email, role
    FROM users
    WHERE auth_user_id = %s
"""


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    access_token: str | None = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=10)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


def client_ip(request: Request):
    """Best effort. Behind a proxy this needs the proxy's forwarded header,
    and trusting that header without a proxy in front lets a caller pick their
    own bucket — so it is read only when one is actually configured."""

    return request.client.host if request.client else "unknown"


def limit(key, limit_count, window, detail):

    try:
        rate_limit.check(key, limit_count, window)
    except rate_limit.RateLimited as limited:
        raise HTTPException(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(limited.retry_after)},
        )


def _session_payload(session, user=None):

    payload = {
        "access_token": session.access_token,
        # Sent so the browser can renew without asking for the password again.
        # The access token is short-lived by design; without this a student is
        # thrown out mid-lecture when it expires.
        "refresh_token": session.refresh_token,
        "token_type": "bearer",
        "expires_at": session.expires_at,
    }

    if user is not None:
        payload["user"] = {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "role": user[3],
        }

    return payload


@router.post("/login")
def login(request: Request, data: LoginRequest, conn=Depends(get_conn)):
    """Exchange email and password for a Supabase session.

    The password goes to Supabase and no further: it is not read, stored or
    compared here.
    """

    # Two buckets, because they stop different things. Per address slows a
    # dictionary attack on one account; per caller stops one machine working
    # through a list of addresses, which the per-address budget alone would
    # happily allow.
    limit(f"login:{data.email.lower()}", 8, 300,
          "Too many sign-in attempts for this account. Wait a few minutes.")
    limit(f"login-ip:{client_ip(request)}", 30, 300,
          "Too many sign-in attempts. Wait a few minutes.")

    try:
        response = supabase.auth.sign_in_with_password({
            "email": data.email,
            "password": data.password,
        })
    except Exception:
        # Deliberately one message for a wrong password and an unknown address.
        # Saying which would turn this into a way to find out who has an
        # account here.
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    if response.session is None or response.user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    with conn.cursor() as cur:
        cur.execute(LINKED_USER_SQL, (str(response.user.id),))
        user = cur.fetchone()

    # Authenticated with Supabase, but no application account behind it. 403
    # rather than 401: the credentials were right, and repeating them will not
    # help — somebody has to link the account.
    if user is None:
        raise HTTPException(
            status_code=403,
            detail="User is not linked to application",
        )

    return _session_payload(response.session, user)


@router.post("/refresh")
def refresh(data: RefreshRequest, conn=Depends(get_conn)):
    """Trade a refresh token for a new access token.

    Called by the page when a request comes back 401, so a session outlives the
    access token's short life without a second login. Supabase rotates the
    refresh token as part of this, which is why the new one is returned and has
    to replace the stored one.
    """

    try:
        response = supabase.auth.refresh_session(data.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Could not refresh session")

    if response.session is None or response.user is None:
        raise HTTPException(status_code=401, detail="Could not refresh session")

    with conn.cursor() as cur:
        cur.execute(LINKED_USER_SQL, (str(response.user.id),))
        user = cur.fetchone()

    if user is None:
        raise HTTPException(
            status_code=403,
            detail="User is not linked to application",
        )

    return _session_payload(response.session, user)


@router.post("/logout")
def logout(data: LogoutRequest, current_user=Depends(get_current_user)):
    """End the session on the server, not just in the browser.

    Discarding the tokens in the page would already log the user out of the UI,
    but the refresh token would stay valid until it expired — so a copy taken
    from storage would still be worth something. This revokes it.

    Reported as success either way. The page has thrown its tokens away by the
    time it hears back, and a logout that appears to fail invites the user to
    stay on a screen they believe is still signed in.
    """

    token = data.access_token

    if token:
        try:
            admin_client().auth.admin.sign_out(token)
        except Exception:
            pass

    return {"logged_out": True}


@router.get("/me")
def me(current_user=Depends(get_current_user)):
    """The application user behind the presented token.

    The whole identity chain in one response: an accepted issuer verified the
    token, its source selected the UUID or integer lookup, and this is the
    public.users row every domain table has always keyed on.
    """

    return current_user


# -------------------------
# Password recovery
# -------------------------
#
# Supabase owns this: it generates the code, mails it, decides how long it lives
# and checks it. Nothing here stores a code — there is no reset-codes table and
# there should not be one, because a second store of a second secret is a second
# thing to leak.
#
# The mail carries a six-digit code rather than a link because the Supabase
# project's "Reset Password" template renders {{ .Token }}. A template left on
# the default {{ .ConfirmationURL }} sends a link instead and the code box below
# will have nothing to type into it.


@router.post("/password/forgot")
def forgot_password(request: Request, data: ForgotPasswordRequest):
    """Send a recovery code to an email address, if it has an account.

    Always answers the same way. Whether a given address is registered is not
    something a stranger gets to find out by watching this endpoint's replies —
    for a platform where the addresses are students at one school, "does this
    person study here" is itself worth protecting.
    """

    limit(f"forgot:{data.email.lower()}", 3, 900,
          "A code was already sent. Check your inbox, or wait a few minutes.")
    limit(f"forgot-ip:{client_ip(request)}", 10, 900,
          "Too many requests. Wait a few minutes.")

    try:
        supabase.auth.reset_password_for_email(data.email)

    except Exception as error:
        # Logged, not returned. A failure here is usually the mail provider, and
        # reporting it back would distinguish a real address from an unknown one
        # exactly as clearly as saying so outright.
        log.warning("password recovery for %s failed: %s", data.email, error)

    return {
        "sent": True,
        "message": "لو الإيميل ده مسجّل عندنا، هيوصله كود خلال دقيقة.",
    }


@router.post("/password/reset")
def reset_password(request: Request, data: ResetPasswordRequest):
    """Check the emailed code and set a new password.

    Verifying the code is what proves the person asking reads that inbox, so it
    is the whole of the authentication here — hence the tight budget on guesses:
    six digits is a million combinations, which is a lot for a human and an
    afternoon for a script.

    The password is then set through the admin API rather than through the
    session the code just produced. Same outcome, but it does not depend on
    which session a shared client happens to be holding — and it leaves the
    recovery session unused rather than handing it back as a way in.
    """

    limit(f"reset:{data.email.lower()}", 6, 900,
          "Too many attempts. Ask for a new code.")
    limit(f"reset-ip:{client_ip(request)}", 15, 900,
          "Too many attempts. Wait a few minutes.")

    try:
        verified = fresh_client().auth.verify_otp({
            "email": data.email,
            "token": data.code.strip(),
            "type": "recovery",
        })
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="الكود غلط أو انتهت صلاحيته. اطلب كود جديد.",
        )

    if verified.user is None:
        raise HTTPException(
            status_code=400,
            detail="الكود غلط أو انتهت صلاحيته. اطلب كود جديد.",
        )

    try:
        admin_client().auth.admin.update_user_by_id(
            str(verified.user.id), {"password": data.new_password}
        )
    except Exception as error:
        log.error("could not set new password for %s: %s", verified.user.id, error)
        raise HTTPException(
            status_code=500,
            detail="مش قادرين نحفظ كلمة المرور الجديدة. جرّب تاني.",
        )

    # Every other session for this user is dropped. Whoever prompted the reset
    # may have been locked out by somebody already signed in as them, and
    # leaving those sessions alive would make the new password beside the point.
    try:
        admin_client().auth.admin.sign_out(
            verified.session.access_token, "global"
        )
    except Exception:
        pass

    return {"reset": True, "message": "تم تغيير كلمة المرور. سجّل دخولك دلوقتي."}
