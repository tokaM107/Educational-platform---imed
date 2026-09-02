"""Authentication and authorization at the HTTP layer.

Two questions, and they fail differently:

    authentication   who is asking?          missing/bad/unlinked -> 401
    authorization    may they have this?     wrong person/role    -> 403

Token verification is usually stubbed at `app.api.deps.decode_access_token`,
the seam between "an issuer proved this identity" and everything this
application does with that fact. The database is stubbed too
(`tests/fake_db.py`), so the recorded queries can be inspected directly.
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.main import app
from app.services import security
from app.services.security import InvalidToken, VerifiedIdentity
from tests.fake_db import FakeConn


STUDENT = {
    "id": 2,
    "name": "Ahmed",
    "email": "student@example.com",
    "role": "student",
    "auth_user_id": "11111111-1111-1111-1111-111111111111",
}

OTHER_STUDENT_ID = 30

DOCTOR = {
    "id": 1,
    "name": "Dr Selim",
    "email": "doctor@example.com",
    "role": "doctor",
    "auth_user_id": "22222222-2222-2222-2222-222222222222",
}

AUTH = {"Authorization": "Bearer any-token-the-stub-accepts"}
NEST_SECRET = "nest-test-access-secret-at-least-32-characters-and-long-enough"


@pytest.fixture
def client():

    # These are request-layer tests with get_conn replaced by FakeConn. Avoid
    # entering the production lifespan, whose job is to open a real DB pool.
    test_client = TestClient(app)
    app.dependency_overrides[deps.get_tutor] = lambda: object()
    yield test_client
    test_client.close()

    app.dependency_overrides.clear()


@pytest.fixture
def conn():
    """A fake connection, installed for the request."""

    fake = FakeConn()
    app.dependency_overrides[deps.get_conn] = lambda: fake

    yield fake

    app.dependency_overrides.clear()


def as_user(user):
    """Skip token verification: this request is this user."""

    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_current_user_streaming] = lambda: user


# -------------------------
# Resolving a token to an application user
# -------------------------


def test_a_valid_token_resolves_to_the_linked_user(client, conn, monkeypatch):

    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda token: VerifiedIdentity(
            source="supabase", subject=STUDENT["auth_user_id"]
        ),
    )
    conn.answer = lambda sql, params: [
        (2, "Ahmed", "student@example.com", "student", STUDENT["auth_user_id"])
    ]

    response = client.get("/api/auth/me", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == STUDENT

    # Looked up by the token's subject, not by anything the caller sent.
    assert conn.params_for("auth_user_id = %s") == (STUDENT["auth_user_id"],)


def test_a_missing_token_is_401_not_403(client, conn):
    """FastAPI's own HTTPBearer answers 403 here, which sends the wrong signal.

    401 tells the browser to go and log in; 403 tells it not to bother.
    """

    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_an_invalid_token_is_401(client, conn, monkeypatch):

    def reject(token):
        raise InvalidToken("bad signature")

    monkeypatch.setattr(deps, "decode_access_token", reject)

    assert client.get("/api/auth/me", headers=AUTH).status_code == 401


def test_an_expired_token_is_401(client, conn, monkeypatch):

    def expired(token):
        raise InvalidToken("token is expired")

    monkeypatch.setattr(deps, "decode_access_token", expired)

    assert client.get("/api/auth/me", headers=AUTH).status_code == 401


def test_a_verified_identity_with_no_subject_is_401(client, conn, monkeypatch):

    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda token: VerifiedIdentity(source="supabase", subject=""),
    )

    assert client.get("/api/auth/me", headers=AUTH).status_code == 401


def test_a_genuine_supabase_user_with_no_application_row_is_401(
    client, conn, monkeypatch
):
    """Signed up with Supabase, never provisioned here.

    The token is perfectly valid, so it is tempting to let it through as "some
    user". There is no such user: nothing in the domain tables can key off them,
    and every id in this application is the integer one.
    """

    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda token: VerifiedIdentity(source="supabase", subject="unlinked-uuid"),
    )
    conn.answer = lambda sql, params: []

    response = client.get("/api/auth/me", headers=AUTH)

    assert response.status_code == 401
    assert "not linked" in response.json()["detail"].lower()


def test_the_application_role_is_read_from_the_database_not_the_token(
    client, conn, monkeypatch
):
    """Supabase's `role` claim says "authenticated" and means a Postgres role.

    Taking it as the application's role would make every logged-in user the
    same, and none of them a doctor or a student.
    """

    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda token: VerifiedIdentity(
            source="supabase", subject=DOCTOR["auth_user_id"]
        ),
    )
    conn.answer = lambda sql, params: [
        (1, "Dr Selim", "doctor@example.com", "doctor", DOCTOR["auth_user_id"])
    ]

    assert client.get("/api/auth/me", headers=AUTH).json()["role"] == "doctor"


@pytest.mark.parametrize("user", [STUDENT, DOCTOR])
def test_a_valid_nest_token_resolves_by_integer_user_id(
    client, conn, monkeypatch, user
):

    monkeypatch.setattr(
        security.get_settings(), "nest_jwt_access_secret", NEST_SECRET
    )
    token = jwt.encode(
        {
            "sub": str(user["id"]),
            "email": user["email"],
            "role": user["role"],
            "aud": "user",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        },
        NEST_SECRET,
        algorithm="HS256",
    )
    conn.answer = lambda sql, params: [
        (
            user["id"],
            user["name"],
            user["email"],
            user["role"],
            user["auth_user_id"],
        )
    ]

    response = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["id"] == user["id"]
    assert conn.params_for("users WHERE id = %s") == (user["id"],)


def test_a_nest_token_is_refused_when_the_database_role_changed(
    client, conn, monkeypatch
):

    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda token: VerifiedIdentity(source="nest", subject=2, role="doctor"),
    )
    conn.answer = lambda sql, params: [
        (2, "Ahmed", "student@example.com", "student", None)
    ]

    response = client.get("/api/auth/me", headers=AUTH)

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_a_nest_token_for_a_nonexistent_user_is_401(client, conn, monkeypatch):

    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda token: VerifiedIdentity(source="nest", subject=404, role="student"),
    )
    conn.answer = lambda sql, params: []

    assert client.get("/api/auth/me", headers=AUTH).status_code == 401


def test_a_valid_nest_student_token_reaches_tutor_sessions(
    client, conn, monkeypatch
):

    monkeypatch.setattr(
        security.get_settings(), "nest_jwt_access_secret", NEST_SECRET
    )
    token = jwt.encode(
        {
            "sub": str(STUDENT["id"]),
            "email": STUDENT["email"],
            "role": "student",
            "aud": "user",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        },
        NEST_SECRET,
        algorithm="HS256",
    )

    def answer(sql, params):
        if "users WHERE id = %s" in sql:
            return [(2, "Ahmed", "student@example.com", "student", None)]
        return []

    conn.answer = answer
    response = client.get(
        "/api/chat/sessions?lecture_id=7",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == []
    assert conn.params_for("FROM chat_sessions")[:3] == (2, 7, 7)


# -------------------------
# Endpoints must be authenticated at all
# -------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/lectures"),
        ("get", "/api/lectures/1/video"),
        ("get", "/api/reports/subjects"),
        ("get", "/api/reports/weekly"),
        ("get", "/api/reports/1"),
        ("get", "/api/notifications"),
        ("post", "/api/notifications/read-all"),
        ("get", "/api/events/analytics?lecture_id=1"),
        ("post", "/api/events"),
        ("get", "/api/exams"),
        ("get", "/api/questions/1"),
        ("post", "/api/chat"),
        ("post", "/api/chat/sessions"),
        ("get", "/api/chat/sessions/11111111-1111-1111-1111-111111111111/messages"),
        ("post", "/api/chat/sessions/11111111-1111-1111-1111-111111111111/messages"),
        ("get", "/api/subscriptions/access?lecture_id=1"),
        ("get", "/api/subscriptions/student/2"),
        ("get", "/api/subscriptions/doctor/1"),
    ],
)
def test_protected_endpoints_refuse_anonymous_callers(client, conn, method, path):

    response = getattr(client, method)(path)

    assert response.status_code == 401, f"{method.upper()} {path} was not protected"


def test_health_and_login_stay_public(client, conn):

    assert client.get("/health").status_code == 200

    # Wrong credentials, but reached without a token — which is the point.
    assert client.post("/api/auth/login", json={}).status_code in (401, 422)


# -------------------------
# Self-scoped endpoints derive identity from the token
# -------------------------


def test_an_event_is_recorded_against_the_authenticated_student(client, conn):
    """The body cannot choose whose watch time this is.

    These rows are the only evidence behind the engagement figures, so a body
    that could name its own student would let anyone manufacture another
    student's attendance.
    """

    as_user(STUDENT)
    conn.answer = lambda sql, params: [
        (99, STUDENT["id"], 1, "play", 12.0, "session-1", "2026-01-01T00:00:00Z")
    ]

    response = client.post(
        "/api/events",
        headers=AUTH,
        json={
            # A caller trying it on. Pydantic ignores the unknown field, and the
            # endpoint never looks at one anyway.
            "student_id": OTHER_STUDENT_ID,
            "lecture_id": 1,
            "event_type": "play",
            "video_ts": 12.0,
            "session_id": "session-1",
        },
    )

    assert response.status_code == 200
    assert conn.params_for("INSERT INTO video_events")[0] == STUDENT["id"]


def test_an_attempt_is_recorded_against_the_authenticated_student(client, conn):

    as_user(STUDENT)

    def answer(sql, params):
        if "SELECT correct_option" in sql:
            return [("A", 1)]
        return [(5, STUDENT["id"], 7, True, "A", "2026-01-01T00:00:00Z")]

    conn.answer = answer

    response = client.post(
        "/api/questions/7/attempt",
        headers=AUTH,
        json={"student_id": OTHER_STUDENT_ID, "selected_option": "A"},
    )

    assert response.status_code == 200
    assert conn.params_for("INSERT INTO question_attempts")[0] == STUDENT["id"]


def test_the_inbox_is_the_callers_own(client, conn):

    as_user(STUDENT)

    def answer(sql, params):
        if "count(*)" in sql:
            return [(0,)]
        return []

    conn.answer = answer

    response = client.get("/api/notifications?limit=5", headers=AUTH)

    assert response.status_code == 200

    # Both the listing (named parameters) and the unread count (positional)
    # are scoped to the token's user, and no id came from the query string.
    listed = conn.params_for("FROM notifications AS n")
    assert listed["user_id"] == STUDENT["id"]
    assert conn.params_for("SELECT count(*)") == (STUDENT["id"],)


def test_marking_a_notification_read_is_scoped_to_the_owner(client, conn):
    """Ids are sequential; without the scope, counting marks other people's."""

    as_user(STUDENT)
    conn.answer = lambda sql, params: []

    client.post("/api/notifications/4321/read", headers=AUTH)

    assert conn.params_for("UPDATE notifications") == (4321, STUDENT["id"])


