"""Verifying access tokens issued by Supabase or the platform's Nest API.

Supabase Auth still owns its credentials and sessions. Nest also issues access
tokens for the main application. This module decides whether either token was
issued by the expected authority; it never mints a token and never sees a
password.

`supabase.auth.get_claims` does the verification. Given this project's ES256
signing keys it works entirely locally: it reads the `kid` from the token
header, fetches the project's public keys from the JWKS endpoint once, caches
them, and checks the signature against the cached key. So this costs no network
round trip per request. (Were the project on a legacy HS256 shared secret, the
same call would fall back to asking the Auth server about every token — worth
knowing if verification ever suddenly gets slow.)

Nest tokens are verified locally with the same server-only HS256 user-token
secret used by Nest. Their algorithm and ``user`` audience are pinned here;
admin tokens and tokens signed with another algorithm are never candidates.

What comes back is the token's claims, and it is worth being clear about one of
them: Supabase's `role` claim holds a *Postgres* role, normally "authenticated".
It is not this application's student/doctor role, which lives in public.users
and is read from there. Treating the token's role as the application's would
hand every logged-in user the same permissions.
"""

from dataclasses import dataclass
from typing import Literal

import jwt

from app.config import get_settings
from app.services.supabase_client import SUPABASE_URL, supabase


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """The trusted identity selected by one successful token verifier."""

    source: Literal["supabase", "nest"]
    subject: str | int
    role: str | None = None


class InvalidToken(Exception):
    """A token that was malformed, expired, or not signed by our project."""


def decode_access_token(token: str) -> VerifiedIdentity:
    """A verified Supabase or Nest identity, or ``InvalidToken``.

    Every failure collapses into one exception on purpose. Expired, forged and
    malformed are different to us and identical to the caller — they are not
    logged in — and telling them apart in a response only helps someone probing
    which of their guesses was closest.

    That deliberately includes the case where the JWKS endpoint cannot be
    reached: a token we are unable to verify is one we must not accept, so the
    failure mode is refusal rather than admission.
    """

    if not token:
        raise InvalidToken("Invalid token")

    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
    except Exception as exc:
        raise InvalidToken("Invalid token") from exc

    if algorithm == "HS256":
        return _decode_nest_access_token(token)

    if algorithm == "ES256":
        return _decode_supabase_access_token(token)

    raise InvalidToken("Invalid token")


def _decode_nest_access_token(token: str) -> VerifiedIdentity:
    """Verify the fixed contract used by Nest's user access tokens."""

    try:
        claims = jwt.decode(
            token,
            get_settings().require_nest_jwt_access_secret(),
            algorithms=["HS256"],
            audience="user",
            options={"require": ["sub", "exp", "aud", "role", "email"]},
        )

        raw_subject = claims.get("sub")
        if isinstance(raw_subject, bool) or not isinstance(raw_subject, (str, int)):
            raise InvalidToken("Invalid token")

        subject = int(raw_subject)
        if subject <= 0 or str(subject) != str(raw_subject):
            raise InvalidToken("Invalid token")

        role = claims.get("role")
        if role not in ("student", "doctor"):
            raise InvalidToken("Invalid token")

        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            raise InvalidToken("Invalid token")

        if claims.get("aud") != "user":
            raise InvalidToken("Invalid token")

        return VerifiedIdentity(source="nest", subject=subject, role=role)

    except InvalidToken:
        raise

    except Exception as exc:
        raise InvalidToken("Invalid token") from exc


def _decode_supabase_access_token(token: str) -> VerifiedIdentity:
    """Verify an ES256 Supabase token against the project's JWKS."""

    try:
        # ClaimsResponse is a TypedDict, so this is a plain dict at runtime and
        # `response.claims` would raise AttributeError — caught below and
        # reported as an invalid token, which is how a working signature can
        # still fail to let anybody in.
        response = supabase.auth.get_claims(token)
        claims = (response or {}).get("claims") or {}

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidToken("Token carries no subject")

        _check_issuer(claims)

        return VerifiedIdentity(source="supabase", subject=subject)

    except InvalidToken:
        raise

    except Exception as exc:
        raise InvalidToken("Invalid token") from exc


def _check_issuer(claims):
    """Reject a well-formed token minted by somebody else's project.

    Verification already fetches signing keys from our own project's JWKS, so a
    foreign token fails on its key id long before this. The check is here for
    the case that reasoning stops holding — a misconfigured URL, a key set
    served from somewhere unexpected — where the difference between "signed" and
    "signed by us" is the whole of the security.
    """

    issuer = claims.get("iss")
    expected = f"{SUPABASE_URL.rstrip('/')}/auth/v1"

    if issuer and issuer.rstrip("/") != expected:
        raise InvalidToken("Token issued by a different project")
