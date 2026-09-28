"""Offline gold evaluation exercises the real downstream service boundaries."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from casefile_ai.application.claim_contracts import ExtractedClaim, ModelClaimExtraction
from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.application.contradiction_contracts import ContradictionResult
from casefile_ai.evaluation.downstream import (
    GoldClaimFakeLLM,
    _fact_matches,
    _first_failure,
    _rate,
    _score_pairs,
    _source_scores,
    _summary,
    load_downstream_gold,
    run_downstream_evaluation,
)
from casefile_ai.evaluation.runner import EvaluationInputError

SET = Path(__file__).resolve().parents[2] / "evals/case_sets/mvp_v1"


class HashEmbedding:
    """Small deterministic Chinese character n-gram vectors with no model download."""

    model_version = "downstream-ci-hash-v1"

    @staticmethod
    def _embed(text: str) -> list[float]:
        cleaned = re.sub(r"\s+", "", text.casefold())
        terms = [cleaned[index:index + 2] for index in range(len(cleaned) - 1)]
        terms.extend(re.findall(r"\d+", cleaned))
        vector = [0.0] * 512
        for term in terms:
            index = int.from_bytes(hashlib.blake2b(term.encode(), digest_size=2).digest(),
                                   "big") % len(vector)
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def _inputs() -> tuple[Path, Path, Path]:
    return (SET / "downstream_case_set.json", SET / "downstream_questions.jsonl",
            SET / "downstream_gold.json")


def test_gold_schema_links_pages_and_four_conflict_types() -> None:
    case_set, questions, gold_path = _inputs()
    gold, items = load_downstream_gold(gold_path, case_set, questions)
    assert (len(gold.claims), len(gold.conflict_pairs), len(gold.timeline_events)) == (16, 8, 6)
    assert len(items) == 16
    assert {pair.contradiction_type for pair in gold.conflict_pairs} == {
        "amount", "date", "action", "person"
    }
    assert {pair.expected_relation for pair in gold.conflict_pairs} == {
        "contradiction", "non_contradiction", "uncertain"
    }
    assert any(item.date_precision == "partial" for item in gold.timeline_events)
    assert sum(item.expected_date == "2021-03-18" for item in gold.timeline_events) == 2
    assert len({claim.source.doc_key for claim in gold.claims}) >= 3


def test_full_offline_run_claim_candidate_judge_and_timeline(tmp_path: Path) -> None:
    summary = run_downstream_evaluation(*_inputs(), tmp_path,
                                        embeddings=HashEmbedding())
    rows = [json.loads(line) for line in (tmp_path / "downstream_results.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert len(rows) == 30
    assert summary["claim"]["fact_recall"] == {
        "numerator": 16, "denominator": 16, "value": 1.0
    }
    assert summary["claim"]["source_page_accuracy"]["denominator"] == 16
    assert summary["candidate"]["pair_recall"] == {
        "numerator": 4, "denominator": 4, "value": 1.0
    }
    assert summary["contradiction"]["expected_pairs_all_relations"] == 8
    assert summary["timeline"]["event_recall"]["denominator"] == 6
    assert summary["timeline"]["order_accuracy"]["denominator"] > 0
    for category in ("amount", "date", "person", "action"):
        assert summary["contradiction"]["by_type"][category][
            "expected_conflicting_pairs"] == 1
    assert next(item for item in rows if item["gold_id"] == "date_uncertain")[
        "actual_relation"] == "uncertain"
    assert next(item for item in rows if item["gold_id"] == "event_month")[
        "actual_date"] == "2021-03"
    assert (tmp_path / "results.jsonl").is_file()
    assert (tmp_path / "summary.json").is_file()


def test_claim_omission_is_reported_as_extraction_loss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    original = GoldClaimFakeLLM.generate_structured

    def omit_one(self: GoldClaimFakeLLM, *, system_prompt: str, user_prompt: str,
                 response_model: type[Any]) -> Any:
        if (issubclass(response_model, ModelClaimExtraction) and
                "本次交易金额为人民币438000元" in json.loads(user_prompt)["answer_claim"]):
            return response_model.model_validate({"claims": []})
        return original(self, system_prompt=system_prompt, user_prompt=user_prompt,
                        response_model=response_model)

    monkeypatch.setattr(GoldClaimFakeLLM, "generate_structured", omit_one)
    summary = run_downstream_evaluation(*_inputs(), tmp_path,
                                        embeddings=HashEmbedding())
    rows = [json.loads(line) for line in (tmp_path / "downstream_results.jsonl").read_text(
        encoding="utf-8").splitlines()]
    missing = next(item for item in rows if item["gold_id"] == "amount_a")
    pair = next(item for item in rows if item["gold_id"] == "amount_conflict")
    assert missing["answer_validated"] is True
    assert missing["claim_extracted"] is False
    assert missing["first_failure_stage"] == "claim_extraction"
    assert pair["first_failure_stage"] == "claim_extraction"
    assert pair["candidate_found"] is None
    assert summary["candidate"]["pair_recall"] == {
        "numerator": 3, "denominator": 3, "value": 1.0
    }
    assert summary["contradiction"]["end_to_end_contradiction_recall"][
        "denominator"] == 4


def test_fact_normalization_and_wrong_source_page_are_separate() -> None:
    gold, _ = load_downstream_gold(*(_inputs()[2], _inputs()[0], _inputs()[1]))
    amount = next(item for item in gold.claims if item.claim_key == "amount_a")
    claim = ExtractedClaim("c", "a", "s", "本次交易金额为人民币43.8万元", "amount",
                           ("e",), "v", "m")
    assert _fact_matches(claim, amount)
    assert not _fact_matches(ExtractedClaim(
        "wrong", "a", "s", "本次交易金额为人民币448000元", "amount",
        ("e",), "v", "m"
    ), amount)
    citation = AnswerCitation("e", "s", "doc", "page", "合成.pdf", 2,
                              amount.source.snippet, "version")
    assert _source_scores([citation], amount.source, "version") == (True, False, False)
    row = {key: True for key in (
        "document_present", "parsed", "chunked", "retrieved", "evidence",
        "answer_validated", "claim_extracted", "claim_fact_match",
        "claim_source_document_match",
    )}
    row["claim_source_page_match"] = False
    assert _first_failure(row, (
        ("claim_fact_match", "claim_fact"),
        ("claim_source_document_match", "claim_source_document"),
        ("claim_source_page_match", "claim_source_page"),
    )) == "claim_source_page"
    assert _first_failure({"claim_fact_match": False, "claim_source_page_match": None}, (
        ("claim_fact_match", "claim_fact"),
        ("claim_source_page_match", "claim_source_page"),
    )) == "claim_fact"
    summary = _summary(gold, [{
        "kind": "claim", "gold_id": "amount_a", "category": "amount",
        "claim_fact_match": True, "claim_source_document_match": True,
        "claim_source_page_match": False,
        "claim_evidence_snippet_match": False,
        "first_failure_stage": "claim_source_page",
    }], 1, {}, "test")
    assert summary["claim"]["source_document_accuracy"]["value"] == 1.0
    assert summary["claim"]["source_page_accuracy"] == {
        "numerator": 0, "denominator": 1, "value": 0.0
    }


def test_candidate_miss_judge_error_and_uncertain_are_distinct() -> None:
    gold, _ = load_downstream_gold(*(_inputs()[2], _inputs()[0], _inputs()[1]))
    linked = {item.claim_key: ExtractedClaim(
        item.claim_key, item.question_id, "source", item.expected_text_any[0],
        item.category, ("e",), "v", "m"
    ) for item in gold.claims}
    claim_rows = [{"gold_id": item.claim_key, "first_failure_stage": None,
                   **{name: True for name in (
                       "document_present", "parsed", "chunked", "retrieved",
                       "evidence", "answer_validated",
                   )}} for item in gold.claims]
    candidate_ids = {
        frozenset((pair.claim_a, pair.claim_b)): pair.pair_key
        for pair in gold.conflict_pairs if pair.pair_key != "amount_conflict"
    }
    results = {pair.pair_key: cast(ContradictionResult, SimpleNamespace(
        verdict=("non_contradiction" if pair.pair_key == "date_conflict"
                 else pair.expected_relation), basis="test"
    )) for pair in gold.conflict_pairs if pair.pair_key != "amount_conflict"}
    rows = _score_pairs(gold, claim_rows, linked, candidate_ids, results)
    by_key = {item["gold_id"]: item for item in rows}
    assert by_key["amount_conflict"]["first_failure_stage"] == "candidate_generation"
    assert by_key["amount_conflict"]["contradiction_evaluated"] is None
    assert by_key["date_conflict"]["first_failure_stage"] == "contradiction_decision"
    assert by_key["date_conflict"]["candidate_found"] is True
    assert by_key["date_uncertain"]["contradiction_correct"] is True
    summary = _summary(gold, rows, 0, {}, "test")
    assert summary["candidate"]["pair_recall"] == {
        "numerator": 3, "denominator": 4, "value": 0.75
    }
    assert summary["contradiction"]["end_to_end_contradiction_recall"]["numerator"] == 2


def test_timeline_order_and_not_applicable_denominators() -> None:
    gold, _ = load_downstream_gold(*(_inputs()[2], _inputs()[0], _inputs()[1]))
    records = [{"kind": "timeline_event", "gold_id": item.event_key,
                "timeline_found": True, "timeline_date_match": True,
                "timeline_source_match": True, "first_failure_stage": None}
               for item in gold.timeline_events]
    events = {item.event_key: cast(Any, SimpleNamespace(date_value=item.expected_date))
              for item in gold.timeline_events}
    perfect = _summary(gold, records, 0, events, "test")
    assert perfect["timeline"]["order_accuracy"] == {
        "numerator": 14, "denominator": 14, "value": 1.0
    }
    events["event_delivery"].date_value = "2020-01-01"
    wrong = _summary(gold, records, 0, events, "test")
    assert wrong["timeline"]["order_accuracy"]["numerator"] < 14
    assert _rate([{"eligible": None, "correct": False},
                  {"eligible": True, "correct": True}], "eligible", "correct") == {
        "numerator": 1, "denominator": 1, "value": 1.0
    }


def test_downstream_schema_error_fails_before_run(tmp_path: Path) -> None:
    case_set, questions, gold_path = _inputs()
    raw = json.loads(gold_path.read_text(encoding="utf-8"))
    raw["conflict_pairs"][0]["claim_b"] = "unknown_claim"
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="unknown claim"):
        load_downstream_gold(invalid, case_set, questions)
    raw = json.loads(gold_path.read_text(encoding="utf-8"))
    raw["claims"][0]["normalized"]["amount"] = -1
    invalid.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="amount"):
        load_downstream_gold(invalid, case_set, questions)
    assert not (tmp_path / "downstream_results.jsonl").exists()
