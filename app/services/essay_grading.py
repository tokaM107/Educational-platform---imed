"""Two-stage LLM orchestration for the isolated essay-grading prototype."""

import asyncio
import copy
import json
import time
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from app.config import get_settings
from app.schemas.essay_grading import (
    AnswerEvaluationResult, CriteriaGenerationResult, Criterion, GradeResponse,
    ModelStageResult, Usage,
)
from app.services.essay_grading_prompts import (
    CRITERIA_PROMPT_VERSION, CRITERIA_SYSTEM_INSTRUCTION,
    EVALUATOR_PROMPT_VERSION, EVALUATOR_SYSTEM_INSTRUCTION,
    build_criteria_prompt, build_evaluator_prompt,
)
from app.services.essay_scoring import (
    IncompleteEvaluation, calculate_score, ensure_complete,
)
from app.services.llm import ChatModel


class GradingStageError(RuntimeError):
    def __init__(self, message: str, stage: ModelStageResult):
        super().__init__(message)
        self.stage = stage


@dataclass
class StageOutput:
    parsed: CriteriaGenerationResult | AnswerEvaluationResult
    metadata: ModelStageResult


def provider_response_schema(schema):
    """Return Gemini-compatible JSON Schema without weakening local validation.

    Pydantic's ``extra='forbid'`` correctly emits ``additionalProperties: false``.
    Gemini Developer API's OpenAPI subset rejects that keyword, including in
    nested definitions. The provider gets the supported structural subset; its
    response is still parsed afterward by the original strict Pydantic model.
    """

    provider_schema = copy.deepcopy(schema.model_json_schema())

    def strip_unsupported(value):
        if isinstance(value, dict):
            value.pop("additionalProperties", None)
            value.pop("additional_properties", None)
            for child in value.values():
                strip_unsupported(child)
        elif isinstance(value, list):
            for child in value:
                strip_unsupported(child)

    strip_unsupported(provider_schema)
    return provider_schema


class EssayGradingService:
    def __init__(self, settings=None, llm=None):
        self.settings = settings or get_settings()
        self.llm = llm or ChatModel(self.settings)

    async def _run_stage(self, *, system, prompt, schema, model, version):
        started = time.perf_counter()
        validation_errors = []
        last_reply = None
        for parse_attempt in range(2):
            try:
                reply = await asyncio.to_thread(
                    self.llm.generate_with_metadata,
                    system_instruction=system,
                    user_prompt=prompt,
                    response_schema=provider_response_schema(schema),
                    model_name=model,
                    temperature=0,
                    max_output_tokens=8192,
                )
                last_reply = reply
                if isinstance(reply.parsed, schema):
                    parsed = reply.parsed
                elif isinstance(reply.parsed, str):
                    parsed = schema.model_validate_json(reply.parsed, strict=True)
                else:
                    parsed = schema.model_validate_json(
                        json.dumps(reply.parsed), strict=True
                    )
                metadata = ModelStageResult(
                    model_identifier=reply.model_name,
                    prompt_version=version,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    usage=Usage(
                        input_tokens=reply.input_tokens,
                        output_tokens=reply.output_tokens,
                    ),
                    raw_response=reply.raw_response,
                    parsed_response=parsed,
                    retry_count=reply.retry_count + parse_attempt,
                    retry_errors=list(reply.retry_errors) + validation_errors,
                )
                return StageOutput(parsed, metadata)
            except ValidationError as error:
                validation_errors.append(f"structured_validation:{error.title}")
            except Exception as error:
                metadata = ModelStageResult(
                    model_identifier=(last_reply.model_name if last_reply else model),
                    prompt_version=version,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    usage=Usage(
                        input_tokens=(last_reply.input_tokens if last_reply else None),
                        output_tokens=(last_reply.output_tokens if last_reply else None),
                    ),
                    raw_response=(last_reply.raw_response if last_reply else None),
                    parsed_response=None,
                    retry_count=(last_reply.retry_count if last_reply else 0),
                    retry_errors=(
                        list(last_reply.retry_errors) if last_reply else []
                    ),
                    error=f"{type(error).__name__}: {error}",
                )
                raise GradingStageError("LLM stage failed", metadata) from error

        metadata = ModelStageResult(
            model_identifier=(last_reply.model_name if last_reply else model),
            prompt_version=version,
            latency_ms=round((time.perf_counter() - started) * 1000),
            usage=Usage(
                input_tokens=(last_reply.input_tokens if last_reply else None),
                output_tokens=(last_reply.output_tokens if last_reply else None),
            ),
            raw_response=(last_reply.raw_response if last_reply else None),
            parsed_response=None,
            retry_count=1,
            retry_errors=validation_errors,
            error="Malformed structured response",
        )
        raise GradingStageError("Malformed structured response", metadata)

    async def generate_criteria(self, question: str, model_answer: str):
        output = await self._run_stage(
            system=CRITERIA_SYSTEM_INSTRUCTION,
            prompt=build_criteria_prompt(question, model_answer),
            schema=CriteriaGenerationResult,
            model=self.settings.essay_criteria_model,
            version=CRITERIA_PROMPT_VERSION,
        )
        if not output.parsed.criteria:
            output.metadata.error = "Criteria generation returned no criteria"
            raise GradingStageError(output.metadata.error, output.metadata)
        return output

    async def evaluate_student_answer(
        self, question: str, criteria: list[Criterion], student_answer: str
    ):
        output = await self._run_stage(
            system=EVALUATOR_SYSTEM_INSTRUCTION,
            prompt=build_evaluator_prompt(question, criteria, student_answer),
            schema=AnswerEvaluationResult,
            model=self.settings.essay_evaluator_model,
            version=EVALUATOR_PROMPT_VERSION,
        )
        try:
            ensure_complete(criteria, output.parsed)
        except IncompleteEvaluation as error:
            output.metadata.error = str(error)
            raise GradingStageError(str(error), output.metadata) from error
        return output

    async def grade(
        self, question: str, model_answer: str, student_answer: str,
        max_points: Decimal,
    ) -> GradeResponse:
        try:
            criteria = await self.generate_criteria(question, model_answer)
        except GradingStageError as error:
            return GradeResponse(
                run_status="grading_failed", criteria_model=error.stage,
                evaluator_model=None, deterministic_scoring=None, error=str(error),
            )

        try:
            evaluation = await self.evaluate_student_answer(
                question, criteria.parsed.criteria, student_answer
            )
            scoring = calculate_score(
                criteria.parsed.criteria,
                evaluation.parsed,
                max_points,
                needs_review=criteria.parsed.needs_review,
            )
        except GradingStageError as error:
            return GradeResponse(
                run_status="grading_failed", criteria_model=criteria.metadata,
                evaluator_model=error.stage, deterministic_scoring=None,
                error=str(error),
            )
        except IncompleteEvaluation as error:
            evaluation.metadata.error = str(error)
            return GradeResponse(
                run_status="grading_failed", criteria_model=criteria.metadata,
                evaluator_model=evaluation.metadata, deterministic_scoring=None,
                error=str(error),
            )

        return GradeResponse(
            run_status="completed", criteria_model=criteria.metadata,
            evaluator_model=evaluation.metadata, deterministic_scoring=scoring,
        )


async def generate_criteria(question: str, model_answer: str):
    return (await EssayGradingService().generate_criteria(question, model_answer)).parsed


async def evaluate_student_answer(
    question: str, criteria: list[Criterion], student_answer: str
):
    return (
        await EssayGradingService().evaluate_student_answer(
            question, criteria, student_answer
        )
    ).parsed
