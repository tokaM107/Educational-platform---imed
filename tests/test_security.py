"""Token verification accepts exactly the two platform token contracts."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.services import security
from app.services.security import InvalidToken, VerifiedIdentity


SUB = "69505d75-cff0-4a87-8520-44c5af38e9f4"
NEST_SECRET = "nest-test-access-secret-at-least-32-characters-and-long-enough"
SUPABASE_TOKEN = "eyJhbGciOiJFUzI1NiJ9.e30.c2lnbmF0dXJl"


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


def nest_token(*, secret=NEST_SECRET, algorithm="HS256", **overrides):

    payload = {
        "sub": "2",
        "email": "student@example.com",
        "role": "student",
        "aud": "user",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    payload.update(overrides)

    return jwt.encode(payload, secret, algorithm=algorithm)


@pytest.fixture(autouse=True)
def nest_secret(monkeypatch):

    monkeypatch.setattr(
        security.get_settings(), "nest_jwt_access_secret", NEST_SECRET
    )


@pytest.fixture
def verifier(monkeypatch):
    """Replace Supabase's JWKS verifier with something the test controls."""

    def install(result):

        def get_claims(token):
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(security.supabase.auth, "get_claims", get_claims)

    return install


def test_a_verified_supabase_token_returns_a_source_aware_identity(verifier):

    verifier(claims())

    assert security.decode_access_token(SUPABASE_TOKEN) == VerifiedIdentity(
        source="supabase", subject=SUB
    )


def test_a_supabase_token_from_another_project_is_refused(verifier):

    verifier(claims(iss="https://someone-elses-project.supabase.co/auth/v1"))

    with pytest.raises(InvalidToken):
        security.decode_access_token(SUPABASE_TOKEN)


def test_the_supabase_issuer_check_tolerates_a_trailing_slash(verifier):

    verifier(claims(iss=f"{security.SUPABASE_URL.rstrip('/')}/auth/v1/"))

    assert security.decode_access_token(SUPABASE_TOKEN).subject == SUB


@pytest.mark.parametrize("role", ["student", "doctor"])
def test_a_valid_nest_user_token_returns_its_integer_identity(role):

    assert security.decode_access_token(nest_token(role=role)) == VerifiedIdentity(
        source="nest", subject=2, role=role
    )


def test_a_nest_token_signed_with_the_wrong_secret_is_refused():

    with pytest.raises(InvalidToken):
        security.decode_access_token(
            nest_token(secret="another-secret-that-is-at-least-32-characters")
        )


@pytest.mark.parametrize("secret", ["", "too-short"])
def test_the_nest_secret_is_required_and_at_least_32_characters(
    monkeypatch, secret
):

    monkeypatch.setattr(security.get_settings(), "nest_jwt_access_secret", secret)

    with pytest.raises(RuntimeError):
        security.get_settings().require_nest_jwt_access_secret()


def test_an_expired_nest_token_is_refused():

    with pytest.raises(InvalidToken):
        security.decode_access_token(
            nest_token(exp=datetime.now(timezone.utc) - timedelta(seconds=1))
        )


@pytest.mark.parametrize("missing", ["sub", "exp", "aud", "role", "email"])
def test_a_nest_token_missing_a_required_claim_is_refused(missing):

    payload = {
        "sub": "2",
        "email": "student@example.com",
        "role": "student",
        "aud": "user",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    del payload[missing]
    token = jwt.encode(payload, NEST_SECRET, algorithm="HS256")

    with pytest.raises(InvalidToken):
        security.decode_access_token(token)


def test_an_unsigned_token_is_refused():

    token = jwt.encode(
        {"sub": "2", "aud": "user", "role": "student", "email": "s@e.com"},
        key="",
        algorithm="none",
    )

    with pytest.raises(InvalidToken):
        security.decode_access_token(token)


def test_a_nest_token_using_another_algorithm_is_refused():

    with pytest.raises(InvalidToken):
        security.decode_access_token(nest_token(algorithm="HS384"))


@pytest.mark.parametrize("audience", ["admin", ["user", "admin"]])
def test_a_nest_admin_audience_is_refused(audience):

    with pytest.raises(InvalidToken):
        security.decode_access_token(nest_token(aud=audience))


@pytest.mark.parametrize("subject", ["not-a-number", "0", "-1", "02", 1.5, True])
def test_a_nest_subject_must_be_a_canonical_positive_integer(subject):

    with pytest.raises(InvalidToken):
        security.decode_access_token(nest_token(sub=subject))


@pytest.mark.parametrize("role", ["admin", "authenticated", "", None])
def test_a_nest_role_must_be_a_supported_application_role(role):

    with pytest.raises(InvalidToken):
        security.decode_access_token(nest_token(role=role))


def test_a_nest_email_must_be_nonempty_text():

    with pytest.raises(InvalidToken):
        security.decode_access_token(nest_token(email=""))


def test_a_rejected_supabase_token_raises_invalid_token(verifier):

    verifier(Exception("signature verification failed"))

    with pytest.raises(InvalidToken):
        security.decode_access_token(SUPABASE_TOKEN)


def test_an_empty_token_is_refused_without_asking_supabase(verifier):

    verifier(Exception("should not have been called"))

    with pytest.raises(InvalidToken):
        security.decode_access_token("")


def test_a_supabase_token_with_no_subject_is_refused(verifier):

    verifier(claims(sub=None))

    with pytest.raises(InvalidToken):
        security.decode_access_token(SUPABASE_TOKEN)


def test_an_empty_supabase_claims_body_is_refused(verifier):

    verifier({"claims": {}, "headers": {}, "signature": b""})

    with pytest.raises(InvalidToken):
        security.decode_access_token(SUPABASE_TOKEN)


def test_nothing_here_hashes_passwords_or_mints_tokens():

    for gone in (
        "hash_password",
        "verify_password",
        "create_access_token",
        "BCRYPT_ROUNDS",
    ):
        assert not hasattr(security, gone), f"{gone} came back"
