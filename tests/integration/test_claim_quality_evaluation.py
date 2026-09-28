"""Fixed Claim stage labels and conservative metrics; no network provider in CI."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError
from test_claim_extraction import _answer

from casefile_ai.application.claim_contracts import ExtractedClaim
from casefile_ai.application.services.claim_provenance import extractive_positions
from casefile_ai.evaluation.claim_quality import (
    ClaimQualityGold,
    assess_claim,
    fact_coverage,
    matching_gold,
    quality_summary,
)
from casefile_ai.evaluation.runner import _normalized, _read_inputs
from casefile_ai.infrastructure.parsers.pdf import extract_pdf_pages

SET = Path(__file__).resolve().parents[2] / "evals/case_sets/mvp_stress_v1"


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SET))
    spec = importlib.util.spec_from_file_location("claim_quality_cli", SET / "run_claim_quality.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_quality_samples_resolve_to_real_pinned_material(script: ModuleType) -> None:
    samples = script.load_samples()
    assert len(samples) == len({sample["id"] for sample in samples}) == 14
    assert {"s06", "s09", "s10", "s38"} <= set(script.SUBSET)
    _, _, paths = _read_inputs(SET / "case_set.json", SET / "questions.jsonl")
    keys = {ref["doc_key"] for sample in samples for source in sample["source_claims"]
            for ref in source["evidence"]}
    pages = {key: extract_pdf_pages(paths[key]) for key in keys}
    categories = []
    for sample in samples:
        golds = [ClaimQualityGold.model_validate(item) for item in sample["gold"]]
        assert sum(gold.core_required for gold in golds) == sample["expected_core_claim_count"]
        for source in sample["source_claims"]:
            for ref in source["evidence"]:
                assert _normalized(ref["snippet"]) in _normalized(
                    pages[ref["doc_key"]][ref["page"] - 1].text)
        for gold in golds:
            categories.append(gold.category)
            assert _normalized(gold.source.snippet) in _normalized(
                pages[gold.source.doc_key][gold.source.page - 1].text)
            assert all(any(extractive_positions(source["text"], text) is not None
                           for source in sample["source_claims"])
                       for text in gold.atomic_text_any)
    assert all(categories.count(kind) >= 2 for kind in ("amount", "date", "person", "action"))
    assert sum(sample["expected_core_claim_count"] >= 2 for sample in samples) >= 2


def gold() -> ClaimQualityGold:
    return ClaimQualityGold.model_validate({
        "fact_key": "rent", "category": "amount", "core_match_groups": [["450000元"]],
        "required_scope_groups": [["交易A"]], "atomic_text_any": ["交易A金额为450000元"],
        "source": {"doc_key": "bank", "page": 2, "snippet": "450000元"},
    })


def test_quality_checks_scope_category_and_actual_source_not_model_flags() -> None:
    citation = replace(_answer().citations[0], quote="交易A金额为450000元", page_number=2,
                       document_version_id="v1")
    claim = ExtractedClaim("c1", "a1", citation.claim_id or "", "交易A金额为450000元",
                           "amount", (citation.evidence_id,), "v3", "fake")
    row = assess_claim(claim, [citation], [gold()], {"v1": "bank"})
    assert row["atomicity"] and row["category_correct"] and row["scope_preserved"]
    assert row["source_correct"] and row["supported"] is None
    assert row["manual_review_required"]
    wrong = replace(claim, kind="other", text="金额为450000元")
    row = assess_claim(wrong, [replace(citation, page_number=1)], [gold()], {"v1": "bank"})
    assert row["category_correct"] is False and row["scope_preserved"] is False
    assert row["source_correct"] is False and row["atomicity"] is None
    assert assess_claim(claim, [citation], [gold()], {"v1": "wrong"})["source_correct"] is False


def test_quality_unknowns_do_not_enter_denominators_or_look_like_success() -> None:
    unknown = {field: None for field in ("atomicity", "category_correct", "scope_preserved",
                                        "source_correct", "supported")}
    summary = quality_summary([{**unknown, "manual_review_required": True},
                               {**dict.fromkeys(unknown, True), "manual_review_required": False}])
    assert summary["claim_category_accuracy"] == {"numerator": 1, "denominator": 1, "value": 1}
    assert summary["unsupported_claim_rate"]["numerator"] == 0
    assert summary["manual_review_count"] == 1
    assert quality_summary([])["unsupported_claim_rate"]["value"] is None


def test_missing_facts_and_extra_claims_are_reported_apart_from_quality_rates() -> None:
    assert fact_coverage([gold()], []) == {
        "expected_core_count": 1, "expected_all_count": 1,
        "core_fact_recall": {"numerator": 0, "denominator": 1, "value": 0},
        "missing_core_fact_keys": ["rent"], "duplicate_fact_keys": [],
        "unmatched_claim_count": 0,
    }
    report = fact_coverage([gold()], [{"fact_key": "rent"}, {"fact_key": "rent"},
                                      {"fact_key": None}])
    assert report["core_fact_recall"]["value"] == 1
    assert report["duplicate_fact_keys"] == ["rent"]
    assert report["unmatched_claim_count"] == 1


def test_annotation_matching_is_independent_of_category_and_ambiguity_is_unresolved() -> None:
    assert matching_gold("交易A金额为450000元", gold())
    citation = _answer().citations[0]
    claim = ExtractedClaim("c", "a", citation.claim_id or "", "450000元", "other",
                           (citation.evidence_id,), "v3", "fake")
    row = assess_claim(claim, [citation], [gold(), gold()], {})
    assert row["fact_key"] is None and row["category_correct"] is None
    data = gold().model_dump()
    data["required_scope_groups"] = [[""]]
    with pytest.raises(ValidationError):
        ClaimQualityGold.model_validate(data)


def test_setup_replay_requires_evidence_offered_by_real_qa_context(script: ModuleType) -> None:
    sample = script.load_samples()[0]
    provider = script.ReplayQAProvider([sample], {"table": "03_broken_table.pdf"})
    with pytest.raises(script.EvaluationInputError, match="not offered"):
        provider.generate_structured(system_prompt="", user_prompt=json.dumps({
            "question": sample["question"], "evidence": []}),
            response_model=script.ModelGroundedAnswer)
