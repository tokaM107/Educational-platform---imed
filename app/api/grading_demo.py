"""Opt-in endpoints for the isolated essay-grading engineering prototype."""

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import require_doctor
from app.schemas.essay_grading import (
    EvaluateAnswerRequest, GenerateCriteriaRequest, GradeRequest,
)
from app.services.essay_dataset import evaluate_dataset, load_dataset
from app.services.essay_grading import EssayGradingService, GradingStageError
from app.services import rate_limit


def prevent_sensitive_response_caching(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/grading-demo",
    tags=["Grading demo"],
    dependencies=[
        Depends(require_doctor),
        Depends(prevent_sensitive_response_caching),
    ],
)


@lru_cache
def get_grading_service():
    return EssayGradingService()


@router.get("/dataset")
def dataset():
    return load_dataset()


@router.post("/generate-criteria")
async def generate_criteria(
    data: GenerateCriteriaRequest,
    service=Depends(get_grading_service),
):
    try:
        result = await service.generate_criteria(data.question, data.model_answer)
        return result.metadata
    except GradingStageError as error:
        raise HTTPException(status_code=502, detail=error.stage.model_dump(mode="json"))


@router.post("/evaluate-answer")
async def evaluate_answer(
    data: EvaluateAnswerRequest,
    service=Depends(get_grading_service),
):
    try:
        result = await service.evaluate_student_answer(
            data.question, data.criteria, data.student_answer
        )
        return result.metadata
    except GradingStageError as error:
        raise HTTPException(status_code=502, detail=error.stage.model_dump(mode="json"))


@router.post("/grade")
async def grade(data: GradeRequest, service=Depends(get_grading_service)):
    return await service.grade(
        data.question, data.model_answer, data.student_answer, data.max_points
    )


@router.post("/evaluate-dataset")
async def run_dataset(
    service=Depends(get_grading_service),
    current_user=Depends(require_doctor),
):
    try:
        rate_limit.check(
            f"grading-dataset:{current_user['id']}", limit=2,
            window_seconds=600,
        )
    except rate_limit.RateLimited as error:
        raise HTTPException(
            status_code=429,
            detail="Dataset evaluation limit reached. Try again later.",
            headers={"Retry-After": str(error.retry_after)},
        )
    return await evaluate_dataset(service=service)
