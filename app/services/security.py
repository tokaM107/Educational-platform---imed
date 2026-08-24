"""Verifying a Supabase access token.

Supabase Auth owns everything about credentials — password hashing, sign-in,
JWT issuance, sessions, refresh, email verification, recovery. This module does
the one thing left to us: decide whether a token presented to *our* API is
genuine. It never mints a token and never sees a password.

`supabase.auth.get_claims` does the verification. Given this project's ES256
signing keys it works entirely locally: it reads the `kid` from the token
header, fetches the project's public keys from the JWKS endpoint once, caches
them, and checks the signature against the cached key. So this costs no network
round trip per request. (Were the project on a legacy HS256 shared secret, the
same call would fall back to asking the Auth server about every token — worth
knowing if verification ever suddenly gets slow.)

Expiry is checked before the signature, by the same call.

What comes back is the token's claims, and it is worth being clear about one of
them: Supabase's `role` claim holds a *Postgres* role, normally "authenticated".
It is not this application's student/doctor role, which lives in public.users
and is read from there. Treating the token's role as the application's would
hand every logged-in user the same permissions.
"""

from app.services.supabase_client import SUPABASE_URL, supabase


class InvalidToken(Exception):
    """A token that was malformed, expired, or not signed by our project."""


def decode_access_token(token: str) -> dict:
    """The verified claims of a Supabase access token, or InvalidToken.

    Every failure collapses into one exception on purpose. Expired, forged and
    malformed are different to us and identical to the caller — they are not
    logged in — and telling them apart in a response only helps someone probing
    which of their guesses was closest.

    That deliberately includes the case where the JWKS endpoint cannot be
    reached: a token we are unable to verify is one we must not accept, so the
    failure mode is refusal rather than admission.
    """

    if not token:
        raise InvalidToken("No token")

    try:
        # ClaimsResponse is a TypedDict, so this is a plain dict at runtime and
        # `response.claims` would raise AttributeError — caught below and
        # reported as an invalid token, which is how a working signature can
        # still fail to let anybody in.
        response = supabase.auth.get_claims(token)
        claims = (response or {}).get("claims") or {}

        if not claims.get("sub"):
            raise InvalidToken("Token carries no subject")

        _check_issuer(claims)

        return claims

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