# -------------------------
# One user must not reach another's data by changing an id
# -------------------------


def test_a_student_cannot_read_another_students_report(client, conn):

    as_user(STUDENT)

    response = client.get(
        f"/api/reports/weekly?student_id={OTHER_STUDENT_ID}&narrative=false",
        headers=AUTH,
    )

    assert response.status_code == 403


def test_a_student_cannot_read_another_students_engagement(client, conn):

    as_user(STUDENT)

    response = client.get(
        f"/api/events/analytics?lecture_id=1&student_id={OTHER_STUDENT_ID}",
        headers=AUTH,
    )

    assert response.status_code == 403


def test_a_student_cannot_read_another_students_subscriptions(client, conn):

    as_user(STUDENT)

    response = client.get(
        f"/api/subscriptions/student/{OTHER_STUDENT_ID}", headers=AUTH
    )

    assert response.status_code == 403


def test_a_student_cannot_open_another_students_stored_report(client, conn):
    """Report ids are sequential: counting from 1 used to walk everyone's."""

    as_user(STUDENT)
    conn.answer = lambda sql, params: [(OTHER_STUDENT_ID, 4)]

    assert client.get("/api/reports/77", headers=AUTH).status_code == 403


def test_a_doctor_cannot_read_a_student_they_do_not_teach(client, conn):
    """Being a doctor is not the permission — teaching that student is."""

    as_user(DOCTOR)
    conn.answer = lambda sql, params: []          # the teaches() lookup misses

    response = client.get(
        f"/api/reports/weekly?student_id={OTHER_STUDENT_ID}&narrative=false",
        headers=AUTH,
    )

    assert response.status_code == 403


