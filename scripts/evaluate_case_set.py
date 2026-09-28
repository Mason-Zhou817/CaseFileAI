"""CLI for isolated case-set evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from casefile_ai.evaluation.runner import EvaluationInputError, run_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-set", required=True, type=Path)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        summary = run_evaluation(arguments.case_set, arguments.questions, arguments.output)
    except EvaluationInputError as exc:
        parser.error(str(exc))
    print(json.dumps({"summary": str(arguments.output / "summary.json"),
                      "total_questions": summary["total_questions"],
                      "failure_count": summary["failure_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
