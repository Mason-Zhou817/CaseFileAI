"""Alignment regression tooling is pinned and observable without live CI calls."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import httpx
import pytest

SET = Path(__file__).resolve().parents[2] / "evals/case_sets/mvp_stress_v1"


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SET))
    spec = importlib.util.spec_from_file_location(
        "alignment_smoke_cli", SET / "run_alignment_smoke.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_subset_has_both_failures_and_previous_controls(script: ModuleType) -> None:
    by_id = {item.id: item for item in script.select_questions()}
    subset = [by_id[key] for key in script.SUBSET]
    assert len(subset) == len({item.id for item in subset}) == 9
    assert {"s19", "s27"} <= {item.id for item in subset}
    assert sum(item.expected_status == "answered" for item in subset
               if item.id != "s27") >= 3
    assert sum(item.expected_status == "insufficient_evidence" for item in subset
               if item.id != "s19") >= 2


def test_usage_observations_exclude_secrets_and_unrelated_fields(script: ModuleType) -> None:
    transport = script.UsageTransport(httpx.MockTransport(lambda request: httpx.Response(
        200, json={"usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49,
                             "private": "secret-response"}}, request=request,
    )))
    with httpx.Client(transport=transport,
                      headers={"Authorization": "Bearer secret-key"}) as client:
        client.post("https://synthetic.invalid/chat/completions")
    assert transport.calls[0]["tokens"] == {
        "prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49,
    }
    assert "secret" not in json.dumps(transport.calls)


def test_rejected_wrong_answer_is_distinct_from_correct_negative_abstention(
    script: ModuleType,
) -> None:
    rows = [{"id": "negative", "expected_status": "insufficient_evidence",
             "actual_status": "insufficient_evidence", "failure_stage": "validation",
             "validation_status": "alignment_validation_failed", "latency_ms": {"qa_ms": 12}},
            {"id": "positive", "expected_status": "answered",
             "actual_status": "insufficient_evidence", "failure_stage": "validation",
             "validation_status": "alignment_validation_failed", "latency_ms": {"qa_ms": 20}}]
    calls = [{"scope": key, "schema": "ModelAnswerAlignment", "output": {}}
             for key in ("negative", "positive")]
    report = script.observation_summary(rows, calls, [])
    assert report["correct_abstain_ids"] == ["negative"]
    assert report["positive_abstain_ids"] == ["positive"]
    assert report["false_answer_ids"] == []
    assert report["validation_failure_ids"] == report["alignment_rejected_ids"]
    assert report["mean_added_structured_alignment_calls"] == 1