def test_a_doctor_may_read_a_student_they_do_teach(client, conn):

    as_user(DOCTOR)

    def answer(sql, params):
        if "FROM enrollments" in sql and "c.doctor_id" in sql:
            return [(1,)]                          # yes, they teach them
        if "SELECT student_id, course_id FROM reports" in sql:
            return [(OTHER_STUDENT_ID, 4)]
        return []                                  # the report itself: absent

    conn.answer = answer

    response = client.get("/api/reports/77", headers=AUTH)

    # Past the authorization gate; 404 because this fake holds no report body.
    # The point is that it was not refused.
    assert response.status_code == 404


def test_a_doctor_cannot_read_another_doctors_subscribers(client, conn):

    as_user(DOCTOR)

    response = client.get("/api/subscriptions/doctor/999", headers=AUTH)

    assert response.status_code == 403


def test_a_doctor_cannot_read_exam_stats_for_a_lecture_they_do_not_own(
    client, conn
):

    as_user(DOCTOR)
    conn.answer = lambda sql, params: []           # owns_lecture() misses

    # 404 rather than 403: a 403 would confirm the lecture exists.
    assert client.get("/api/exams/500", headers=AUTH).status_code == 404


# -------------------------
# Roles
# -------------------------


def test_a_student_is_refused_a_doctors_endpoint(client, conn):

    as_user(STUDENT)

    response = client.get("/api/exams", headers=AUTH)

    assert response.status_code == 403
    assert "doctor" in response.json()["detail"].lower()


