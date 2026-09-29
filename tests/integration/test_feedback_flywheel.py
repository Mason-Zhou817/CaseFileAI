"""Synthetic feedback lifecycle through CLI and the existing offline runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_cli_workflow import FakeLLM, cli_env, invoke, pdf  # noqa: F401

from casefile_ai.evaluation.runner import run_evaluation


class Embedding:
    model_version = "feedback-test-embedding"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class LocalFakeLLM:
    model_version = "feedback-local-fake"

    def generate_structured(
        self, *, system_prompt: str, user_prompt: str, response_model: type[Any]
    ) -> Any:
        return FakeLLM().generate_structured(
            system_prompt=system_prompt, user_prompt=user_prompt, response_model=response_model
        )


def test_feedback_to_offline_regression(
    cli_env: tuple[Path, type[FakeLLM]],  # noqa: F811
    pdf: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    case = invoke("case", "create", "合成反馈案件")[1]["data"]["case_id"]
    other = invoke("case", "create", "其他案件")[1]["data"]["case_id"]
    assert invoke("document", "import", "--case", case, str(pdf))[0] == 0
    assert invoke("index", "build", "--case", case)[0] == 0
    answer = invoke("ask", "--case", case, "交易金额是多少？")[1]["data"]
    answer_id = answer["answer_id"]

    assert (
        invoke(
            "feedback",
            "add",
            "--case",
            case,
            "--answer",
            answer_id,
            "--rating",
            "bad",
            "--category",
            "citation",
        )[1]["error"]["code"]
        == "invalid_rating"
    )
    assert (
        invoke(
            "feedback",
            "add",
            "--case",
            case,
            "--answer",
            answer_id,
            "--rating",
            "incorrect",
            "--category",
            "bad",
        )[1]["error"]["code"]
        == "invalid_category"
    )
    assert (
        invoke(
            "feedback",
            "add",
            "--case",
            case,
            "--answer",
            "missing",
            "--rating",
            "incorrect",
            "--category",
            "citation",
        )[1]["error"]["code"]
        == "answer_not_found"
    )
    assert (
        invoke(
            "feedback",
            "add",
            "--case",
            other,
            "--answer",
            answer_id,
            "--rating",
            "incorrect",
            "--category",
            "citation",
        )[1]["error"]["code"]
        == "cross_case_answer"
    )

    added = invoke(
        "feedback",
        "add",
        "--case",
        case,
        "--answer",
        answer_id,
        "--rating",
        "incorrect",
        "--category",
        "citation",
        "--note",
        "合成引用页码错误",
    )[1]["data"]
    feedback_id = added["feedback_id"]
    assert added["review_status"] == "pending"
    assert (
        invoke(
            "feedback",
            "add",
            "--case",
            case,
            "--answer",
            answer_id,
            "--rating",
            "correct",
            "--category",
            "answer",
        )[0]
        == 0
    )
    rows = invoke("feedback", "list", "--case", case)[1]["data"]
    assert len(rows) == 2
    summary = invoke("feedback", "summary", "--case", case)[1]["data"]
    assert summary["total"] == 2
    assert summary["category"]["citation"] == 1
    assert summary["incorrect_by_category"]["citation"] == 1
    assert summary["rating"]["correct"] == 1

    pending_path = tmp_path / "feedback-review.jsonl"
    assert invoke("feedback", "export", "--case", case, "--output", str(pending_path))[0] == 0
    pending = json.loads(pending_path.read_text(encoding="utf-8").splitlines()[0])
    assert pending["question"] == "交易金额是多少？"
    assert pending["answer"] and pending["review_status"] == "pending"
    assert pending["citations"]

    case_set = tmp_path / "case-set.json"
    case_set.write_text(
        json.dumps(
            {
                "name": "合成反馈回归",
                "documents": [{"doc_key": "transaction", "path": str(pdf)}],
                "allow_cloud_model": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    staging = tmp_path / "feedback-approved-eval.jsonl"
    assert (
        invoke(
            "feedback",
            "export-eval",
            "--case",
            case,
            "--case-set",
            str(case_set),
            "--output",
            str(staging),
        )[1]["error"]["code"]
        == "no_approved_feedback"
    )
    assert not staging.exists()
    assert (
        invoke("feedback", "review", "--feedback", feedback_id, "--status", "approved")[1]["error"][
            "code"
        ]
        == "incomplete_gold"
    )

    evidence_path = tmp_path / "expected-evidence.json"
    evidence_path.write_text(
        json.dumps([{"doc_key": "transaction", "page": 1, "snippet": "450000"}]), encoding="utf-8"
    )
    approved = invoke(
        "feedback",
        "review",
        "--feedback",
        feedback_id,
        "--status",
        "approved",
        "--expected-status",
        "answered",
        "--expected-answer",
        "450000",
        "--expected-evidence",
        str(evidence_path),
    )[1]["data"]
    assert approved["review_status"] == "approved"
    remaining = next(row["feedback_id"] for row in rows if row["feedback_id"] != feedback_id)
    assert (
        invoke("feedback", "review", "--feedback", remaining, "--status", "rejected")[1]["data"][
            "review_status"
        ]
        == "rejected"
    )
    assert (
        invoke(
            "feedback",
            "export-eval",
            "--case",
            case,
            "--case-set",
            str(case_set),
            "--output",
            str(staging),
        )[1]["data"]["count"]
        == 1
    )
    sample = json.loads(staging.read_text(encoding="utf-8").strip())
    assert sample["question"] == "交易金额是多少？"
    assert sample["gold_evidence"][0]["doc_key"] == "transaction"
    report = run_evaluation(
        case_set, staging, tmp_path / "regression", llm=LocalFakeLLM(), embeddings=Embedding()
    )
    assert report["total_questions"] == 1
    assert report["failure_count"] == 0
