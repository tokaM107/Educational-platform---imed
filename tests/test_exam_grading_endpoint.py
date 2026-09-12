"""Internal exam-grading endpoints: the criteria/evaluate split.

The load-bearing test in here is `test_three_students_share_one_criteria_set`.
Everything else guards a boundary; that one guards the reason the split exists.
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import exam_grading
from app.config import get_settings
from app.main import app
from app.schemas.essay_grading import (
    AnswerEvaluationResult, CriteriaGenerationResult, Criterion, CriterionEvaluation,
    EvaluationStatus,
)
from app.services.essay_grading import GradingStageError

KEY = "x" * 40
CRITERIA = [
    Criterion(id="C1", claim="يذكر انقباض البطينين"),
    Criterion(id="C2", claim="يذكر انبساط البطينين"),
]


@pytest.fixture(autouse=True)
def internal_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", KEY, raising=False)


def stage(parsed):
    return SimpleNamespace(
        parsed=parsed,
        metadata=SimpleNamespace(model_identifier="gemini-test", prompt_version="v1"),
    )


class FakeService:
    """Stands in for EssayGradingService. Counts calls; never reaches a model."""

    def __init__(self, *, criteria=None, evaluation=None, delay=0.0, raises=None):
        self.criteria_calls = 0
        self.evaluate_calls = []
        self._criteria = criteria if criteria is not None else CRITERIA
        self._evaluation = evaluation
        self.delay = delay
        self.raises = raises

    async def generate_criteria(self, question, model_answer):
        self.criteria_calls += 1
        if isinstance(self.raises, Exception):
            raise self.raises
        if self.delay:
            await asyncio.sleep(self.delay)
        return stage(CriteriaGenerationResult(
            criteria=self._criteria, needs_review=False, review_reason=None,
        ))

    async def evaluate_student_answer(self, question, criteria, student_answer):
        self.evaluate_calls.append((tuple(c.id for c in criteria), student_answer))
        if isinstance(self.raises, Exception):
            raise self.raises
        if self.delay:
            await asyncio.sleep(self.delay)
        results = self._evaluation or [
            CriterionEvaluation(criterion_id=c.id, status=EvaluationStatus("yes"), reason="ok")
            for c in criteria
        ]
        return stage(AnswerEvaluationResult(results=results, needs_review=False))


def install(monkeypatch, service):
    monkeypatch.setattr(exam_grading, "EssayGradingService", lambda: service)
    return service


def post(client, path, payload, key=KEY):
    headers = {} if key is None else {"X-Internal-Key": key}
    return client.post(f"/api/internal/exam-grading{path}", json=payload, headers=headers)


def criteria_body(**over):
    return {"question": "اشرح دورة القلب", "max_score": "5",
            "model_answer": "الانقباض ثم الانبساط", **over}


def evaluate_body(answer="الانقباض ثم الانبساط", **over):
    item = {"reference": "q1", "question": "اشرح دورة القلب", "student_answer": answer,
            "max_score": "5", "criteria": [c.model_dump() for c in CRITERIA],
            "criteria_hash": "abc123"}
    item.update(over.pop("item", {}))
    return {"attempt_id": 1, "items": [item], **over}


# --- auth ------------------------------------------------------------------

def test_both_endpoints_reject_a_caller_without_the_key(monkeypatch):
    install(monkeypatch, FakeService())
    with TestClient(app) as client:
        assert post(client, "/criteria", criteria_body(), key=None).status_code == 401
        assert post(client, "/evaluate", evaluate_body(), key="no" * 20).status_code == 401


# --- criteria --------------------------------------------------------------

def test_criteria_are_generated_and_content_addressed(monkeypatch):
    service = install(monkeypatch, FakeService())
    with TestClient(app) as client:
        res = post(client, "/criteria", criteria_body())
    body = res.json()
    assert res.status_code == 200 and body["status"] == "ready"
    assert [c["id"] for c in body["criteria"]] == ["C1", "C2"]
    assert len(body["criteria_hash"]) == 64
    assert service.criteria_calls == 1


def test_marking_guide_leads_the_reference_material(monkeypatch):
    seen = {}

    class Recording(FakeService):
        async def generate_criteria(self, question, model_answer):
            seen["reference"] = model_answer
            return await super().generate_criteria(question, model_answer)

    install(monkeypatch, Recording())
    with TestClient(app) as client:
        post(client, "/criteria", criteria_body(
            marking_guide="2 للانقباض، 2 للانبساط، 1 للوضوح",
            teacher_instructions="كن متساهلاً",
        ))

    reference = seen["reference"]
    # The guide is the teacher saying how marks are split, so it goes first.
    assert reference.index("2 للانقباض") < reference.index("الانقباض ثم الانبساط")
    assert "كن متساهلاً" in reference


def test_thin_material_still_generates_but_flags_review(monkeypatch):
    install(monkeypatch, FakeService())
    with TestClient(app) as client:
        res = post(client, "/criteria", {"question": "س", "max_score": "5"})
    body = res.json()
    assert body["status"] == "ready"
    assert body["needs_review"] is True
    assert "model answer" in (body["review_reason"] or "")


def test_criteria_failure_is_reported_not_faked(monkeypatch):
    install(monkeypatch, FakeService(raises=GradingStageError("boom", SimpleNamespace())))
    with TestClient(app) as client:
        res = post(client, "/criteria", criteria_body())
    body = res.json()
    assert body["status"] == "failed" and body["criteria"] == []
    assert body["criteria_hash"] is None


def test_criteria_generation_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(exam_grading, "CRITERIA_TIMEOUT_SECONDS", 0.05)
    install(monkeypatch, FakeService(delay=0.4))
    with TestClient(app) as client:
        res = post(client, "/criteria", criteria_body())
    assert res.json()["status"] == "failed"


# --- evaluation ------------------------------------------------------------

def test_evaluation_never_generates_criteria(monkeypatch):
    service = install(monkeypatch, FakeService())
    with TestClient(app) as client:
        res = post(client, "/evaluate", evaluate_body())
    assert res.json()["results"][0]["status"] == "graded"
    assert service.criteria_calls == 0


def test_evaluation_requires_criteria(monkeypatch):
    install(monkeypatch, FakeService())
    with TestClient(app) as client:
        res = post(client, "/evaluate", evaluate_body(item={"criteria": []}))
    # Rejected by the schema: there is no branch that would generate them.
    assert res.status_code == 422


def test_three_students_share_one_criteria_set(monkeypatch):
    """The reason this architecture exists.

    One generation at publish, three submissions after it, and every
    evaluation sees the identical criteria — same ids, same hash echoed back.
    """

    service = install(monkeypatch, FakeService())
    with TestClient(app) as client:
        generated = post(client, "/criteria", criteria_body()).json()
        assert service.criteria_calls == 1

        frozen = generated["criteria"]
        digest = generated["criteria_hash"]
        hashes = []
        for answer in ("الانقباض ثم الانبساط", "ventricular contraction then relaxation",
                       "الانقباض ثم relaxation مع valve closure"):
            res = post(client, "/evaluate", {
                "attempt_id": 1,
                "items": [{"reference": "q1", "question": "اشرح دورة القلب",
                           "student_answer": answer, "max_score": "5",
                           "criteria": frozen, "criteria_hash": digest}],
            })
            hashes.append(res.json()["results"][0]["criteria_hash"])

    assert service.criteria_calls == 1, "criteria were regenerated per student"
    assert hashes == [digest, digest, digest]
    assert {ids for ids, _ in service.evaluate_calls} == {("C1", "C2")}


def test_blank_answer_scores_zero_without_a_model_call(monkeypatch):
    service = install(monkeypatch, FakeService())
    with TestClient(app) as client:
        res = post(client, "/evaluate", evaluate_body(answer="   "))
    result = res.json()["results"][0]
    assert result["status"] == "graded" and Decimal(result["awarded_score"]) == 0
    assert service.evaluate_calls == []


def test_partial_credit_is_derived_deterministically(monkeypatch):
    install(monkeypatch, FakeService(evaluation=[
        CriterionEvaluation(criterion_id="C1", status=EvaluationStatus("yes"), reason="ok"),
        CriterionEvaluation(criterion_id="C2", status=EvaluationStatus("no"), reason="missing"),
    ]))
    with TestClient(app) as client:
        res = post(client, "/evaluate", evaluate_body())
    # Two equally weighted criteria over 5 marks: one met is half.
    assert Decimal(res.json()["results"][0]["awarded_score"]) == Decimal("2.50")


def test_feedback_names_claims_not_identifiers(monkeypatch):
    install(monkeypatch, FakeService(evaluation=[
        CriterionEvaluation(criterion_id="C1", status=EvaluationStatus("yes"), reason="ok"),
        CriterionEvaluation(criterion_id="C2", status=EvaluationStatus("no"), reason="missing"),
    ]))
    with TestClient(app) as client:
        res = post(client, "/evaluate", evaluate_body())
    feedback = res.json()["results"][0]["feedback"]
    assert "يذكر انقباض البطينين" in feedback and "C1" not in feedback


def test_evaluation_failure_does_not_become_zero(monkeypatch):
    install(monkeypatch, FakeService(raises=GradingStageError("down", SimpleNamespace())))
    with TestClient(app) as client:
        res = post(client, "/evaluate", evaluate_body())
    result = res.json()["results"][0]
    assert result["status"] == "failed"
    assert result["awarded_score"] is None
    assert result["needs_review"] is True


def test_evaluation_timeout_does_not_become_zero(monkeypatch):
    monkeypatch.setattr(exam_grading, "PER_ITEM_TIMEOUT_SECONDS", 0.05)
    install(monkeypatch, FakeService(delay=0.4))
    with TestClient(app) as client:
        res = post(client, "/evaluate", evaluate_body())
    assert res.json()["results"][0]["awarded_score"] is None


def test_several_essays_evaluate_concurrently(monkeypatch):
    install(monkeypatch, FakeService(delay=0.2))
    items = [{"reference": f"q{i}", "question": "س", "student_answer": "ج", "max_score": "5",
              "criteria": [c.model_dump() for c in CRITERIA], "criteria_hash": "h"}
             for i in range(4)]
    with TestClient(app) as client:
        res = post(client, "/evaluate", {"attempt_id": 7, "items": items})
    body = res.json()
    assert len(body["results"]) == 4
    assert body["elapsed_ms"] < 700, "evaluation ran serially"


def test_neither_shape_carries_student_identity():
    from app.schemas.exam_grading import CriteriaRequest, EvaluationItem, EvaluationRequest

    fields = (set(CriteriaRequest.model_fields) | set(EvaluationItem.model_fields)
              | set(EvaluationRequest.model_fields))
    for leak in ("student_id", "student_name", "email", "user_id"):
        assert leak not in fields
