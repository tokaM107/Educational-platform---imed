"""Password recovery, and the rate limits around it.

Supabase is stubbed throughout. What is worth testing is not that Supabase can
check a one-time code — it can — but the decisions this application makes
around that: that it never reveals which addresses are registered, that a wrong
code cannot be ground down by repetition, and that a successful reset does not
leave the old sessions alive.
"""

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import app
from app.services import rate_limit


EMAIL = "student@example.com"
USER_ID = "69505d75-cff0-4a87-8520-44c5af38e9f4"
GOOD_CODE = "123456"
NEW_PASSWORD = "a-long-enough-password"


class FakeUser:
    id = USER_ID


class FakeSession:
    access_token = "recovery-session-token"


class FakeVerified:
    user = FakeUser()
    session = FakeSession()


class FakeAdminAuth:
    """Records what the admin API was asked to do."""

    def __init__(self, log):
        self.admin = self
        self._log = log

    def update_user_by_id(self, uid, attributes):
        self._log.append(("update", uid, attributes))

    def sign_out(self, token, scope=None):
        self._log.append(("sign_out", token, scope))


class FakeAdminClient:
    def __init__(self, log):
        self.auth = FakeAdminAuth(log)


@pytest.fixture(autouse=True)
def clean_limits():
    """Budgets are process-wide, so one test's attempts would spend another's."""

    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def supabase(monkeypatch):
    """Stub the recovery calls. Returns the log of what was attempted."""

    log = []

    class FakeAuth:
        def reset_password_for_email(self, email, options=None):
            log.append(("email", email))

        def verify_otp(self, params):
            log.append(("verify", params["email"], params["token"]))
            if params["token"] != GOOD_CODE:
                raise Exception("Token has expired or is invalid")
            return FakeVerified()

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_api, "supabase", FakeClient())
    monkeypatch.setattr(auth_api, "fresh_client", lambda: FakeClient())
    monkeypatch.setattr(auth_api, "admin_client", lambda: FakeAdminClient(log))

    return log


# -------------------------
# Asking for a code
# -------------------------


def test_asking_for_a_code_sends_one(client, supabase):

    response = client.post("/api/auth/password/forgot", json={"email": EMAIL})

    assert response.status_code == 200
    assert ("email", EMAIL) in supabase


