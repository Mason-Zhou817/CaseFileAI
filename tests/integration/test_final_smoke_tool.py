"""Smoke instrumentation uses mocked HTTP only; CI never invokes Qwen."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

SET = Path(__file__).resolve().parents[2] / "evals/case_sets/mvp_stress_v1"


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SET))
    spec = importlib.util.spec_from_file_location("final_smoke_cli", SET / "run_final_smoke.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selected_smoke_is_pinned_and_covers_all_four_groups(script: ModuleType) -> None:
    questions = script.select_questions()
    assert len(questions) == len({item.id for item in questions}) == 20
    assert all(len(ids) == 5 for ids in script.GROUPS.values())
    assert sum(item.expected_status == "insufficient_evidence" for item in questions) == 5
    assert any("claim_multi_fact" in item.tags for item in questions)
    assert any("ocr" in item.tags for item in questions)
    assert any("long_doc" in item.tags for item in questions)


def test_http_failures_are_counted_once_without_credentials(script: ModuleType) -> None:
    transport = script.ObservedTransport(httpx.MockTransport(
        lambda request: httpx.Response(503, text="secret response", request=request)
    ))
    with httpx.Client(transport=transport,
                      headers={"Authorization": "Bearer secret-key"}) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            client.post("https://synthetic.invalid/chat/completions").raise_for_status()
    stats = script.provider_summary(transport.calls)
    assert stats["total_model_calls"] == stats["provider_failures"] == stats["http_503"] == 1
    assert stats["http_5xx"] == 1 and stats["successful_http_calls"] == 0
    serialized = json.dumps([transport.calls, script.error_info(error.value)])
    assert "secret" not in serialized


def test_timeout_is_recorded_without_retry(script: ModuleType) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret-key", request=request)

    transport = script.ObservedTransport(httpx.MockTransport(timeout))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.post("https://synthetic.invalid/chat/completions")
    stats = script.provider_summary(transport.calls)
    assert stats["timeout"] == stats["provider_failures"] == stats["total_model_calls"] == 1
    assert "secret-key" not in json.dumps(transport.calls)


def _row() -> dict[str, Any]:
    return {"retrieval_results": [], "retrieval_all_required_hit": True,
            "validation_status": "model_abstained", "actual_status": "insufficient_evidence",
            "failure_stage": None, "citations": []}


def test_full_context_abstain_is_distinct_from_retrieval_miss(script: ModuleType) -> None:
    question = script.select_questions()[0]
    gold = question.gold_evidence[0]
    hit = {"doc_key": gold.doc_key, "page": gold.page, "text": gold.snippet, "rank": 1}
    row = _row()
    row["retrieval_results"] = [hit]
    script.enrich_qa(row, question, [{"phase": "qa", "scope": question.id,
                                     "context_quotes": [gold.snippet], "error": None,
                                     "output": {"status": "insufficient_evidence"}}])
    assert row["gold_in_qa_context"] and row["false_abstain"]
    assert row["failure_categories"] == ["qa_false_abstain"]


def test_rejected_factual_negative_is_not_hidden_by_validation(script: ModuleType) -> None:
    question = next(item for item in script.select_questions()
                    if item.expected_status == "insufficient_evidence")
    row = _row()
    row.update(validation_status="semantic_validation_failed", failure_stage="validation")
    script.enrich_qa(row, question, [{"phase": "qa", "scope": question.id,
                                     "context_quotes": [], "error": None,
                                     "output": {"status": "answered", "answer": "invented"}}])
    assert not row["false_answer"] and row["model_false_answer"]
    assert "qa_wrong_answer" in row["failure_categories"]
    assert "validation_failure" in row["failure_categories"]


def test_protocol_failure_is_not_counted_as_network_failure(script: ModuleType) -> None:
    question = script.select_questions()[0]
    row = _row()
    row.update(actual_status=None, validation_status=None, failure_stage="qa")
    script.enrich_qa(row, question, [{"phase": "qa", "scope": question.id,
                                     "context_quotes": [],
                                     "error": script.error_info(ValueError("bad schema")),
                                     "output": None}])
    assert "validation_failure" in row["failure_categories"]
    assert "provider_failure" not in row["failure_categories"]
