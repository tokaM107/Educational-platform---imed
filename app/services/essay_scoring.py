"""Pure deterministic scoring for essay-grading results."""

from decimal import Decimal, ROUND_HALF_UP

from app.schemas.essay_grading import (
    AnswerEvaluationResult, Criterion, DeterministicScoring, EvaluationStatus,
    ScoreContribution,
)


STATUS_FACTORS = {
    "yes": Decimal("1.0"),
    "partial": Decimal("0.5"),
    "no": Decimal("0.0"),
    "contradicted": Decimal("0.0"),
}
TWO_PLACES = Decimal("0.01")


class IncompleteEvaluation(ValueError):
    pass


def ensure_complete(criteria: list[Criterion], evaluation: AnswerEvaluationResult):
    expected = {criterion.id for criterion in criteria}
    actual = {result.criterion_id for result in evaluation.results}
    unknown = actual - expected
    missing = expected - actual
    if unknown:
        raise IncompleteEvaluation(
            f"unknown evaluator criterion IDs: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise IncompleteEvaluation(
            f"missing evaluator results: {', '.join(sorted(missing))}"
        )
    if len(evaluation.results) != len(criteria):
        raise IncompleteEvaluation("every criterion must be evaluated exactly once")


def _money(value: Decimal) -> str:
    return str(value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def calculate_score(
    criteria: list[Criterion],
    evaluation: AnswerEvaluationResult,
    max_points: Decimal,
    needs_review: bool = False,
) -> DeterministicScoring:
    if not criteria:
        raise IncompleteEvaluation("criteria are missing")
    ensure_complete(criteria, evaluation)
    maximum = Decimal(max_points)
    if maximum <= 0:
        raise ValueError("max_points must be positive")

    by_id = {result.criterion_id: result for result in evaluation.results}
    weight = maximum / Decimal(len(criteria))
    total = Decimal("0")
    breakdown = []
    for criterion in criteria:
        result = by_id[criterion.id]
        awarded = weight * STATUS_FACTORS[result.status.value]
        total += awarded
        breakdown.append(ScoreContribution(
            criterion_id=criterion.id,
            status=result.status,
            weight=_money(weight),
            awarded_points=_money(awarded),
        ))

    total = min(max(total, Decimal("0")), maximum)
    return DeterministicScoring(
        score=_money(total),
        max_points=_money(maximum),
        needs_review=bool(needs_review or evaluation.needs_review),
        score_breakdown=breakdown,
    )