def test_an_unknown_address_is_answered_identically(client, supabase, monkeypatch):
    """No user enumeration.

    Whether somebody has an account must not be readable off this endpoint. On
    a platform whose users are all students at one school, "does this person
    study here" is itself worth not leaking.
    """

    known = client.post("/api/auth/password/forgot", json={"email": EMAIL})

    rate_limit.reset()

    def refuse(email, options=None):
        raise Exception("User not found")

    monkeypatch.setattr(auth_api.supabase.auth, "reset_password_for_email", refuse)

    unknown = client.post(
        "/api/auth/password/forgot", json={"email": "nobody@example.com"}
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_a_provider_failure_is_not_reported_to_the_caller(
    client, supabase, monkeypatch
):
    """A 500 here would distinguish a real address just as well as a message."""

    def explode(email, options=None):
        raise Exception("SMTP is down")

    monkeypatch.setattr(auth_api.supabase.auth, "reset_password_for_email", explode)

    assert client.post(
        "/api/auth/password/forgot", json={"email": EMAIL}
    ).status_code == 200


def test_requests_for_one_address_are_rate_limited(client, supabase):
    """Otherwise the form is a way to send somebody a great deal of mail."""

    codes = [
        client.post("/api/auth/password/forgot", json={"email": EMAIL}).status_code
        for _ in range(5)
    ]

    assert codes[0] == 200
    assert 429 in codes


def test_a_rate_limited_reply_says_when_to_come_back(client, supabase):

    for _ in range(6):
        response = client.post("/api/auth/password/forgot", json={"email": EMAIL})

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_a_malformed_address_is_refused_before_anything_is_sent(client, supabase):

    assert client.post(
        "/api/auth/password/forgot", json={"email": "not-an-email"}
    ).status_code == 422
    assert supabase == []


# -------------------------
# Using the code
# -------------------------


def test_the_right_code_sets_the_new_password(client, supabase):

    response = client.post(
        "/api/auth/password/reset",
        json={"email": EMAIL, "code": GOOD_CODE, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 200
    assert ("update", USER_ID, {"password": NEW_PASSWORD}) in supabase


def test_a_successful_reset_ends_every_other_session(client, supabase):
    """Whoever forced the reset may be signed in as them right now.

    Leaving those sessions alive would make the new password beside the point.
    """

    client.post(
        "/api/auth/password/reset",
        json={"email": EMAIL, "code": GOOD_CODE, "new_password": NEW_PASSWORD},
    )

    assert any(
        entry[0] == "sign_out" and entry[2] == "global" for entry in supabase
    )


def test_a_wrong_code_is_refused(client, supabase):

    response = client.post(
        "/api/auth/password/reset",
        json={"email": EMAIL, "code": "000000", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400
    assert not any(entry[0] == "update" for entry in supabase)


def test_guessing_the_code_is_rate_limited(client, supabase):
    """Six digits is a million combinations — an afternoon for a script."""

    codes = [
        client.post(
            "/api/auth/password/reset",
            json={"email": EMAIL, "code": "000000", "new_password": NEW_PASSWORD},
        ).status_code
        for _ in range(9)
    ]

    assert 429 in codes
    assert codes.count(400) <= 6


def test_a_short_password_is_refused_before_the_code_is_spent(client, supabase):
    """A single-use code must not be burned on a password we would reject."""

    response = client.post(
        "/api/auth/password/reset",
        json={"email": EMAIL, "code": GOOD_CODE, "new_password": "short"},
    )

    assert response.status_code == 422
    assert not any(entry[0] == "verify" for entry in supabase)


def test_the_reset_routes_need_no_authentication(client, supabase):
    """The whole point is being unable to log in."""

    for path, body in [
        ("/api/auth/password/forgot", {"email": EMAIL}),
        (
            "/api/auth/password/reset",
            {"email": EMAIL, "code": GOOD_CODE, "new_password": NEW_PASSWORD},
        ),
    ]:
        assert client.post(path, json=body).status_code != 401


# -------------------------
# The limiter itself
# -------------------------


def test_the_limiter_allows_up_to_the_budget_then_refuses():

    for _ in range(3):
        rate_limit.check("k", 3, 60)

    with pytest.raises(rate_limit.RateLimited):
        rate_limit.check("k", 3, 60)


def test_the_limiter_keeps_separate_budgets_per_key():

    rate_limit.check("a", 1, 60)
    rate_limit.check("b", 1, 60)          # must not be refused

    with pytest.raises(rate_limit.RateLimited):
        rate_limit.check("a", 1, 60)


def test_the_limiter_forgets_attempts_once_the_window_passes():

    rate_limit.check("k", 1, 60)

    with pytest.raises(rate_limit.RateLimited):
        rate_limit.check("k", 1, 60)

    # A zero-length window is every window, one instant later.
    rate_limit.check("k", 1, 0)


def test_signing_in_is_rate_limited(client, monkeypatch):
    """Every attempt reaches Supabase from this one server, so its per-IP limit
    sees the whole user base as one client. The distinction only exists here."""

    class Refusing:
        class auth:
            @staticmethod
            def sign_in_with_password(credentials):
                raise Exception("Invalid login credentials")

    monkeypatch.setattr(auth_api, "supabase", Refusing())

    codes = [
        client.post(
            "/api/auth/login", json={"email": EMAIL, "password": "guess"}
        ).status_code
        for _ in range(12)
    ]

    assert 401 in codes
    assert 429 in codes
