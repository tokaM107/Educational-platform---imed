"""Internal endpoint: grade every essay answer on one exam attempt.

Not reachable by a student, and not by a signed-in user at all. NestJS calls
this from a background worker after an attempt is submitted, so there is no
request-scoped user to authorise — the caller proves itself with the shared
`INTERNAL_API_KEY` instead. That is also the point: a student must never be
able to ask for a grade, because the answer would be a score.

The grading itself is `EssayGradingService`, unchanged. This module only
batches, bounds and reports it.
"""

import asyncio
import hmac
import logging
import time
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.config import get_settings
from app.schemas.exam_grading import (
    EssayGradingItem, EssayGradingResult, ExamGradingRequest, ExamGradingResponse,
)
from app.services.essay_grading import EssayGradingService, GradingStageError
from app.services.essay_scoring import IncompleteEvaluation

logger = logging.getLogger(__name__)

# One attempt's essays are graded together, but each is its own pair of model
# calls, so they run concurrently. The cap keeps a twenty-essay attempt from
# opening twenty simultaneous model requests and having the provider throttle
# all of them.
MAX_CONCURRENCY = 4

# Per-essay ceiling. The product budget is 20s for grading inside a 30s
# end-to-end target; leaving headroom for the caller's own persistence, an
# essay that has not come back by this point is reported as needing review
# rather than holding the attempt open indefinitely.
PER_ITEM_TIMEOUT_SECONDS = 18.0


def require_internal_caller(x_internal_key: str = Header(default="")):
    """Constant-time check of the service-to-service secret.

    `compare_digest` rather than `==`: a plain comparison returns as soon as
    two bytes differ, and the time it takes leaks how much of the key was
    right — the same reasoning as the Bunny webhook check.
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


# `yes` is full credit and `partial` is half; `no` and `contradicted` earn
# nothing. Mirrors STATUS_FACTORS in essay_scoring rather than restating it as
# a second opinion about what counts as answered.
_CREDITED = {"yes", "partial"}


def _feedback_from(result) -> str:
    """A short, student-facing rationale built from the scored criteria.

    Assembled from the deterministic breakdown rather than asked of the model:
    the breakdown is what the mark was actually computed from, so the words
    cannot drift from the number. It names the criteria in the grader's own
    wording — `claim` — because `criterion_id` is "C1" and means nothing to a
    student. No chain of thought is kept; only which claims were credited.
    """

    scoring = result.deterministic_scoring
    parsed = getattr(result.criteria_model, "parsed_response", None)
    claims = {c.id: c.claim for c in getattr(parsed, "criteria", []) or []}

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


async def _grade_one(
    service: EssayGradingService, item: EssayGradingItem, gate: asyncio.Semaphore,
) -> EssayGradingResult:
    async with gate:
        blank = not item.student_answer.strip()
        if blank:
            # An unanswered essay is not a grading failure and does not need a
            # model call. Zero is the honest mark for nothing written, and it
            # is the one zero this pipeline is allowed to award.
            return EssayGradingResult(
                reference=item.reference, status="graded",
                awarded_score=Decimal("0"), max_score=item.max_points,
                feedback="لم تُكتب إجابة.", needs_review=False,
            )

        # Without a model answer the grader has only the question to go on.
        # It still grades, and the result is flagged for a human.
        reference_answer = (item.model_answer or "").strip()
        guide = "\n\n".join(
            part for part in (item.marking_guide, item.teacher_instructions) if part
        )
        if guide:
            reference_answer = f"{reference_answer}\n\n{guide}".strip()
        thin = not reference_answer

        try:
            result = await asyncio.wait_for(
                service.grade(
                    item.question,
                    reference_answer or item.question,
                    item.student_answer,
                    item.max_points,
                ),
                timeout=PER_ITEM_TIMEOUT_SECONDS,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return EssayGradingResult(
                reference=item.reference, status="failed", max_score=item.max_points,
                needs_review=True, error="grading timed out",
            )
        except (GradingStageError, IncompleteEvaluation) as error:
            return EssayGradingResult(
                reference=item.reference, status="failed", max_score=item.max_points,
                needs_review=True, error=str(error),
            )

        scoring = result.deterministic_scoring
        if result.run_status != "completed" or scoring is None:
            return EssayGradingResult(
                reference=item.reference, status="failed", max_score=item.max_points,
                needs_review=True, error=result.error or "grading did not complete",
            )

        awarded = Decimal(scoring.score)
        # Clamped here as well as validated by the caller. The scorer derives
        # the mark arithmetically so it should already be in range; a bound
        # that is only checked in one place is a bound that eventually is not.
        awarded = max(Decimal("0"), min(awarded, item.max_points))

        return EssayGradingResult(
            reference=item.reference, status="graded", awarded_score=awarded,
            max_score=item.max_points, feedback=_feedback_from(result),
            needs_review=bool(scoring.needs_review) or thin,
        )


@router.post("", response_model=ExamGradingResponse)
async def grade_attempt(data: ExamGradingRequest) -> ExamGradingResponse:
    started = time.monotonic()
    service = EssayGradingService()
    gate = asyncio.Semaphore(MAX_CONCURRENCY)

    results = await asyncio.gather(
        *(_grade_one(service, item, gate) for item in data.items)
    )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    graded = sum(1 for r in results if r.status == "graded")
    logger.info(
        "essay_grading_completed attempt_id=%s items=%s graded=%s failed=%s elapsed_ms=%s",
        data.attempt_id, len(results), graded, len(results) - graded, elapsed_ms,
    )
    return ExamGradingResponse(
        attempt_id=data.attempt_id, results=list(results), elapsed_ms=elapsed_ms,
    )
