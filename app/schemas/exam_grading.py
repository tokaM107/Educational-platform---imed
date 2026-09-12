"""Wire shapes for the internal exam-grading endpoints.

Two operations, deliberately separate:

    criteria    run once when a teacher publishes an exam
    evaluate    run for every student submission, against those frozen criteria

The split is the point. Criteria generated per student meant two students could
be marked against different standards for the same question, and it put a model
call on the path between pressing submit and seeing a result. Evaluation here
CANNOT generate criteria: missing criteria is a preparation error, not
something to paper over at grading time.

Neither shape carries student identity. The grader needs the question, the
teacher's marking material and the text that was written; who wrote it is the
caller's business.
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.essay_grading import Criterion

MAX_QUESTION_CHARS = 8_000
MAX_ANSWER_CHARS = 20_000
MAX_GUIDE_CHARS = 8_000
MAX_ITEMS = 20
MAX_CRITERIA = 12


# --- criteria: publish time ------------------------------------------------


class CriteriaRequest(BaseModel):
    """Everything the grading standard for one essay question derives from."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    max_score: Decimal = Field(gt=0, le=1000)
    model_answer: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)
    marking_guide: str | None = Field(default=None, max_length=MAX_GUIDE_CHARS)
    teacher_instructions: str | None = Field(default=None, max_length=MAX_GUIDE_CHARS)


class CriteriaResponse(BaseModel):
    status: str
    criteria: list[Criterion] = Field(default_factory=list)
    # Content address of the criteria themselves. Two attempts graded against
    # the same standard carry the same value, which is what makes "every
    # student was marked the same way" a check rather than a claim.
    criteria_hash: str | None = None
    # True when the grader had thin material to work from — no model answer, no
    # rubric. The criteria are still usable; the caller may want a human to
    # look before students sit the exam.
    needs_review: bool = False
    review_reason: str | None = None
    model_identifier: str | None = None
    prompt_version: str | None = None
    elapsed_ms: int = 0
    error: str | None = None


# --- evaluate: submission time ---------------------------------------------


class EvaluationItem(BaseModel):
    """One student answer, and the frozen criteria it is marked against."""

    reference: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    student_answer: str = Field(max_length=MAX_ANSWER_CHARS)
    max_score: Decimal = Field(gt=0, le=1000)
    # Required, and carrying the teacher's own mark allocation when they have
    # set one. There is no branch here that generates them.
    criteria: list[Criterion] = Field(min_length=1, max_length=MAX_CRITERIA)
    criteria_hash: str | None = Field(default=None, max_length=128)


class EvaluationRequest(BaseModel):
    attempt_id: int = Field(gt=0)
    items: list[EvaluationItem] = Field(min_length=1, max_length=MAX_ITEMS)


class EvaluationResult(BaseModel):
    reference: str
    status: str
    awarded_score: Decimal | None = None
    max_score: Decimal
    feedback: str | None = None
    needs_review: bool = False
    # Echoed back so the caller can record which standard produced the mark.
    criteria_hash: str | None = None
    error: str | None = None


class EvaluationResponse(BaseModel):
    attempt_id: int
    results: list[EvaluationResult]
    elapsed_ms: int