def test_a_student_is_refused_exam_statistics(client, conn):

    as_user(STUDENT)

    assert client.get("/api/exams/1", headers=AUTH).status_code == 403


def test_a_doctor_is_admitted_to_a_doctors_endpoint(client, conn):

    as_user(DOCTOR)
    conn.answer = lambda sql, params: []

    assert client.get("/api/exams", headers=AUTH).status_code == 200


# -------------------------
# The video element's query-parameter token
# -------------------------


def test_the_video_route_accepts_the_token_as_a_query_parameter(
    client, conn, monkeypatch
):
    """A <video> element cannot send an Authorization header.

    Same token and same verification — only the transport differs.
    """

    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda token: VerifiedIdentity(
            source="supabase", subject=STUDENT["auth_user_id"]
        ),
    )

    def answer(sql, params):
        if "auth_user_id" in sql:
            return [(2, "Ahmed", "s@e.com", "student", STUDENT["auth_user_id"])]
        if "SELECT doctor_id, title FROM lectures" in sql:
            return [(1, "Bone histology")]
        if "SELECT EXISTS" in sql:
            return [(True,)]                       # subscribed
        return [("lecture.mp4",)]

    conn.answer = answer

    response = client.get("/api/lectures/1/video?access_token=stub")

    # Not 401: it authenticated. Whatever happens next (paywall, missing file)
    # is a different question.
    assert response.status_code != 401


def test_the_video_route_still_refuses_a_bad_query_token(client, conn, monkeypatch):

    def reject(token):
        raise InvalidToken("nope")

    monkeypatch.setattr(deps, "decode_access_token", reject)

    assert client.get("/api/lectures/1/video?access_token=forged").status_code == 401
