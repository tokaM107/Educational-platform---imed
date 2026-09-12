"""Internal exam-grading endpoints: criteria at publish, evaluation at submit.

Neither is reachable by a student, or by a signed-in user at all. NestJS calls
both with the shared `INTERNAL_API_KEY`: criteria generation happens while a
teacher publishes, and evaluation happens from a background worker after the
student has gone, so in neither case is there a token to forward.

The separation is the architecture, not a tidying. Generating criteria per
student meant the grading standard could differ between two people answering
the same question, and it put a model call between pressing submit and seeing a
result. Evaluation here takes criteria as input and has no branch that makes
them: missing criteria is a preparation failure the publish step is supposed to
have caught.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.config import get_settings
from app.schemas.essay_grading import Criterion
from app.schemas.exam_grading import (
    CriteriaRequest, CriteriaResponse, EvaluationItem, EvaluationRequest,
    EvaluationResponse, EvaluationResult,
)
from app.services.essay_grading import EssayGradingService, GradingStageError
from app.services.essay_scoring import IncompleteEvaluation, calculate_score

logger = logging.getLogger(__name__)

# One attempt's essays are evaluated together, but each is its own model call,
# so they run concurrently. The cap stops a twenty-essay attempt opening twenty
# simultaneous requests and having the provider throttle all of them.
MAX_CONCURRENCY = 4

# Per-essay ceiling. Evaluation is now a SINGLE model call — criteria were
# generated at publish — so this is generous rather than tight.
PER_ITEM_TIMEOUT_SECONDS = 15.0

# Criteria generation is off the student's path, so it may take longer.
CRITERIA_TIMEOUT_SECONDS = 40.0

_CREDITED = {"yes", "partial"}


def require_internal_caller(x_internal_key: str = Header(default="")):
    """Constant-time check of the service-to-service secret.

    `compare_digest` rather than `==`: a plain comparison returns as soon as
    two bytes differ, and how long it took leaks how much of the key was right.
    """

    expected = get_settings().require_internal_api_key()
    if not x_internal_key or not hmac.compare_digest(x_internal_key, expected):
        raise HTTPException(status_code=401, detail="Invalid internal credential")


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/internal/exam-grading",
    tags=["Internal exam grading"],
    dependencies=[Depends(require_internal_caller), Depends(no_store)],
)


def criteria_hash(criteria: list[Criterion]) -> str:
    """A content address for one grading standard.

    Over the claims in order, not the whole model response: the identifiers are
    positional and the prose around them is not what a student is marked
    against. Two attempts showing the same value were marked to the same
    standard, which is what makes fairness checkable rather than asserted.
    """

    payload = json.dumps(
        [{"id": c.id, "claim": c.claim} for c in criteria],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reference_material(data: CriteriaRequest) -> str:
    """What the criteria are derived from, best material first.

    The marking guide leads because it is the teacher saying how marks are
    split; the model answer is what a full answer looks like. With neither,
    the question alone has to carry it and the result is flagged for review.
    """

    parts = [
        part for part in (data.marking_guide, data.model_answer, data.teacher_instructions)
        if part and part.strip()
    ]
    return "\n\n".join(parts).strip()


@router.post("/criteria", response_model=CriteriaResponse)
async def generate_criteria(data: CriteriaRequest) -> CriteriaResponse:
    """Freeze the grading standard for one essay question. Runs at publish."""

    started = time.monotonic()
    service = EssayGradingService()
    reference = _reference_material(data)
    thin = not reference

    try:
        output = await asyncio.wait_for(
            service.generate_criteria(data.question, reference or data.question),
            timeout=CRITERIA_TIMEOUT_SECONDS,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return CriteriaResponse(
            status="failed", elapsed_ms=int((time.monotonic() - started) * 1000),
            error="criteria generation timed out",
        )
    except GradingStageError as error:
        return CriteriaResponse(
            status="failed", elapsed_ms=int((time.monotonic() - started) * 1000),
            error=str(error),
        )

    parsed = output.parsed
    criteria = list(parsed.criteria)
    if not criteria:
        return CriteriaResponse(
            status="failed", elapsed_ms=int((time.monotonic() - started) * 1000),
            error="the grader returned no criteria",
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    digest = criteria_hash(criteria)
    logger.info(
        "essay_criteria_generated criteria=%s hash=%s thin_material=%s elapsed_ms=%s",
        len(criteria), digest[:12], thin, elapsed_ms,
    )
    return CriteriaResponse(
        status="ready", criteria=criteria, criteria_hash=digest,
        needs_review=bool(parsed.needs_review) or thin,
        review_reason=parsed.review_reason or ("no model answer or marking guide" if thin else None),
        model_identifier=output.metadata.model_identifier,
        prompt_version=output.metadata.prompt_version,
        elapsed_ms=elapsed_ms,
    )


def _feedback_from(criteria: list[Criterion], scoring) -> str:
    """A short, student-facing rationale built from the scored criteria.

    Assembled from the deterministic breakdown rather than asked of the model,
    so the words cannot drift from the number, and named with the grader's own
    wording because `C1` means nothing to a student. No chain of thought is
    kept — only which claims were credited.
    """

    claims = {c.id: c.claim for c in criteria}
    met, missed = [], []
    for contribution in scoring.score_breakdown:
        label = claims.get(contribution.criterion_id, contribution.criterion_id)
        (met if contribution.status.value in _CREDITED else missed).append(label)

    parts = []
    if met:
        parts.append("تناولت: " + "، ".join(met[:5]))
    if missed:
        parts.append("ينقص: " + "، ".join(missed[:5]))
    return " — ".join(parts) if parts else "تم تقييم الإجابة."


async def _evaluate_one(
    service: EssayGradingService, item: EvaluationItem, gate: asyncio.Semaphore,
) -> EvaluationResult:
    async with gate:
        if not item.student_answer.strip():
            # An unanswered essay needs no model call. Zero is the honest mark
            # for nothing written, and it is the only zero this path awards.
            return EvaluationResult(
                reference=item.reference, status="graded", awarded_score=Decimal("0"),
                max_score=item.max_score, feedback="لم تُكتب إجابة.",
                criteria_hash=item.criteria_hash,
            )

        try:
            output = await asyncio.wait_for(
                service.evaluate_student_answer(
                    item.question, list(item.criteria), item.student_answer
                ),
                timeout=PER_ITEM_TIMEOUT_SECONDS,
            )
            scoring = calculate_score(
                list(item.criteria), output.parsed, item.max_score,
                # The evaluator's own doubt about this answer. Criteria-level
                # doubt was settled at publish and is the caller's to remember.
                needs_review=bool(output.parsed.needs_review),
            )
        except (TimeoutError, asyncio.TimeoutError):
            return EvaluationResult(
                reference=item.reference, status="failed", max_score=item.max_score,
                needs_review=True, criteria_hash=item.criteria_hash,
                error="evaluation timed out",
            )
        except (GradingStageError, IncompleteEvaluation, ValueError) as error:
            return EvaluationResult(
                reference=item.reference, status="failed", max_score=item.max_score,
                needs_review=True, criteria_hash=item.criteria_hash, error=str(error),
            )

        awarded = Decimal(scoring.score)
        # Clamped as well as derived. The scorer computes the mark
        # arithmetically so it should already be in range; a bound checked in
        # only one place is a bound that eventually is not.
        awarded = max(Decimal("0"), min(awarded, item.max_score))

        return EvaluationResult(
            reference=item.reference, status="graded", awarded_score=awarded,
            max_score=item.max_score,
            feedback=_feedback_from(list(item.criteria), scoring),
            needs_review=bool(scoring.needs_review),
            criteria_hash=item.criteria_hash,
        )


@router.post("/evaluate", response_model=EvaluationResponse)
async def evaluate_attempt(data: EvaluationRequest) -> EvaluationResponse:
    """Mark every essay on one attempt against its frozen criteria."""

    started = time.monotonic()
    service = EssayGradingService()
    gate = asyncio.Semaphore(MAX_CONCURRENCY)

    results = await asyncio.gather(
        *(_evaluate_one(service, item, gate) for item in data.items)
    )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    graded = sum(1 for r in results if r.status == "graded")
    logger.info(
        "essay_evaluation_completed attempt_id=%s items=%s graded=%s failed=%s elapsed_ms=%s",
        data.attempt_id, len(results), graded, len(results) - graded, elapsed_ms,
    )
    return EvaluationResponse(
        attempt_id=data.attempt_id, results=list(results), elapsed_ms=elapsed_ms,
    )
