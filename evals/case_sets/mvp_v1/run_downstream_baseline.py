"""Run the MVP v1 downstream gold set entirely offline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from casefile_ai.evaluation.downstream import run_downstream_evaluation

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    summary = run_downstream_evaluation(
        ROOT / "downstream_case_set.json", ROOT / "downstream_questions.jsonl",
        ROOT / "downstream_gold.json", arguments.output,
    )
    print(json.dumps({
        "results": str(arguments.output / "downstream_results.jsonl"),
        "summary": str(arguments.output / "downstream_summary.json"),
        "records": summary["pipeline"]["record_count"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
