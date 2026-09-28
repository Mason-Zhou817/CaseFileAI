from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_qa_service import _setup

from casefile_ai.application.claim_contracts import (
    ClaimExtractionError,
    ExtractedClaim,
    ModelAtomicClaim,
    ModelClaimExtraction,
    ModelClaimValidation,
)
from casefile_ai.application.contracts import (
    AnswerCitation,
    AnswerClaim,
    GroundedAnswer,
    QARunMetadata,
)
from casefile_ai.application.services.claims import DefaultClaimExtractionService
from casefile_ai.domain.ids import derived_id
from casefile_ai.infrastructure.db.claims import SqlAlchemyClaimRepository
from casefile_ai.infrastructure.db.session import build_session_factory


class ClaimFakeLLM:
    model_version = "claim-fake-v1"

    def __init__(self, claims: list[dict[str, object]]) -> None:
        self.claims = claims
        self.calls = 0
        self.support_calls = 0
        self.schema_aliases: list[str] = []
        self.prompt_evidence: list[dict[str, str]] = []
        self.verdict = "supported"

    def generate_structured(
        self, *, system_prompt: str, user_prompt: str, response_model: type[Any]
    ) -> Any:
        payload = json.loads(user_prompt)
        if response_model is ModelClaimValidation:
            self.support_calls += 1
            return response_model.model_validate(
                {"verdict": self.verdict, "reason": "合成测试判定",
                 "atomicity": "matches", "scope_preservation": "matches",
                 "category_matches": "matches"}
            )
        assert issubclass(response_model, ModelClaimExtraction)
        self.calls += 1
        self.prompt_evidence = payload["evidence"]
        schema = response_model.model_json_schema()["$defs"]["AllowedAtomicClaim"]
        items = schema["properties"]["citation_ids"]["items"]
        self.schema_aliases = items.get("enum") or [items["const"]]
        return response_model.model_validate({"claims": self.claims})


def test_single_claim_persists_real_answer_citation_and_reloads(tmp_path: Path) -> None:
    application, ingestion, retrieval, qa_repository, _, qa = _setup(tmp_path)
    case = application.service.create_case("Claim 合成案件")
    fixture = Path(__file__).resolve().parents[1] / "fixtures/synthetic_case/02_银行凭证_扫描件.pdf"
    ingestion.import_document(case.case_id, fixture)
    retrieval.index_case(case.case_id)
    answer = qa.ask_case(case.case_id, "银行凭证中的金额是多少？", 2)
    assert not answer.abstained and answer.metadata is not None

    fake = ClaimFakeLLM(
        [{"text": "交易金额为450000元。", "kind": "amount", "citation_ids": ["E1"]}]
    )
    repository = SqlAlchemyClaimRepository(build_session_factory(application.engine))
    service = DefaultClaimExtractionService(qa_repository, repository, fake)
    claims = service.extract_answer_claims(answer.answer_id)
    assert len(claims) == 1
    claim = claims[0]
    assert claim.text == "交易金额为450000元。"
    assert claim.answer_id == answer.answer_id
    assert claim.source_answer_claim_id == answer.claims[0].claim_id
    assert claim.evidence_ids == (answer.citations[0].evidence_id,)
    assert answer.citations[0].filename == fixture.name
    assert answer.citations[0].page_number >= 1
    assert fake.schema_aliases == ["E1"]
    assert fake.prompt_evidence[0]["citation_id"] == "E1"
    assert "evidence_id" not in fake.prompt_evidence[0]
    assert fake.support_calls == 1
    assert service.extract_answer_claims(answer.answer_id) == claims
    assert fake.calls == 1
    assert (
        repository.get_claims(answer.answer_id, claim.extraction_version, fake.model_version)
        == claims
    )
    assert repository.list_case_claims(
        case.case_id, claim.extraction_version, fake.model_version
    )[0].claim == claim

    forged = ExtractedClaim(
        claim_id=derived_id("forged-claim", answer.answer_id),
        answer_id=answer.answer_id,
        source_answer_claim_id=answer.claims[0].claim_id,
        text="伪造事实",
        kind="other",
        evidence_ids=("00000000-0000-0000-0000-000000000000",),
        extraction_version="different-version",
        model_version=fake.model_version,
    )
    with pytest.raises(ClaimExtractionError, match="citation"):
        repository.save_claims(answer.answer_id, (forged,))

    other = application.service.create_case("另一合成案件")
    ingestion.import_document(other.case_id, fixture)
    retrieval.index_case(other.case_id)
    other_answer = qa.ask_case(other.case_id, "银行凭证中的金额是多少？", 2)
    assert not other_answer.abstained
    foreign = ExtractedClaim(
        claim_id=derived_id("foreign-claim", answer.answer_id),
        answer_id=answer.answer_id,
        source_answer_claim_id=answer.claims[0].claim_id,
        text="跨案证据",
        kind="other",
        evidence_ids=(other_answer.citations[0].evidence_id,),
        extraction_version="different-version",
        model_version=fake.model_version,
    )
    with pytest.raises(ClaimExtractionError, match="citation"):
        repository.save_claims(answer.answer_id, (foreign,))
    assert repository.list_case_claims(
        other.case_id, claim.extraction_version, fake.model_version
    ) == ()
    application.engine.dispose()


class AnswerStub:
    def __init__(self, answer: GroundedAnswer) -> None:
        self.answer = answer

    def get_answer(self, answer_id: str) -> GroundedAnswer | None:
        return self.answer if answer_id == self.answer.answer_id else None


