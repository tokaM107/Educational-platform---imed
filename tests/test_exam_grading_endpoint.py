"""The internal exam-grading endpoint: auth, batching, bounds and failure."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import exam_grading
from app.config import get_settings
from app.main import app
from app.schemas.essay_grading import DeterministicScoring, ScoreContribution, EvaluationStatus
from app.services.essay_grading import GradingStageError

KEY = "x" * 40


@pytest.fixture(autouse=True)
def internal_key(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "internal_api_key", KEY, raising=False,
    )
    yield


def scoring(score: str, maximum: str, needs_review: bool = False):
    return DeterministicScoring(
        score=score, max_points=maximum, needs_review=needs_review,
        score_breakdown=[
            ScoreContribution(
                criterion_id="mentions systole", status=EvaluationStatus("yes"),
                weight="2.50", awarded_points="2.50",
            ),
            ScoreContribution(
                criterion_id="mentions diastole", status=EvaluationStatus("no"),
                weight="2.50", awarded_points="0.00",
            ),
        ],
    )


class FakeService:
    """Stands in for EssayGradingService. Never calls a model."""

    def __init__(self, outcome=None, delay=0.0):
        self.outcome = outcome or SimpleNamespace(
            run_status="completed", deterministic_scoring=scoring("2.50", "5"),
            criteria_model=SimpleNamespace(parsed_response=None), error=None,
        )
        self.delay = delay
        self.calls = []

    async def grade(self, question, model_answer, student_answer, max_points):
        self.calls.append((question, model_answer, student_answer, max_points))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def body(**over):
    item = {
        "reference": "q1", "question": "اشرح دورة القلب",
        "student_answer": "الانقباض ثم الانبساط", "max_points": "5",
        "model_answer": "الانقباض ثم الانبساط",
    }
    item.update(over.pop("item", {}))
    return {"attempt_id": 1, "items": [item], **over}


def post(client, payload, key=KEY):
    headers = {} if key is None else {"X-Internal-Key": key}
    return client.post("/api/internal/exam-grading", json=payload, headers=headers)


def test_rejects_a_caller_without_the_internal_key(monkeypatch):
    monkeypatch.setattr(exam_grading, "EssayGradingService", lambda: FakeService())
    with TestClient(app) as client:
        assert post(client, body(), key=None).status_code == 401
        assert post(client, body(), key="wrong" * 10).status_code == 401


def test_grades_an_essay_and_returns_a_bounded_score(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(exam_grading, "EssayGradingService", lambda: fake)
    with TestClient(app) as client:
        res = post(client, body())
    assert res.status_code == 200
    payload = res.json()
    result = payload["results"][0]
    assert result["status"] == "graded"
    assert Decimal(result["awarded_score"]) == Decimal("2.50")
    assert Decimal(result["max_score"]) == Decimal("5")
    assert result["reference"] == "q1"
    assert "elapsed_ms" in payload


def test_marking_guide_reaches_the_grader(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(exam_grading, "EssayGradingService", lambda: fake)
    with TestClient(app) as client:
        post(client, body(item={"marking_guide": "2 systole, 2 diastole, 1 clarity",
                                "teacher_instructions": "be generous"}))
    reference = fake.calls[0][1]
    assert "2 systole" in reference and "be generous" in reference


def test_missing_model_answer_still_grades_but_flags_review(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(exam_grading, "EssayGradingService", lambda: fake)
    with TestClient(app) as client:
        res = post(client, body(item={"model_answer": None}))
    assert res.json()["results"][0]["needs_review"] is True


def test_blank_answer_scores_zero_without_calling_the_model(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(exam_grading, "EssayGradingService", lambda: fake)
    with TestClient(app) as client:
        res = post(client, body(item={"student_answer": "   "}))
    result = res.json()["results"][0]
    assert result["status"] == "graded"
    assert Decimal(result["awarded_score"]) == Decimal("0")
    assert fake.calls == []


def test_a_failed_stage_is_reported_not_scored_zero(monkeypatch):
    fake = FakeService(outcome=SimpleNamespace(
        run_status="grading_failed", deterministic_scoring=None, error="model unreachable",
    ))
    monkeypatch.setattr(exam_grading, "EssayGradingService", lambda: fake)
    with TestClient(app) as client:
        res = post(client, body())
    result = res.json()["results"][0]
    assert result["status"] == "failed"
    assert result["awarded_score"] is None
    assert result["needs_review"] is True


def test_a_raising_grader_is_reported_not_scored_zero(monkeypatch):
    monkeypatch.setattr(
        exam_grading, "EssayGradingService",
        lambda: FakeService(outcome=GradingStageError("boom", SimpleNamespace())),
    )
    with TestClient(app) as client:
        res = post(client, body())
    assert res.json()["results"][0]["status"] == "failed"
    assert res.json()["results"][0]["awarded_score"] is None


def test_a_slow_essay_times_out_into_review(monkeypatch):
    monkeypatch.setattr(exam_grading, "PER_ITEM_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(exam_grading, "EssayGradingService", lambda: FakeService(delay=0.5))
    with TestClient(app) as client:
        res = post(client, body())
    result = res.json()["results"][0]
    assert result["status"] == "failed"
    assert result["needs_review"] is True
    assert result["awarded_score"] is None


def test_a_score_above_the_maximum_is_clamped(monkeypatch):
    monkeypatch.setattr(
        exam_grading, "EssayGradingService",
        lambda: FakeService(outcome=SimpleNamespace(
            run_status="completed", deterministic_scoring=scoring("99", "5"),
            criteria_model=SimpleNamespace(parsed_response=None), error=None,
        )),
    )
    with TestClient(app) as client:
        res = post(client, body())
    assert Decimal(res.json()["results"][0]["awarded_score"]) == Decimal("5")


def test_several_essays_are_graded_concurrently(monkeypatch):
    fake = FakeService(delay=0.2)
    monkeypatch.setattr(exam_grading, "EssayGradingService", lambda: fake)
    items = [
        {"reference": f"q{i}", "question": "س", "student_answer": "ج",
         "max_points": "5", "model_answer": "ن"}
        for i in range(4)
    ]
    with TestClient(app) as client:
        res = post(client, {"attempt_id": 7, "items": items})
    payload = res.json()
    assert len(payload["results"]) == 4
    # Serial would be 4 x 200ms. Concurrency is capped at 4, so one batch.
    assert payload["elapsed_ms"] < 700


def test_request_carries_no_student_identity():
    from app.schemas.exam_grading import EssayGradingItem, ExamGradingRequest

    fields = set(EssayGradingItem.model_fields) | set(ExamGradingRequest.model_fields)
    for leak in ("student_id", "student_name", "email", "user_id"):
        assert leak not in fields
