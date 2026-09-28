"""Run the synthetic stress set offline; opt in explicitly for cloud smoke QA."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from casefile_ai.evaluation.runner import (
    EvaluationInputError,
    Question,
    _read_inputs,
    run_evaluation,
)
from casefile_ai.evaluation.stress import run_stress_evaluation

HERE = Path(__file__).resolve().parent
SMOKE_GROUPS = ("smoke_regular", "smoke_table", "smoke_paraphrase", "smoke_abstain")


def verify_real_smoke_inputs() -> list[Question]:
    """Allow only the reviewed, hash-pinned synthetic PDFs and the 20 smoke questions."""
    offline, questions, paths = _read_inputs(HERE / "case_set.json", HERE / "questions.jsonl")
    online, online_questions, online_paths = _read_inputs(
        HERE / "real_model_case_set.json", HERE / "questions.jsonl"
    )
    if offline.allow_cloud_model or not online.allow_cloud_model:
        raise EvaluationInputError("real-model smoke requires explicit allow_cloud_model=true")
    if offline.documents != online.documents or paths != online_paths:
        raise EvaluationInputError("real-model smoke must use exactly the pinned stress PDFs")
    if any(path.parent != (HERE / "documents").resolve() for path in paths.values()):
        raise EvaluationInputError("real-model smoke accepts only bundled synthetic PDFs")
    if any(document.expected_sha256 is None for document in online.documents):
        raise EvaluationInputError("real-model smoke requires pinned document hashes")
    baseline_path = HERE.parents[2] / "docs/evaluation/baselines/mvp-stress-v1-2026-09-25.json"
    try:
        pinned = json.loads(baseline_path.read_text(encoding="utf-8"))["inputs"]["documents"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvaluationInputError("real-model smoke requires the committed hash baseline") from exc
    if {item.doc_key: item.expected_sha256 for item in online.documents} != pinned:
        raise EvaluationInputError("real-model smoke PDFs differ from the committed baseline")
    if questions != online_questions:
        raise EvaluationInputError("real-model smoke questions differ from the reviewed set")
    selected = [question for question in questions
                if any(tag in question.tags for tag in SMOKE_GROUPS)]
    if len(selected) != 20 or any(
        sum(tag in question.tags for question in selected) != 5 for tag in SMOKE_GROUPS
    ):
        raise EvaluationInputError("real-model smoke needs 5 questions in each of 4 groups")
    return selected


def run_real_smoke(output_dir: Path) -> dict[str, object]:
    """Invoke the normal runner only after explicit CLI selection and input checks."""
    selected = verify_real_smoke_inputs()
    with tempfile.TemporaryDirectory(prefix="casefile-real-smoke-") as directory:
        temporary = Path(directory)
        questions_path = temporary / "questions.jsonl"
        questions_path.write_text("".join(
            question.model_dump_json() + "\n" for question in selected
        ), encoding="utf-8")
        reports = temporary / "reports"
        summary = run_evaluation(HERE / "real_model_case_set.json", questions_path, reports)
        rows = [json.loads(line) for line in (reports / "results.jsonl").read_text(
            encoding="utf-8").splitlines()]
        answerable = [row for row in rows if row["expected_status"] == "answered"]
        negatives = [row for row in rows
                     if row["expected_status"] == "insufficient_evidence"]
        provider_failures = [row for row in rows if row["failure_stage"] == "qa"]
        summary["real_model_smoke"] = {
            "explicit_opt_in": True,
            "synthetic_pinned_materials": True,
            "answered": sum(row["actual_status"] == "answered" for row in rows),
            "abstained": sum(row["actual_status"] == "insufficient_evidence" for row in rows),
            "false_abstain_ids": [row["id"] for row in answerable
                                  if row["actual_status"] == "insufficient_evidence"],
            "false_answer_ids": [row["id"] for row in negatives
                                 if row["actual_status"] == "answered"],
            "provider_failure_ids": [row["id"] for row in provider_failures],
            "provider_503_ids": [row["id"] for row in provider_failures
                                 if "503" in (row["failure_reason"] or "")],
            "group_counts": {tag: sum(tag in row["tags"] for row in rows)
                             for tag in SMOKE_GROUPS},
            "citation_document_accuracy": summary["citation_document_accuracy"],
            "citation_page_accuracy": summary["citation_page_accuracy"],
            "citation_snippet_accuracy": summary["citation_snippet_accuracy"],
            "latency_ms": summary["latency_ms"],
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(reports / "results.jsonl", output_dir / "real_model_results.jsonl")
        (output_dir / "real_model_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(".eval-results/mvp-stress-v1"))
    parser.add_argument(
        "--real-model", action="store_true",
        help="explicitly send only the bundled synthetic smoke set to configured QA",
    )
    args = parser.parse_args()
    try:
        if args.real_model:
            summary = run_real_smoke(args.output)
            report = args.output / "real_model_summary.json"
        else:
            summary = run_stress_evaluation(
                HERE / "case_set.json", HERE / "questions.jsonl",
                HERE / "downstream_gold.json", HERE / "stress_gold.json", args.output,
            )
            report = args.output / "summary.json"
    except EvaluationInputError as exc:
        parser.error(str(exc))
    print(json.dumps({"summary": str(report), "total_questions": summary["total_questions"],
                      "failure_count": summary["failure_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
