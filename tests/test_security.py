"""Verifying a Supabase access token.

The custom password hashing and JWT minting this file used to cover are gone:
Supabase Auth owns credentials now, and two implementations of that would be one
too many. What is left to test is the decision this application still makes for
itself — whether to believe a token it was handed.

`supabase.auth.get_claims` is stubbed. Calling the real thing would test
Supabase's signature checking, which is not ours, and would need the network.
What is tested is what this module does with each answer that call can give.
"""

import pytest

from app.services import security
from app.services.security import InvalidToken


SUB = "69505d75-cff0-4a87-8520-44c5af38e9f4"


def claims(**overrides):

    payload = {
        "sub": SUB,
        "role": "authenticated",
        "iss": f"{security.SUPABASE_URL.rstrip('/')}/auth/v1",
        "exp": 4102444800,
        "email": "student@example.com",
    }
    payload.update(overrides)

    return {"claims": payload, "headers": {"alg": "ES256"}, "signature": b""}


@pytest.fixture
def verifier(monkeypatch):
    """Replace get_claims with something the test controls."""

    def install(result):

        def get_claims(token):
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(security.supabase.auth, "get_claims", get_claims)

    return install


def test_a_verified_token_returns_its_claims(verifier):

    verifier(claims())

    assert security.decode_access_token("a-token")["sub"] == SUB


def test_the_returned_claims_are_a_plain_dict(verifier):
    """ClaimsResponse is a TypedDict, so this is a dict at runtime.

    Reading it as an object — `response.claims` — raises AttributeError, and
    because everything here fails closed that surfaces as "invalid token" for
    every user rather than as the bug it is. Worth pinning.
    """

    verifier(claims())

    result = security.decode_access_token("a-token")

    assert isinstance(result, dict)
    assert result["email"] == "student@example.com"


def test_a_rejected_token_raises_invalid_token(verifier):
    """Whatever the library raises comes back as one exception of ours."""

    verifier(Exception("signature verification failed"))

    with pytest.raises(InvalidToken):
        security.decode_access_token("forged")


def test_an_expired_token_raises_invalid_token(verifier):

    verifier(Exception("token is expired"))

    with pytest.raises(InvalidToken):
        security.decode_access_token("stale")


def test_an_empty_token_is_refused_without_asking_supabase(verifier):

    verifier(Exception("should not have been called"))

    with pytest.raises(InvalidToken):
        security.decode_access_token("")


def test_a_token_with_no_subject_is_refused(verifier):
    """Verified, but naming nobody — so there is no user to act as."""

    verifier(claims(sub=None))

    with pytest.raises(InvalidToken):
        security.decode_access_token("subjectless")


def test_an_empty_claims_body_is_refused(verifier):

    verifier({"claims": {}, "headers": {}, "signature": b""})

    with pytest.raises(InvalidToken):
        security.decode_access_token("hollow")


def test_a_token_from_another_project_is_refused(verifier):
    """A valid signature from the wrong issuer is still the wrong token."""

    verifier(claims(iss="https://someone-elses-project.supabase.co/auth/v1"))

    with pytest.raises(InvalidToken):
        security.decode_access_token("foreign")


def test_the_issuer_check_tolerates_a_trailing_slash(verifier):

    verifier(claims(iss=f"{security.SUPABASE_URL.rstrip('/')}/auth/v1/"))

    assert security.decode_access_token("a-token")["sub"] == SUB


def test_nothing_here_hashes_or_signs_anything():
    """Supabase owns credentials; a second implementation would be one too many.

    Named directly so that reintroducing any of it fails loudly rather than
    quietly growing a competing login path.
    """

    for gone in (
        "hash_password",
        "verify_password",
        "create_access_token",
        "BCRYPT_ROUNDS",
    ):
        assert not hasattr(security, gone), f"{gone} came back"
