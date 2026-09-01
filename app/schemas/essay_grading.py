"""Contracts for the isolated essay-grading evaluation prototype."""

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_QUESTION_CHARS = 10_000
MAX_ANSWER_CHARS = 50_000
MAX_CRITERIA = 50


class StrictLLMModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Criterion(StrictLLMModel):
    id: str = Field(pattern=r"^C[1-9][0-9]*$")
    claim: str = Field(min_length=1, max_length=2_000)


class CriteriaGenerationResult(StrictLLMModel):
    criteria: list[Criterion] = Field(min_length=1, max_length=MAX_CRITERIA)
    needs_review: bool
    review_reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("criteria")
    @classmethod
    def unique_ids(cls, criteria):
        ids = [criterion.id for criterion in criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion IDs must be unique")
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
