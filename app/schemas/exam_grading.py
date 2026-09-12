"""Wire shapes for the internal exam essay-grading endpoint.

Deliberately free of student identity. The grader needs the question, the
teacher's marking material and the text that was written; who wrote it is the
caller's business and sending it here would put a named student's essay into a
model request for no grading benefit.
"""

from decimal import Decimal

from pydantic import BaseModel, Field

MAX_QUESTION_CHARS = 8_000
MAX_ANSWER_CHARS = 20_000
MAX_GUIDE_CHARS = 8_000
MAX_ITEMS = 20


class EssayGradingItem(BaseModel):
    """One essay answer to mark."""

    # Opaque to this service: it is echoed back so the caller can match the
    # result to its own row without this service knowing what it identifies.
    reference: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    student_answer: str = Field(max_length=MAX_ANSWER_CHARS)
    max_points: Decimal = Field(gt=0, le=1000)
    model_answer: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)
    marking_guide: str | None = Field(default=None, max_length=MAX_GUIDE_CHARS)
    teacher_instructions: str | None = Field(default=None, max_length=MAX_GUIDE_CHARS)


class ExamGradingRequest(BaseModel):
    """Every essay on one attempt, graded together."""

    attempt_id: int = Field(gt=0)
    items: list[EssayGradingItem] = Field(min_length=1, max_length=MAX_ITEMS)


class EssayGradingResult(BaseModel):
    reference: str
    status: str
    awarded_score: Decimal | None = None
    max_score: Decimal
    feedback: str | None = None
    # True when the grader itself is unsure — too little marking material, or
    # an answer it could not judge. The caller decides what to do about it;
    # this service never silently awards zero to express doubt.
    needs_review: bool = False
    error: str | None = None


class ExamGradingResponse(BaseModel):
    attempt_id: int
    results: list[EssayGradingResult]
    elapsed_ms: int
