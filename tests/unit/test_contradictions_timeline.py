from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from casefile_ai.application.claim_contracts import (
    CaseClaim,
    ClaimKind,
    ConflictCandidate,
    ExtractedClaim,
)
from casefile_ai.application.contracts import (
    AnswerCitation,
    AnswerClaim,
    GroundedAnswer,
    QARunMetadata,
)
from casefile_ai.application.contradiction_contracts import (
    ContradictionResult,
    ContradictionValidationError,
    ModelSemanticDecision,
)
from casefile_ai.application.services.contradictions import DefaultContradictionService
from casefile_ai.application.services.timeline import DefaultTimelineService


def candidate(left: str, right: str, kind: ClaimKind = "amount") -> ConflictCandidate:
    claims = tuple(
        ExtractedClaim(f"c{i}", f"a{i}", f"s{i}", text, kind,
                       (f"e{i}",), "v2", "claim-fake")
        for i, text in enumerate((left, right), start=1)
    )
    citations = tuple(
        AnswerCitation(f"e{i}", f"s{i}", f"d{i}", f"p{i}", "合成.pdf", i, text)
        for i, text in enumerate((left, right), start=1)
    )
    return ConflictCandidate("case", claims[0], claims[1], (citations[0],),
                             (citations[1],), "payment")


class CandidateStub:
    def __init__(self, *items: ConflictCandidate) -> None:
        self.items = items

    def generate(self, case_id: str, extraction_version: str,
                 model_version: str) -> tuple[ConflictCandidate, ...]:
        return self.items


class MemoryResults:
    def __init__(self) -> None:
        self.items: dict[str, ContradictionResult] = {}
        self.saves = 0

    def get_result(self, result_id: str) -> ContradictionResult | None:
        return self.items.get(result_id)

    def save_result(self, result: ContradictionResult) -> ContradictionResult:
        self.saves += 1
        self.items[result.result_id] = result
        return result

    def list_case_results(self, case_id: str) -> tuple[ContradictionResult, ...]:
        return tuple(item for item in self.items.values() if item.case_id == case_id)


class FakeLLM:
    model_version = "fake-semantic-v1"

    def __init__(self, output: dict[str, object] | None) -> None:
        self.output = output
        self.calls = 0

    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_model: type[Any]) -> Any:
        self.calls += 1
        assert "evidence_id" not in user_prompt and "claim_id" not in user_prompt
        assert response_model is ModelSemanticDecision
        if self.output is None:
            raise ValueError("malformed")
        return response_model.model_validate(self.output)


def detect(item: ConflictCandidate, llm: FakeLLM | None = None,
           repository: MemoryResults | None = None) -> ContradictionResult:
    return DefaultContradictionService(
        CandidateStub(item), repository or MemoryResults(), llm
    ).detect_case("case", "v2", "claim-fake")[0]


@pytest.mark.parametrize(("left", "right", "verdict"), [
    ("交易日期为2021年3月5日。", "交易日期为2021-03-06。", "contradiction"),
    ("交易日期为2021年3月5日。", "交易日期为2021/03/05。", "non_contradiction"),
    ("交易日期为2021年。", "交易日期为2021年3月5日。", "non_contradiction"),
    ("交易日期为2021年3月。", "交易日期为2021年4月。", "contradiction"),
    ("交易时间为09:30。", "交易时间为10:30。", "contradiction"),
    ("交易时间为09:30。", "交易时间为9:30。", "non_contradiction"),
    ("第一笔交易日期为2021年3月5日。", "第二笔交易日期为2021年3月6日。", "uncertain"),
    ("交易日期为去年春天。", "交易日期为2021年3月5日。", "uncertain"),
])
def test_date_detection(left: str, right: str, verdict: str) -> None:
    assert detect(candidate(left, right, "date")).verdict == verdict


