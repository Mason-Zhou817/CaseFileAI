"""Run the synthetic MVP v1 set with local BGE and an explicit FakeLLM."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from casefile_ai.evaluation.gold_fake import GoldEvidenceFakeLLM
from casefile_ai.evaluation.runner import _read_inputs, run_evaluation

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    case_set = ROOT / "case_set.json"
    questions = ROOT / "questions.jsonl"
    spec, items, _ = _read_inputs(case_set, questions)
    if spec.allow_cloud_model:
        raise RuntimeError("synthetic baseline must forbid cloud QA")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    summary = run_evaluation(case_set, questions, arguments.output,
                             llm=GoldEvidenceFakeLLM(items))
    print(json.dumps({
        "results": str(arguments.output / "results.jsonl"),
        "summary": str(arguments.output / "summary.json"),
        "questions": summary["total_questions"],
        "failures": summary["failure_count"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
