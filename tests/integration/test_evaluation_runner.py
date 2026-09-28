"""Evaluation reports use real services and synthetic PDFs, with no network QA."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from casefile_ai.application.qa_contracts import (
    ModelAnswerAlignment,
    ModelGroundedAnswer,
    ModelSupportDecision,
)
from casefile_ai.evaluation.runner import EvaluationInputError, _answer_check, run_evaluation
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider

ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT / "tests/fixtures/synthetic_case/01_询问笔录_王海.pdf"


class FakeEmbedding:
    model_version = "evaluation-fake-embedding-v1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "2021年6月1日" in text else [0.0, 1.0]
                for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0] if "日期" in text else [0.0, 1.0]


class BrokenEmbedding(FakeEmbedding):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("injected index failure")


class FakeLLM:
    model_version = "evaluation-fake-qa-v1"

    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_model: type[Any]) -> Any:
        if response_model is ModelAnswerAlignment:
            payload = json.loads(user_prompt)
            return response_model.model_validate({
                "requested_fact": payload["question"], "requested_scope": "synthetic fixture",
                "answers_requested_fact": "matches", "field_matches": "matches",
                "source_scope_matches": "matches", "reason": "scripted fixture alignment",
            })
        if response_model is ModelSupportDecision:
            return response_model.model_validate({"verdict": "supported", "reason": "fixture"})
        assert issubclass(response_model, ModelGroundedAnswer)
        payload = json.loads(user_prompt)
        if "故障" in payload["question"]:
            raise RuntimeError("injected question failure")
        if "租赁" in payload["question"]:
            return response_model.model_validate({
                "status": "insufficient_evidence", "answer": "", "claims": []
            })
        selected = next(item for item in payload["evidence"]
                        if "2021年6月1日" in item["quote"])
        fact = "付款日期为2021年6月1日。"
        return response_model.model_validate({
            "status": "answered", "answer": fact,
            "claims": [{"text": fact, "citation_ids": [selected["citation_id"]],
                        "confidence": 1.0}],
        })


def _inputs(tmp_path: Path, questions: list[dict[str, object]]) -> tuple[Path, Path, Path]:
    config = tmp_path / "case_set.json"
    config.write_text(json.dumps({
        "name": "合成评测", "allow_cloud_model": False, "retrieval_k": 2,
        "documents": [{"doc_key": "interview", "path": str(PDF)}],
    }, ensure_ascii=False), encoding="utf-8")
    questions_path = tmp_path / "questions.jsonl"
    questions_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in questions),
        encoding="utf-8",
    )
    return config, questions_path, tmp_path / "report"


def _answerable(page: int = 1) -> dict[str, object]:
    return {
        "id": "answer", "question": "王海陈述的付款日期是什么时候？",
        "expected_status": "answered", "expected_answer_any": ["2021年6月1日"],
        "gold_evidence": [{"doc_key": "interview", "page": page,
                           "snippet": "2021年6月1日"}], "tags": ["date"],
    }


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text(
        encoding="utf-8").splitlines()]


def test_alignment_rejection_is_reported_as_validation_failure(tmp_path: Path) -> None:
    class MisalignedLLM(FakeLLM):
        def generate_structured(self, *, system_prompt: str, user_prompt: str,
                                response_model: type[Any]) -> Any:
            if response_model is ModelAnswerAlignment:
                return response_model.model_validate({
                    "requested_fact": "payment date", "requested_scope": "interview",
                    "answers_requested_fact": "mismatch", "field_matches": "mismatch",
                    "source_scope_matches": "matches", "reason": "injected adjacent-field answer",
                })
            return super().generate_structured(system_prompt=system_prompt, user_prompt=user_prompt,
                                                response_model=response_model)

    config, questions, output = _inputs(tmp_path, [_answerable()])
    summary = run_evaluation(config, questions, output, llm=MisalignedLLM(),
                             embeddings=FakeEmbedding())
    row = _rows(output)[0]
    assert summary["failure_count"] == 1
    assert row["actual_status"] == "insufficient_evidence" and not row["abstain_correct"]
    assert row["failure_stage"] == "validation"
    assert row["failure_reason"] == "alignment_field_mismatch"
    assert row["citations"] == []


def test_answered_and_no_evidence_reports(tmp_path: Path) -> None:
    negative = {
        "id": "negative", "question": "材料记载的房屋租赁期限是多少？",
        "expected_status": "insufficient_evidence", "expected_answer_any": [],
        "gold_evidence": [], "tags": ["abstain"],
    }
    config, questions, output = _inputs(tmp_path, [_answerable(), negative])
    summary = run_evaluation(config, questions, output, llm=FakeLLM(),
                             embeddings=FakeEmbedding())
    answered, refused = _rows(output)
    assert answered["retrieval_hit"] is True
    assert answered["retrieval_any_hit"] is True
    assert answered["retrieval_all_required_hit"] is True
    assert answered["actual_status"] == "answered"
    assert answered["answer_match"] is True
    assert answered["answer_match_method"] == "normalized_contains_allowed_fact"
    assert answered["citations"][0]["doc_key"] == "interview"
    assert answered["citations"][0]["page"] == 1
    assert answered["citations"][0]["snippet_match"] is True
    assert answered["latency_ms"]["import_ms"] is not None
    assert answered["latency_ms"]["index_ms"] is not None
    assert answered["latency_ms"]["retrieval_ms"] is not None
    assert answered["latency_ms"]["qa_ms"] is not None
    assert answered["run_mode"] == "cold" and answered["cache_hit"] is False
    assert refused["actual_status"] == "insufficient_evidence"
    assert refused["abstain_correct"] is True
    assert refused["retrieval_hit"] is None
    assert refused["retrieval_any_hit"] is None
    assert refused["citations"] == []
    assert summary["retrieval_all_required_hit_at_k"]["denominator"] == 1
    assert summary["correct_abstain_rate"]["denominator"] == 1
    assert summary["answer_status_accuracy"]["numerator"] == 2
    assert json.loads((output / "summary.json").read_text(encoding="utf-8")) == summary


def test_wrong_gold_page_is_scored_without_changing_qa_validation(tmp_path: Path) -> None:
    config, questions, output = _inputs(tmp_path, [_answerable(page=2)])
    summary = run_evaluation(config, questions, output, llm=FakeLLM(),
                             embeddings=FakeEmbedding())
    row = _rows(output)[0]
    assert row["actual_status"] == "answered"
    assert row["validation_status"] == "validated"
    assert row["citations"][0]["document_match"] is True
    assert row["citations"][0]["page_match"] is False
    assert row["failure_stage"] == "evaluation"
    assert row["failure_reason"] == "citation_page_mismatch"
    assert summary["citation_page_accuracy"] == {
        "numerator": 0, "denominator": 1, "value": 0.0
    }


def test_multiple_gold_records_use_all_required_metric(tmp_path: Path) -> None:
    question = _answerable()
    question["gold_evidence"] = [
        {"doc_key": "interview", "page": 1, "snippet": "2021年6月1日"},
        {"doc_key": "interview", "page": 2, "snippet": "不存在的第二条"},
    ]
    config, questions, output = _inputs(tmp_path, [question])
    summary = run_evaluation(config, questions, output, llm=FakeLLM(),
                             embeddings=FakeEmbedding())
    row = _rows(output)[0]
    assert row["retrieval_any_hit"] is True
    assert row["retrieval_all_required_hit"] is False
    assert row["retrieval_hit"] is False
    assert summary["retrieval_any_hit_at_k"]["value"] == 1.0
    assert summary["retrieval_all_required_hit_at_k"]["value"] == 0.0


def test_repeated_question_reports_warm_qa_cache(tmp_path: Path) -> None:
    repeated = {**_answerable(), "id": "repeat"}
    config, questions, output = _inputs(tmp_path, [_answerable(), repeated])
    run_evaluation(config, questions, output, llm=FakeLLM(), embeddings=FakeEmbedding())
    first, second = _rows(output)
    assert first["cache_hit"] is False and first["run_mode"] == "cold"
    assert second["cache_hit"] is True and second["run_mode"] == "warm"
    assert second["qa_versions"]["prompt"].startswith("grounded-qa-citation-alias-v7")


def test_unresolved_answer_is_marked_for_manual_review(tmp_path: Path) -> None:
    question = _answerable()
    question["expected_answer_any"] = ["2021年7月1日"]
    config, questions, output = _inputs(tmp_path, [question])
    summary = run_evaluation(config, questions, output, llm=FakeLLM(),
                             embeddings=FakeEmbedding())
    row = _rows(output)[0]
    assert row["answer_match"] is None
    assert row["manual_review_required"] is True
    assert row["answer_match_method"] == "unresolved_by_deterministic_rules"
    assert summary["manual_review_count"] == 1
    assert summary["answer_match_rate"]["denominator"] == 0


def test_answer_match_does_not_hide_numeric_disagreement() -> None:
    assert _answer_check("金额为1450000元", ["450000元"])[0] is None
    assert _answer_check("金额为455元", ["45.5元"])[0] is None
    assert _answer_check("金额为45.5元", ["45.5元"])[0] is True


def test_single_question_failure_does_not_stop_following_questions(tmp_path: Path) -> None:
    broken = {**_answerable(), "id": "broken", "question": "故障日期是什么？"}
    config, questions, output = _inputs(tmp_path, [broken, _answerable()])
    summary = run_evaluation(config, questions, output, llm=FakeLLM(),
                             embeddings=FakeEmbedding())
    failed, succeeded = _rows(output)
    assert failed["failure_stage"] == "qa"
    assert "QAUnavailableError" in failed["failure_reason"]
    assert succeeded["actual_status"] == "answered"
    assert summary["failure_count"] == 1
    assert summary["total_questions"] == 2


def test_import_failure_records_each_question(tmp_path: Path) -> None:
    config, questions, output = _inputs(tmp_path, [_answerable(), {
        "id": "negative", "question": "材料记载的租期？",
        "expected_status": "insufficient_evidence", "expected_answer_any": [],
        "gold_evidence": [],
    }])
    broken_pdf = tmp_path / "broken.pdf"
    broken_pdf.write_bytes(b"not a PDF")
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["documents"][0]["path"] = str(broken_pdf)
    config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    summary = run_evaluation(config, questions, output, llm=FakeLLM(),
                             embeddings=FakeEmbedding())
    assert [row["failure_stage"] for row in _rows(output)] == ["import", "import"]
    assert summary["failure_count"] == 2
    assert summary["latency_ms"]["index_ms"] is None


def test_index_failure_records_each_question(tmp_path: Path) -> None:
    config, questions, output = _inputs(tmp_path, [_answerable()])
    summary = run_evaluation(config, questions, output, llm=FakeLLM(),
                             embeddings=BrokenEmbedding())
    row = _rows(output)[0]
    assert row["failure_stage"] == "index"
    assert "injected index failure" in row["failure_reason"]
    assert row["latency_ms"]["import_ms"] is not None
    assert row["latency_ms"]["index_ms"] is not None
    assert summary["answer_status_accuracy"]["value"] == 0.0


@pytest.mark.parametrize("invalid", [
    {**_answerable(), "gold_evidence": []},
    {**_answerable(), "expected_status": "bad"},
    {**_answerable(), "expected_answer_any": ["。"]},
    {**_answerable(), "gold_evidence": [{"doc_key": "interview", "page": 1,
                                          "snippet": "："}]},
    {**_answerable(), "gold_evidence": [{"doc_key": "unknown", "page": 1,
                                          "snippet": "x"}]},
])
def test_invalid_question_fails_before_output(tmp_path: Path,
                                              invalid: dict[str, object]) -> None:
    config, questions, output = _inputs(tmp_path, [invalid])
    with pytest.raises(EvaluationInputError, match="questions line 1"):
        run_evaluation(config, questions, output, llm=FakeLLM(),
                       embeddings=FakeEmbedding())
    assert not output.exists()


def test_cloud_is_blocked_by_case_set_before_output(tmp_path: Path) -> None:
    config, questions, output = _inputs(tmp_path, [_answerable()])
    with pytest.raises(EvaluationInputError, match="forbids cloud QA"):
        run_evaluation(config, questions, output, embeddings=FakeEmbedding())
    assert not output.exists()


def test_explicit_cloud_provider_is_blocked_when_disallowed(tmp_path: Path) -> None:
    config, questions, output = _inputs(tmp_path, [_answerable()])
    cloud = OpenAICompatibleLLMProvider(
        base_url="https://invalid.example", api_key="unused", model="test"
    )
    with pytest.raises(EvaluationInputError, match="forbids the OpenAI-compatible"):
        run_evaluation(config, questions, output, llm=cloud, embeddings=FakeEmbedding())
    assert not output.exists()


def test_invalid_case_set_fails_before_output(tmp_path: Path) -> None:
    config, questions, output = _inputs(tmp_path, [_answerable()])
    payload = json.loads(config.read_text(encoding="utf-8"))
    del payload["allow_cloud_model"]
    config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="invalid case set"):
        run_evaluation(config, questions, output, llm=FakeLLM(),
                       embeddings=FakeEmbedding())
    assert not output.exists()


def test_cloud_permission_must_be_json_boolean(tmp_path: Path) -> None:
    config, questions, output = _inputs(tmp_path, [_answerable()])
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["allow_cloud_model"] = "true"
    config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="invalid case set"):
        run_evaluation(config, questions, output, llm=FakeLLM(),
                       embeddings=FakeEmbedding())
    assert not output.exists()