class ClaimStub:
    def __init__(self) -> None:
        self.saved: tuple[ExtractedClaim, ...] = ()

    def get_claims(
        self, answer_id: str, extraction_version: str, model_version: str
    ) -> tuple[ExtractedClaim, ...]:
        return self.saved

    def save_claims(
        self, answer_id: str, claims: tuple[ExtractedClaim, ...]
    ) -> tuple[ExtractedClaim, ...]:
        self.saved = claims
        return claims


def _answer() -> GroundedAnswer:
    return GroundedAnswer(
        answer_id="answer-one",
        question="金额和日期？",
        answer="交易金额为450000元，日期为2021年3月5日。",
        citations=(
            AnswerCitation(
                "evidence-one",
                "qa-claim-one",
                "doc-one",
                "page-one",
                "合成.pdf",
                1,
                "交易金额450000元，日期2021年3月5日。",
            ),
        ),
        abstained=False,
        claims=(
            AnswerClaim(
                "qa-claim-one",
                "交易金额为450000元，日期为2021年3月5日。",
                ("evidence-one",),
                0.9,
                "supported",
            ),
        ),
        metadata=QARunMetadata(
            1, "r", "e", "c", "m", "p", ("evidence-one",), "validated", None, 0, 0, 0, 0
        ),
    )


def test_multiple_atomic_facts_and_empty_result() -> None:
    answer = _answer()
    repository = ClaimStub()
    fake = ClaimFakeLLM(
        [
            {"text": "交易金额为450000元。", "kind": "amount", "citation_ids": ["E1"]},
            {"text": "日期为2021年3月5日。", "kind": "date", "citation_ids": ["E1"]},
        ]
    )
    service = DefaultClaimExtractionService(AnswerStub(answer), repository, fake)  # type: ignore[arg-type]
    claims = service.extract_answer_claims(answer.answer_id)
    assert len(claims) == 2
    assert {claim.kind for claim in claims} == {"amount", "date"}
    assert all(claim.evidence_ids == ("evidence-one",) for claim in claims)
    assert fake.support_calls == 2

    empty_fake = ClaimFakeLLM([])
    empty = DefaultClaimExtractionService(AnswerStub(answer), ClaimStub(), empty_fake)  # type: ignore[arg-type]
    assert empty.extract_answer_claims(answer.answer_id) == ()
    assert empty_fake.support_calls == 0


def test_extraction_corrects_numeric_kind_and_rejects_added_answer_fact() -> None:
    answer = _answer()
    mistaken_kind = ClaimFakeLLM(
        [{"text": "交易金额为450000元", "kind": "date", "citation_ids": ["E1"]}]
    )
    corrected = DefaultClaimExtractionService(
        AnswerStub(answer), ClaimStub(), mistaken_kind  # type: ignore[arg-type]
    ).extract_answer_claims(answer.answer_id)
    assert corrected[0].kind == "amount"

    expanded = ClaimFakeLLM(
        [{"text": "交易金额为人民币450000元", "kind": "amount", "citation_ids": ["E1"]}]
    )
    with pytest.raises(ClaimExtractionError, match="verbatim"):
        DefaultClaimExtractionService(
            AnswerStub(answer), ClaimStub(), expanded  # type: ignore[arg-type]
        ).extract_answer_claims(answer.answer_id)


def test_rejects_invalid_or_foreign_alias_and_unsupported_claim() -> None:
    answer = _answer()
    invalid = ClaimFakeLLM([{"text": "伪造", "kind": "other", "citation_ids": ["E2"]}])
    repository = ClaimStub()
    service = DefaultClaimExtractionService(AnswerStub(answer), repository, invalid)  # type: ignore[arg-type]
    with pytest.raises(ClaimExtractionError, match="model output invalid"):
        service.extract_answer_claims(answer.answer_id)
    assert repository.saved == ()

    # Even a provider bypassing its own schema cannot inject a real evidence ID.
    class BypassLLM(ClaimFakeLLM):
        def generate_structured(
            self, *, system_prompt: str, user_prompt: str, response_model: type[Any]
        ) -> Any:
            if response_model is ModelClaimValidation:
                return super().generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=response_model,
                )
            return ModelClaimExtraction.model_construct(
                claims=[
                    ModelAtomicClaim.model_construct(
                        text="伪造", kind="other", citation_ids=["foreign-evidence"]
                    )
                ]
            )

    bypass = DefaultClaimExtractionService(AnswerStub(answer), repository, BypassLLM([]))  # type: ignore[arg-type]
    with pytest.raises(ClaimExtractionError, match="citation not allowed"):
        bypass.extract_answer_claims(answer.answer_id)
    assert repository.saved == ()

    unsupported = ClaimFakeLLM(
        [{"text": "交易金额为450000元。", "kind": "other", "citation_ids": ["E1"]}]
    )
    unsupported.verdict = "unsupported"
    checked = DefaultClaimExtractionService(AnswerStub(answer), repository, unsupported)  # type: ignore[arg-type]
    with pytest.raises(ClaimExtractionError, match="not fully supported"):
        checked.extract_answer_claims(answer.answer_id)
    assert repository.saved == ()


def test_abstained_answer_does_not_call_model() -> None:
    answer = _answer()
    abstained = GroundedAnswer(answer.answer_id, answer.question, "材料不足", (), True)
    fake = ClaimFakeLLM([])
    service = DefaultClaimExtractionService(AnswerStub(abstained), ClaimStub(), fake)  # type: ignore[arg-type]
    assert service.extract_answer_claims(abstained.answer_id) == ()
    assert fake.calls == 0