@pytest.mark.parametrize(("left", "right", "verdict"), [
    ("付款A交易日期为2026年3月1日。", "付款A交易日期为2026年3月10日。",
     "contradiction"),
    ("付款A交易日期为2026年3月1日。", "付款A交易日期为2026/03/01。",
     "non_contradiction"),
    ("第一笔付款日期为2026年3月1日。", "第二笔付款日期为2026年3月10日。",
     "uncertain"),
    ("合同CT-77起租日期为2026年3月1日。", "合同CT-78起租日期为2026年3月10日。",
     "uncertain"),
    ("甲公司第一笔付款日期为2026年3月1日。", "甲公司第二笔付款日期为2026年3月10日。",
     "uncertain"),
    ("甲公司付款日期为2026年3月1日。", "甲公司到账日期为2026年3月10日。",
     "uncertain"),
    ("付款记录日期为2026年3月1日。", "另一笔付款记录日期为2026年3月10日。",
     "uncertain"),
])
def test_date_event_scope_is_not_value_equality(
    left: str, right: str, verdict: str,
) -> None:
    assert detect(candidate(left, right, "date")).verdict == verdict


@pytest.mark.parametrize(("left", "right", "verdict"), [
    ("交易金额为450000元。", "交易金额为500000元。", "contradiction"),
    ("交易金额为450000元。", "交易金额为450,000元。", "non_contradiction"),
    ("交易金额为45万元。", "交易金额为450000元。", "non_contradiction"),
    ("第一笔交易金额为450000元。", "第二笔交易金额为500000元。", "uncertain"),
    ("合同金额为450000元。", "支付金额为500000元。", "uncertain"),
    ("交易金额为450000元。", "交易金额为500000美元。", "uncertain"),
    ("交易金额为450000。", "交易金额为500000元。", "uncertain"),
    ("交易金额为-450000元。", "交易金额为450000元。", "uncertain"),
])
def test_amount_detection(left: str, right: str, verdict: str) -> None:
    assert detect(candidate(left, right)).verdict == verdict


def decision(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "verdict": "contradiction", "same_event": True, "same_fact_slot": True,
        "reason": "合成事实不兼容", "left_occurrence": "occurred",
        "right_occurrence": "did_not_occur", "left_identity": "王海",
        "right_identity": "陈军",
    }
    value.update(overrides)
    return value


def test_action_conflict_and_insufficient_evidence() -> None:
    item = candidate("公司已收到投资款。", "公司未收到投资款。", "action")
    result = detect(item, FakeLLM(decision()))
    assert result.verdict == "contradiction"
    assert result.candidate_id == item.candidate_id
    assert result.left_claim_id == item.left_claim.claim_id
    assert result.left_evidence == item.left_evidence
    assert result.right_evidence == item.right_evidence
    assert detect(item, FakeLLM(decision(right_occurrence="unknown"))).verdict == "uncertain"
    assert detect(item, FakeLLM(decision(verdict="uncertain"))).verdict == "uncertain"


def test_person_same_slot_conflict_but_different_roles_or_events_do_not() -> None:
    item = candidate("交易唯一付款人是王海。", "交易唯一付款人是陈军。", "person")
    assert detect(item, FakeLLM(decision())).verdict == "contradiction"
    assert detect(item, FakeLLM(decision(same_fact_slot=False))).verdict == "non_contradiction"
    assert detect(item, FakeLLM(decision(same_event=False))).verdict == "non_contradiction"
    assert detect(item, FakeLLM(decision(right_identity="王海"))).verdict == "uncertain"
    plural = candidate("合同签署人是王海。", "合同签署人是陈军。", "person")
    assert detect(plural, FakeLLM(decision())).verdict == "uncertain"
    assert detect(item, FakeLLM(decision(left_identity="外部人物"))).verdict == "uncertain"
    alias = candidate("交易唯一付款人是王海。", "交易唯一付款人是王海先生。", "person")
    assert detect(alias, FakeLLM(decision(right_identity="王海先生"))).verdict == "uncertain"


def test_malformed_output_and_forged_candidate_fail_safely() -> None:
    item = candidate("公司已付款。", "公司未付款。", "action")
    repository = MemoryResults()
    assert detect(item, FakeLLM(None), repository).verdict == "uncertain"
    assert repository.saves == 0
    assert detect(item, FakeLLM(decision(evidence_id="forged"))).verdict == "uncertain"
    forged = replace(item, left_evidence=(replace(item.left_evidence[0], evidence_id="forged"),))
    with pytest.raises(ContradictionValidationError, match="evidence"):
        detect(forged)
    with pytest.raises(ContradictionValidationError, match="case"):
        detect(replace(item, case_id="foreign"))
    with pytest.raises(ContradictionValidationError, match="type"):
        detect(replace(item, right_claim=replace(item.right_claim, kind="person")))


