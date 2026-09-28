from __future__ import annotations

from casefile_ai.application.claim_contracts import CaseClaim, ClaimKind, ExtractedClaim
from casefile_ai.application.contracts import (
    AnswerCitation,
    AnswerClaim,
    GroundedAnswer,
    QARunMetadata,
)
from casefile_ai.application.services.claim_coverage import DefaultClaimCoverageService
from casefile_ai.application.services.conflict_candidates import (
    DefaultConflictCandidateService,
    _omits_evidence_event_qualifier,
)


def _answer(answer_id: str, source_text: str, evidence_id: str) -> GroundedAnswer:
    source_id = f"source-{answer_id}"
    return GroundedAnswer(
        answer_id=answer_id,
        question="合成问题",
        answer=source_text,
        citations=(
            AnswerCitation(evidence_id, source_id, f"doc-{answer_id}", f"page-{answer_id}",
                           "合成.pdf", 1, source_text),
        ),
        abstained=False,
        claims=(AnswerClaim(source_id, source_text, (evidence_id,), 0.9, "supported"),),
        metadata=QARunMetadata(
            1, "r", "e", "c", "m", "p", (evidence_id,), "validated", None, 0, 0, 0, 0
        ),
    )


def _claim(answer: GroundedAnswer, claim_id: str, text: str, kind: ClaimKind,
           evidence_id: str | None = None) -> ExtractedClaim:
    return ExtractedClaim(
        claim_id, answer.answer_id, answer.claims[0].claim_id, text,
        kind, (evidence_id or answer.citations[0].evidence_id,), "v2", "fake"
    )


class QAStub:
    def __init__(self, *answers: GroundedAnswer) -> None:
        self.answers = {answer.answer_id: answer for answer in answers}

    def get_answer(self, answer_id: str) -> GroundedAnswer | None:
        return self.answers.get(answer_id)


class ClaimStub:
    def __init__(self, case_claims: tuple[CaseClaim, ...]) -> None:
        self.case_claims = case_claims

    def get_claims(self, answer_id: str, extraction_version: str,
                   model_version: str) -> tuple[ExtractedClaim, ...]:
        return tuple(item.claim for item in self.case_claims if item.claim.answer_id == answer_id)

    def list_case_claims(self, case_id: str, extraction_version: str,
                         model_version: str) -> tuple[CaseClaim, ...]:
        return self.case_claims


def test_complete_multi_claim_coverage_with_answer_and_evidence_trace() -> None:
    answer = _answer("a", "交易金额为450000元，日期为2021年3月5日。", "e1")
    claims = (
        _claim(answer, "amount", "交易金额为450000元", "amount"),
        _claim(answer, "date", "日期为2021年3月5日", "date"),
    )
    result = DefaultClaimCoverageService(
        QAStub(answer), ClaimStub(tuple(CaseClaim("case", claim) for claim in claims))
    ).evaluate("a", "v2", "fake")
    assert result.complete
    assert result.answer_id == "a"
    assert result.sources[0].covered_claim_ids == ("amount", "date")
    assert result.sources[0].uncovered_fragments == ()
    assert all(item.valid_evidence and item.evidence[0].evidence_id == "e1"
               for item in result.claims)


def test_partial_coverage_identifies_missing_answer_fragment() -> None:
    answer = _answer("a", "交易金额为450000元，日期为2021年3月5日。", "e1")
    claim = _claim(answer, "amount", "交易金额为450000元", "amount")
    result = DefaultClaimCoverageService(
        QAStub(answer), ClaimStub((CaseClaim("case", claim),))
    ).evaluate("a", "v2", "fake")
    assert not result.complete
    assert result.sources[0].covered_claim_ids == ("amount",)
    assert any("2021年3月5日" in fragment.text
               for fragment in result.sources[0].uncovered_fragments)
    assert all(fragment.source_answer_claim_id == answer.claims[0].claim_id
               for fragment in result.sources[0].uncovered_fragments)


def test_claim_without_legal_answer_evidence_does_not_cover() -> None:
    answer = _answer("a", "交易金额为450000元。", "e1")
    claim = _claim(answer, "amount", "交易金额为450000元", "amount", "forged")
    result = DefaultClaimCoverageService(
        QAStub(answer), ClaimStub((CaseClaim("case", claim),))
    ).evaluate("a", "v2", "fake")
    assert not result.complete
    assert result.claims[0].issue == "invalid_evidence"
    assert not result.claims[0].valid_evidence
    assert result.sources[0].covered_claim_ids == ()
    assert result.sources[0].uncovered_fragments


