"""Claim quality/provenance regression; scripted judge decisions, never cloud calls."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError
from test_claim_extraction import AnswerStub, ClaimFakeLLM, ClaimStub, _answer

from casefile_ai.application.claim_contracts import ClaimExtractionError, ModelClaimValidation
from casefile_ai.application.services.claim_coverage import DefaultClaimCoverageService
from casefile_ai.application.services.claims import DefaultClaimExtractionService


def source_answer(text: str, quote: str | None = None):
    original = _answer()
    return replace(original, answer=text,
                   claims=(replace(original.claims[0], text=text),),
                   citations=(replace(original.citations[0], quote=quote or text),))


class QualityLLM(ClaimFakeLLM):
    def __init__(self, claims, **quality: str):
        super().__init__(claims)
        self.quality = quality
        self.validation_inputs = []

    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_model: type[Any]) -> Any:
        if response_model is ModelClaimValidation:
            self.support_calls += 1
            payload = json.loads(user_prompt)
            self.validation_inputs.append(payload)
            assert payload["source_answer_claim"]
            assert payload["kind"] in {"amount", "date", "action", "person", "other"}
            return response_model.model_validate({
                "verdict": self.verdict, "reason": "independent scripted quality decision",
                "atomicity": "matches", "scope_preservation": "matches",
                "category_matches": "matches", **self.quality,
            })
        return super().generate_structured(system_prompt=system_prompt, user_prompt=user_prompt,
                                           response_model=response_model)


def output(text: str, kind: str) -> dict[str, object]:
    return {"text": text, "kind": kind, "citation_ids": ["E1"]}


@pytest.mark.parametrize(("source", "claims"), [
    ("交易A的实付金额为45万元。", [output("交易A的实付金额为45万元", "amount")]),
    ("合同A生效日期为2026年3月1日。", [output("合同A生效日期为2026年3月1日", "date")]),
    ("交易A付款人为甲公司。", [output("交易A付款人为甲公司", "person")]),
    ("甲公司已交付交易A的设备。", [output("甲公司已交付交易A的设备", "action")]),
    ("甲公司尚未支付交易A款项。", [output("甲公司尚未支付交易A款项", "action")]),
    ("交易A的支付经办人为王甲。", [output("交易A的支付经办人为王甲", "person")]),
    ("交易A金额45万元，交易B金额45万元。", [
        output("交易A金额45万元", "amount"), output("交易B金额45万元", "amount")]),
    ("合同A应付款45万元，合同A实际支付40万元。", [
        output("合同A应付款45万元", "amount"), output("合同A实际支付40万元", "amount")]),
    ("甲公司已付款，乙公司尚未付款。", [
        output("甲公司已付款", "action"), output("乙公司尚未付款", "action")]),
    ("付款人为甲公司，收款人为乙公司。", [
        output("付款人为甲公司", "person"), output("收款人为乙公司", "person")]),
    ("甲公司于2026年3月1日支付45万元。", [
        output("甲公司于2026年3月1日支付", "date"), output("甲公司支付45万元", "amount")]),
    ("合同A由甲公司与乙公司于3月1日签订。", [
        output("合同A由甲公司与乙公司于3月1日签订", "date")]),
    ("行号A对应合同B。", [output("行号A对应合同B", "other")]),
])
def test_atomic_self_contained_facts_and_primary_categories(source: str, claims) -> None:
    answer = source_answer(source)
    repository = ClaimStub()
    llm = QualityLLM(claims)
    service = DefaultClaimExtractionService(AnswerStub(answer), repository, llm)  # type: ignore[arg-type]
    actual = service.extract_answer_claims(answer.answer_id)
    assert [(claim.text, claim.kind) for claim in actual] == [
        (claim["text"], claim["kind"]) for claim in claims
    ]
    assert llm.calls == 1 and llm.support_calls == len(claims)
    assert all(claim.evidence_ids == ("evidence-one",) for claim in actual)
    if len(actual) > 1:
        assert len({claim.claim_id for claim in actual}) == len(actual)
    coverage = DefaultClaimCoverageService(AnswerStub(answer), repository).evaluate(
        answer.answer_id, actual[0].extraction_version, llm.model_version,
    )
    assert all(item.issue is None for item in coverage.claims)
    assert coverage.complete


@pytest.mark.parametrize(("dimension", "verdict"), [
    (dimension, verdict) for dimension in ("atomicity", "scope_preservation", "category_matches")
    for verdict in ("mismatch", "uncertain")
])
def test_supported_but_invalid_quality_never_persists(dimension: str, verdict: str) -> None:
    answer = source_answer("甲公司对交易A尚未付款。")
    repository = ClaimStub()
    llm = QualityLLM([output("尚未付款", "other")], **{dimension: verdict})
    with pytest.raises(ClaimExtractionError, match=dimension):
        DefaultClaimExtractionService(AnswerStub(answer), repository, llm).extract_answer_claims(  # type: ignore[arg-type]
            answer.answer_id)
    assert repository.saved == ()


def test_unsupported_new_scope_and_numeric_splicing_are_rejected() -> None:
    answer = source_answer("交易A支付450万元。", "交易A支付450万元，交易B由甲公司付款。")
    for text in ("甲公司交易A支付450万元", "交易A支付40万元"):
        repository = ClaimStub()
        llm = QualityLLM([output(text, "amount")])
        with pytest.raises(ClaimExtractionError, match="provenance"):
            service = DefaultClaimExtractionService(AnswerStub(answer), repository, llm)  # type: ignore[arg-type]
            service.extract_answer_claims(answer.answer_id)
        assert not repository.saved and llm.support_calls == 0
    # Within the answer but unsupported by selected Evidence: do not persist a batch.
    expanded = source_answer("甲公司交易A支付450万元。", "交易A支付450万元。")
    llm = QualityLLM([output("甲公司交易A支付450万元", "amount")])
    llm.verdict = "partially_supported"
    with pytest.raises(ClaimExtractionError, match="not fully supported"):
        DefaultClaimExtractionService(AnswerStub(expanded), ClaimStub(), llm).extract_answer_claims(  # type: ignore[arg-type]
            expanded.answer_id)


def test_negation_loss_and_wrong_other_are_quality_failures() -> None:
    answer = source_answer("甲公司尚未支付交易A款项，乙公司已支付交易B款项。")
    for claim, quality in [
        (output("甲公司已支付交易A款项", "action"), {"scope_preservation": "mismatch"}),
        (output("甲公司尚未支付交易A款项", "other"), {"category_matches": "mismatch"}),
    ]:
        with pytest.raises(ClaimExtractionError, match="not validated|provenance"):
            DefaultClaimExtractionService(AnswerStub(answer), ClaimStub(),  # type: ignore[arg-type]
                                          QualityLLM([claim], **quality)).extract_answer_claims(
                                              answer.answer_id)


def test_claim_quality_schema_requires_every_explicit_decision() -> None:
    with pytest.raises(ValidationError):
        ModelClaimValidation.model_validate({"verdict": "supported", "reason": "insufficient"})
