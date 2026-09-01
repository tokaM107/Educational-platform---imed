from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.essay_grading import (
    AnswerEvaluationResult, CriteriaGenerationResult, Criterion,
)
from app.services import essay_scoring
from app.services.essay_grading import EssayGradingService, provider_response_schema
from app.services.essay_scoring import IncompleteEvaluation, calculate_score
from app.services.llm import GeneratedReply


def settings():
    return SimpleNamespace(
        essay_criteria_model="criteria-test",
        essay_evaluator_model="evaluator-test",
    )


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate_with_metadata(self, **kwargs):
        self.calls.append(kwargs)
        parsed = self.replies.pop(0)
        return GeneratedReply(
            parsed=parsed,
            model_name=kwargs["model_name"],
            raw_response="{}",
            input_tokens=10,
            output_tokens=5,
        )


def criterion(identifier="C1", claim="Required claim"):
    return Criterion(id=identifier, claim=claim)


def evaluation(rows, needs_review=False):
    return AnswerEvaluationResult.model_validate_json(
        __import__("json").dumps({
            "results": rows,
            "needs_review": needs_review,
            "review_reason": "ambiguous" if needs_review else None,
        }),
        strict=True,
    )


@pytest.mark.anyio
async def test_criteria_generator_parses_structured_output_and_uses_zero_temperature():
    llm = FakeLLM([{
        "criteria": [{"id": "C1", "claim": "Insulin lowers glucose"}],
        "needs_review": False,
        "review_reason": None,
    }])
    result = await EssayGradingService(settings(), llm).generate_criteria("q", "a")
    assert result.parsed.criteria[0].id == "C1"
    assert llm.calls[0]["model_name"] == "criteria-test"
    assert llm.calls[0]["temperature"] == 0
    assert isinstance(llm.calls[0]["response_schema"], dict)


def test_provider_schema_removes_unsupported_additional_properties_recursively():
    schema = provider_response_schema(CriteriaGenerationResult)

    def keys(value):
        if isinstance(value, dict):
            yield from value
            for child in value.values():
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert "additionalProperties" not in set(keys(schema))
    assert "additional_properties" not in set(keys(schema))
    assert schema["properties"]["criteria"]["items"]["$ref"] == (
        "#/$defs/Criterion"
    )


@pytest.mark.anyio
async def test_evaluator_parses_each_allowed_status():
    rows = [
        {"criterion_id": f"C{i}", "status": status, "evidence": None,
         "reason": "brief"}
        for i, status in enumerate(
            ["yes", "partial", "no", "contradicted"], start=1
        )
    ]
    llm = FakeLLM([{
        "results": rows, "needs_review": False, "review_reason": None,
    }])
    criteria = [criterion(f"C{i}") for i in range(1, 5)]
    result = await EssayGradingService(settings(), llm).evaluate_student_answer(
        "q", criteria, "student"
    )
    assert [item.status.value for item in result.parsed.results] == [
        "yes", "partial", "no", "contradicted"
    ]
    assert llm.calls[0]["model_name"] == "evaluator-test"


def test_unknown_status_is_rejected():
    with pytest.raises(ValidationError):
        evaluation([{
            "criterion_id": "C1", "status": "mostly", "evidence": None,
            "reason": "invalid",
        }])


def test_duplicate_criterion_ids_are_rejected():
    with pytest.raises(ValidationError, match="criterion IDs must be unique"):
        CriteriaGenerationResult.model_validate_json("""{
          "criteria": [
            {"id":"C1","claim":"one"}, {"id":"C1","claim":"two"}
          ], "needs_review":false, "review_reason":null
        }""", strict=True)


@pytest.mark.parametrize("rows, message", [
    ([], "missing evaluator results"),
    ([{"criterion_id": "C2", "status": "yes", "evidence": "x",
       "reason": "present"}], "unknown evaluator criterion IDs"),
])
def test_missing_or_unknown_evaluator_result_prevents_scoring(rows, message):
    with pytest.raises(IncompleteEvaluation, match=message):
        calculate_score([criterion()], evaluation(rows), Decimal("4.00"))


def test_full_score_calculation():
    result = calculate_score(
        [criterion("C1"), criterion("C2")],
        evaluation([
            {"criterion_id": "C1", "status": "yes", "evidence": "a", "reason": "ok"},
            {"criterion_id": "C2", "status": "yes", "evidence": "b", "reason": "ok"},
        ]), Decimal("8.00")
    )
    assert result.score == "8.00"
    assert [item.awarded_points for item in result.score_breakdown] == ["4.00", "4.00"]


def test_partial_and_contradicted_scoring():
    result = calculate_score(
        [criterion("C1"), criterion("C2")],
        evaluation([
            {"criterion_id": "C1", "status": "partial", "evidence": "a", "reason": "some"},
            {"criterion_id": "C2", "status": "contradicted", "evidence": "b", "reason": "opposite"},
        ]), Decimal("4.00")
    )
    assert result.score == "1.00"
    assert result.score_breakdown[1].awarded_points == "0.00"


def test_decimal_rounding_is_half_up():
    result = calculate_score(
        [criterion("C1"), criterion("C2"), criterion("C3")],
        evaluation([
            {"criterion_id": "C1", "status": "partial", "evidence": "x", "reason": "some"},
            {"criterion_id": "C2", "status": "no", "evidence": None, "reason": "none"},
            {"criterion_id": "C3", "status": "no", "evidence": None, "reason": "none"},
        ]), Decimal("1.00")
    )
    assert result.score == "0.17"


def test_score_is_capped_at_maximum(monkeypatch):
    monkeypatch.setitem(essay_scoring.STATUS_FACTORS, "yes", Decimal("2.0"))
    result = calculate_score(
        [criterion()],
        evaluation([{"criterion_id": "C1", "status": "yes", "evidence": "x", "reason": "ok"}]),
        Decimal("3.00"),
    )
    assert result.score == "3.00"


def test_needs_review_preserves_a_provisional_score():
    result = calculate_score(
        [criterion()],
        evaluation([{"criterion_id": "C1", "status": "yes", "evidence": "x", "reason": "ok"}], True),
        Decimal("2.00"),
    )
    assert result.score == "2.00"
    assert result.needs_review is True


@pytest.mark.anyio
async def test_grading_failure_has_no_finalized_score_for_missing_result():
    llm = FakeLLM([
        {"criteria": [{"id": "C1", "claim": "one"}], "needs_review": False, "review_reason": None},
        {"results": [], "needs_review": False, "review_reason": None},
    ])
    result = await EssayGradingService(settings(), llm).grade(
        "q", "model", "student", Decimal("4.00")
    )
    assert result.run_status == "grading_failed"
    assert result.deterministic_scoring is None


def test_dataset_has_exactly_ten_questions_and_four_cases_each():
    from app.services.essay_dataset import load_dataset
    dataset = load_dataset()
    assert dataset["fixture_type"] == "synthetic_engineering_evaluation"
    assert len(dataset["questions"]) == 10
    assert all(len(question["student_answers"]) >= 4 for question in dataset["questions"])
