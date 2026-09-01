"""Reusable evaluation runner for the synthetic essay-grading fixture."""

import csv
import json
import time
from decimal import Decimal
from pathlib import Path

from app.config import BASE_DIR
from app.services.essay_grading import EssayGradingService, GradingStageError
from app.services.essay_scoring import calculate_score


DATASET_PATH = BASE_DIR / "app" / "fixtures" / "essay_grading_dataset.json"


def load_dataset(path=DATASET_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _stage(stage):
    return stage.model_dump(mode="json") if stage else None


async def evaluate_dataset(service=None, path=DATASET_PATH):
    service = service or EssayGradingService()
    dataset = load_dataset(path)
    rows = []
    latencies = []

    for question in dataset["questions"]:
        criteria = None
        criteria_error = None
        try:
            criteria = await service.generate_criteria(
                question["question"], question["model_answer"]
            )
        except Exception as error:  # one question must not abort the fixture
            criteria_error = str(error)

        for case in question["student_answers"]:
            started = time.perf_counter()
            row = {
                "question_id": question["question_id"],
                "case_id": case["case_id"],
                "expected_score": case["expected_score"],
                "predicted_score": None,
                "absolute_error": None,
                "needs_review": None,
                "criteria_model_output": _stage(
                    criteria.metadata if criteria else None
                ),
                "evaluator_model_output": None,
                "score_breakdown": None,
                "latency_ms": None,
                "error": criteria_error,
            }
            if criteria is not None:
                try:
                    evaluation = await service.evaluate_student_answer(
                        question["question"], criteria.parsed.criteria, case["answer"]
                    )
                    score = calculate_score(
                        criteria.parsed.criteria, evaluation.parsed,
                        Decimal(question["max_points"]),
                        needs_review=criteria.parsed.needs_review,
                    )
                    row.update(
                        predicted_score=score.score,
                        absolute_error=str(abs(
                            Decimal(score.score) - Decimal(case["expected_score"])
                        )),
                        needs_review=score.needs_review,
                        evaluator_model_output=_stage(evaluation.metadata),
                        score_breakdown=[
                            item.model_dump(mode="json")
                            for item in score.score_breakdown
                        ],
                        error=None,
                    )
                except Exception as error:
                    if isinstance(error, GradingStageError):
                        row["evaluator_model_output"] = _stage(error.stage)
                    row["error"] = str(error)
            row["latency_ms"] = round((time.perf_counter() - started) * 1000)
            latencies.append(row["latency_ms"])
            rows.append(row)

    successful = [row for row in rows if row["predicted_score"] is not None]
    errors = [Decimal(row["absolute_error"]) for row in successful]
    count = Decimal(len(successful))
    metrics = {
        "total_cases": len(rows),
        "successful_cases": len(successful),
        "failed_cases": len(rows) - len(successful),
        "review_required_cases": sum(row["needs_review"] is True for row in rows),
        "mean_absolute_error": str(sum(errors) / count) if successful else None,
        "exact_match_rate": str(Decimal(sum(e == 0 for e in errors)) / count)
        if successful else None,
        "within_0_5_rate": str(Decimal(sum(e <= Decimal("0.5") for e in errors)) / count)
        if successful else None,
        "within_1_0_rate": str(Decimal(sum(e <= Decimal("1.0") for e in errors)) / count)
        if successful else None,
        "average_latency": (
            sum(latencies) / len(latencies) if latencies else None
        ),
    }
    return {"dataset": dataset["description"], "metrics": metrics, "cases": rows}


def save_report(report, json_path=None, csv_path=None):
    if json_path:
        Path(json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if csv_path:
        fields = [
            "question_id", "case_id", "expected_score", "predicted_score",
            "absolute_error", "needs_review", "latency_ms", "error",
            "criteria_model_output", "evaluator_model_output", "score_breakdown",
        ]
        with Path(csv_path).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for case in report["cases"]:
                writer.writerow({
                    field: (
                        json.dumps(case[field], ensure_ascii=False)
                        if isinstance(case[field], (dict, list)) else case[field]
                    )
                    for field in fields
                })