def test_semantic_model_failure_keeps_prior_valid_result() -> None:
    item = candidate("公司已付款。", "公司未付款。", "action")
    repository = MemoryResults()
    llm = FakeLLM(decision())
    saved = detect(item, llm, repository)
    assert saved.verdict == "contradiction"
    llm.output = None
    assert detect(item, llm, repository) == saved
    assert llm.calls == 1
    different = replace(
        item,
        left_claim=replace(item.left_claim, claim_id="c3"),
        right_claim=replace(item.right_claim, claim_id="c4"),
    )
    failed = detect(different, llm, repository)
    assert failed.verdict == "uncertain" and failed.basis == "model_output_invalid"
    assert repository.list_case_results("case") == (saved,)


def test_duplicate_detection_does_not_persist_twice() -> None:
    item = candidate("交易金额为450000元。", "交易金额为500000元。")
    repository = MemoryResults()
    service = DefaultContradictionService(CandidateStub(item), repository)
    first = service.detect_case("case", "v2", "claim-fake")
    assert service.detect_case("case", "v2", "claim-fake") == first
    assert repository.saves == 1
    repository.items[first[0].result_id] = replace(first[0], case_id="foreign")
    with pytest.raises(ContradictionValidationError, match="cached"):
        service.detect_case("case", "v2", "claim-fake")


class AnswersAndClaims:
    def __init__(self, claims: tuple[ExtractedClaim, ...]) -> None:
        self.claims = claims

    def get_answer(self, answer_id: str) -> GroundedAnswer | None:
        claim = next((item for item in self.claims if item.answer_id == answer_id), None)
        if claim is None:
            return None
        return GroundedAnswer(
            answer_id, "合成问题", claim.text,
            (AnswerCitation(claim.evidence_ids[0], claim.source_answer_claim_id,
                            "doc", "page", "合成.pdf", 1, claim.text),),
            False, (AnswerClaim(claim.source_answer_claim_id, claim.text,
                                claim.evidence_ids, 1.0, "supported"),),
            QARunMetadata(1, "r", "e", "c", "m", "p", claim.evidence_ids,
                          "validated", None, 0, 0, 0, 0),
        )

    def get_claims(self, answer_id: str, extraction_version: str,
                   model_version: str) -> tuple[ExtractedClaim, ...]:
        return tuple(item for item in self.claims if item.answer_id == answer_id)

    def list_case_claims(self, case_id: str, extraction_version: str,
                         model_version: str) -> tuple[CaseClaim, ...]:
        return tuple(CaseClaim(case_id, item) for item in self.claims)


def test_timeline_order_partial_unknown_same_day_and_provenance() -> None:
    texts = ("合同签署于2021年3月5日。", "付款发生于2021年3月5日。",
             "最早接触于2020年。", "公司收到投资款。", "付款日期为2022年4月。")
    claims = tuple(
        ExtractedClaim(f"c{i}", f"a{i}", f"s{i}", text, "date" if "年" in text else "action",
                       (f"e{i}",), "v2", "claim-fake")
        for i, text in enumerate(texts)
    )
    source = AnswersAndClaims(claims)
    results = MemoryResults()
    conflict = detect(candidate("交易金额为450000元。", "交易金额为500000元。"),
                      repository=results)
    results.items[conflict.result_id] = replace(conflict, left_claim_id="c0")
    timeline = DefaultTimelineService(source, source, results).build("case", "v2", "claim-fake")
    assert [event.date_value for event in timeline] == [
        "2020", "2021-03-05", "2021-03-05", "2022-04", None
    ]
    assert [event.date_status for event in timeline] == [
        "partial", "exact", "exact", "partial", "unknown"
    ]
    assert len({event.event_id for event in timeline}) == 5
    first_day = next(event for event in timeline if event.claim_id == "c0")
    assert first_day.contradiction_result_ids == (conflict.result_id,)
    assert first_day.evidence[0].filename == "合成.pdf"
    assert first_day.evidence[0].page_number == 1
    assert first_day.evidence[0].quote == claims[0].text
