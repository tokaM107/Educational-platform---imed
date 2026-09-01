"""Run the synthetic essay-grading fixture against the configured Gemini models."""

import argparse
import asyncio
import json

from app.services.essay_dataset import evaluate_dataset, save_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path", help="optional JSON report path")
    parser.add_argument("--csv", dest="csv_path", help="optional CSV report path")
    args = parser.parse_args()
    report = asyncio.run(evaluate_dataset())
    save_report(report, args.json_path, args.csv_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