def test_candidates_are_relevant_unique_case_scoped_and_traceable() -> None:
    answers = (
        _answer("a1", "交易金额为450000元。", "e1"),
        _answer("a2", "交易金额为500000元。", "e2"),
        _answer("a3", "合同金额为600000元。", "e3"),
        _answer("a4", "交易日期为2021年3月5日。", "e4"),
        _answer("foreign", "交易金额为700000元。", "e5"),
    )
    claims = (
        _claim(answers[0], "c1", answers[0].answer, "amount"),
        _claim(answers[1], "c2", answers[1].answer, "amount"),
        _claim(answers[2], "c3", answers[2].answer, "amount"),
        _claim(answers[3], "c4", answers[3].answer, "date"),
        _claim(answers[4], "c5", answers[4].answer, "amount"),
    )
    entries = tuple(CaseClaim("case", claim) for claim in claims[:4]) + (
        CaseClaim("case", claims[1]), CaseClaim("foreign-case", claims[4])
    )
    result = DefaultConflictCandidateService(
        QAStub(*answers), ClaimStub(entries)
    ).generate("case", "v2", "fake")
    assert len(result) == 1
    candidate = result[0]
    assert (candidate.left_claim.claim_id, candidate.right_claim.claim_id) == ("c1", "c2")
    assert candidate.shared_topic == "payment"
    assert candidate.case_id == "case"
    assert candidate.left_evidence[0].evidence_id == "e1"
    assert candidate.right_evidence[0].evidence_id == "e2"


def test_action_and_person_candidates_without_duplicate_identical_assertions() -> None:
    answers = (
        _answer("a1", "公司已收到投资款。", "e1"),
        _answer("a2", "公司未收到投资款。", "e2"),
        _answer("a3", "付款人是王海。", "e3"),
        _answer("a4", "付款人是陈军。", "e4"),
        _answer("a5", "付款人是王海。", "e5"),
    )
    claims = (
        _claim(answers[0], "action-yes", answers[0].answer, "action"),
        _claim(answers[1], "action-no", answers[1].answer, "action"),
        _claim(answers[2], "person-wang", answers[2].answer, "person"),
        _claim(answers[3], "person-chen", answers[3].answer, "person"),
        _claim(answers[4], "person-wang-repeat", answers[4].answer, "person"),
    )
    result = DefaultConflictCandidateService(
        QAStub(*answers), ClaimStub(tuple(CaseClaim("case", claim) for claim in claims))
    ).generate("case", "v2", "fake")
    ids = {(item.left_claim.claim_id, item.right_claim.claim_id) for item in result}
    assert ("action-no", "action-yes") in ids
    assert ("person-chen", "person-wang") in ids
    assert ("person-wang", "person-wang-repeat") not in ids
    assert all(left != right for left, right in ids)


def test_date_subclaim_dropping_explicit_event_marker_never_becomes_candidate() -> None:
    current = _answer("current", "付款记录日期为2026年3月1日。", "e-current")
    other = _answer("other", "另一笔付款记录日期为2026年3月10日。", "e-other")
    same_event = _answer("same", "付款记录日期为2026年3月10日。", "e-same")
    claims = (
        _claim(current, "current-date", "付款记录日期为2026年3月1日", "date"),
        _claim(other, "truncated-date", "付款记录日期为2026年3月10日", "date"),
        _claim(other, "other-date", "另一笔付款记录日期为2026年3月10日", "date"),
        _claim(same_event, "same-date", "付款记录日期为2026年3月10日", "date"),
    )
    assert _omits_evidence_event_qualifier(claims[1], other.citations)
    assert not _omits_evidence_event_qualifier(claims[2], other.citations)
    assert not _omits_evidence_event_qualifier(claims[3], same_event.citations)
    pairs = DefaultConflictCandidateService(
        QAStub(current, other, same_event),
        ClaimStub(tuple(CaseClaim("case", claim) for claim in claims)),
    ).generate("case", "v2", "fake")
    assert all("truncated-date" not in (
        item.left_claim.claim_id, item.right_claim.claim_id
    ) for item in pairs)
    assert any({item.left_claim.claim_id, item.right_claim.claim_id} == {
        "current-date", "same-date"
    } for item in pairs)
    assert any({item.left_claim.claim_id, item.right_claim.claim_id} == {
        "current-date", "other-date"
    } for item in pairs)


def test_date_qualifier_guard_preserves_claim_with_unqualified_source() -> None:
    answer = _answer("ordinal", "第一笔付款日期为2026年3月1日。", "e1")
    claim = _claim(answer, "date", "付款日期为2026年3月1日", "date")
    assert _omits_evidence_event_qualifier(claim, answer.citations)
    independent = AnswerCitation(
        "e2", answer.claims[0].claim_id, "doc-2", "page-2", "独立.pdf", 1,
        "付款日期为2026年3月1日。",
    )
    assert not _omits_evidence_event_qualifier(claim, (*answer.citations, independent))
    non_date = _claim(answer, "amount", "付款金额为450000元", "amount")
    assert not _omits_evidence_event_qualifier(non_date, answer.citations)
