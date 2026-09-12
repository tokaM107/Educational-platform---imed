"""Contracts for the isolated essay-grading evaluation prototype."""

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_QUESTION_CHARS = 10_000
MAX_ANSWER_CHARS = 50_000
MAX_CRITERIA = 50
# How far the weights may drift from 1 before the rubric is rejected.
#
# Wide enough for the rounding a model does when it writes 0.33 three times,
# narrow enough that a rubric which genuinely does not add up is refused.
WEIGHT_SUM_TOLERANCE = Decimal("0.02")


class StrictLLMModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Criterion(StrictLLMModel):
    id: str = Field(pattern=r"^C[1-9][0-9]*$")
    claim: str = Field(min_length=1, max_length=2_000)
    # The teacher's own allocation for this criterion, when the caller has one.
    #
    # Absent on anything the model produces: a criteria proposal says what a
    # full answer should contain, not what it is worth. The marks are the
    # teacher's to set, and they arrive here only on the evaluation request, so
    # the deterministic scorer can weight by them. They are never put in a
    # prompt - the model judges whether a claim was met, and how much that is
    # worth is not its business.
    # strict=False for this field alone: the enclosing model is strict because
    # it also parses LLM output, where a string where a number belongs is a
    # malformed answer worth rejecting. But this field never comes from the
    # model - it arrives on the wire as ordinary JSON, where a whole number is
    # an int and no client would think to send a Decimal. Strictness here
    # rejected every real evaluation request with the marks attached.
    marks: Decimal | None = Field(default=None, ge=0, le=1000, strict=False)
    # How much of the question this criterion is worth, as a share of 1.
    #
    # The model proposes RELATIVE IMPORTANCE, never marks. Asked for marks it
    # would be inventing a mark scheme; asked which parts matter more, it is
    # doing the thing it can actually do, and the marks fall out of the
    # question's own total by arithmetic the caller performs. So a 5-mark
    # question with weights 0.4/0.35/0.25 becomes 2/1.75/1.25 deterministically.
    #
    # Zero is rejected rather than allowed: a criterion worth nothing is not a
    # criterion, and a rubric containing one would grade an answer against a
    # standard that cannot affect its mark.
    weight: Decimal | None = Field(default=None, gt=0, le=1, strict=False)


class ProposedCriterion(StrictLLMModel):
    """One criterion as the MODEL must return it.

    Separate from `Criterion` on purpose. `Criterion` is the wire shape a
    caller sends for evaluation, where `weight` is optional because a rubric
    written before weights existed does not have one. Here it is REQUIRED and
    non-nullable, because the provider schema is generated from this class and
    an optional field with a default is a field the model is entitled to omit
    — which is exactly what it did, and every generation failed validation as
    a "malformed structured response".
    """

    id: str = Field(pattern=r"^C[1-9][0-9]*$")
    claim: str = Field(min_length=1, max_length=2_000)
    # Relative importance, a share of 1. A plain float rather than a Decimal:
    # this one is generated into a JSON schema the provider reads, and a
    # Decimal renders as a string-or-number union the model has to choose
    # between. The share is converted to Decimal for arithmetic downstream.
    weight: float = Field(gt=0, le=1)


class CriteriaGenerationResult(StrictLLMModel):
    criteria: list[ProposedCriterion] = Field(min_length=1, max_length=MAX_CRITERIA)
    needs_review: bool
    review_reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("criteria")
    @classmethod
    def unique_ids(cls, criteria):
        ids = [criterion.id for criterion in criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion IDs must be unique")
        return criteria

    @field_validator("criteria")
    @classmethod
    def normalized_weights(cls, criteria):
        """Every criterion carries a positive weight, and they sum to one.

        Rejected rather than repaired. Silently normalizing a model that
        returned 0.4/0.4/0.4 would publish a mark scheme nobody chose, and one
        that returned a zero would hide a criterion that cannot affect the
        mark. The caller retries or the teacher writes the rubric; both are
        better than a plausible invention.

        The tolerance exists because the model returns decimals and three
        thirds do not sum to exactly one in any finite representation. It is
        deliberately tight: this catches representation error, not a model
        that did not add up.
        """

        total = sum(Decimal(str(criterion.weight)) for criterion in criteria)
        if abs(total - Decimal("1")) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"criterion weights must sum to 1, got {total}")

        return criteria


class EvaluationStatus(str, Enum):
    YES = "yes"
    PARTIAL = "partial"
    NO = "no"
    CONTRADICTED = "contradicted"


class CriterionEvaluation(StrictLLMModel):
    criterion_id: str = Field(pattern=r"^C[1-9][0-9]*$")
    status: EvaluationStatus
    evidence: str | None = Field(default=None, max_length=5_000)
    reason: str = Field(min_length=1, max_length=1_000)


class AnswerEvaluationResult(StrictLLMModel):
    results: list[CriterionEvaluation] = Field(max_length=MAX_CRITERIA)
    needs_review: bool
    review_reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("results")
    @classmethod
    def unique_ids(cls, results):
        ids = [result.criterion_id for result in results]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation criterion IDs must be unique")
        return results


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelStageResult(BaseModel):
    model_identifier: str
    prompt_version: str
    latency_ms: int
    usage: Usage
    raw_response: str | None
    parsed_response: CriteriaGenerationResult | AnswerEvaluationResult | None
    retry_count: int = 0
    retry_errors: list[str] = Field(default_factory=list)
    error: str | None = None


class ScoreContribution(BaseModel):
    criterion_id: str
    status: EvaluationStatus
    weight: str
    awarded_points: str


class DeterministicScoring(BaseModel):
    score: str
    max_points: str
    needs_review: bool
    score_breakdown: list[ScoreContribution]


class GenerateCriteriaRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    model_answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)


class EvaluateAnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    criteria: list[Criterion] = Field(min_length=1, max_length=MAX_CRITERIA)
    student_answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)


class GradeRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    model_answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)
    student_answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)
    max_points: Decimal = Field(gt=0)


class GradeResponse(BaseModel):
    run_status: str
    criteria_model: ModelStageResult
    evaluator_model: ModelStageResult | None
    deterministic_scoring: DeterministicScoring | None
    error: str | None = None


class DatasetEvaluationRequest(BaseModel):
    save_json: str | None = None
    save_csv: str | None = None
