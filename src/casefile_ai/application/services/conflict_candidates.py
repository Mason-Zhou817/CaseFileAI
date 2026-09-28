"""Explainable, case-scoped Claim pairs for later contradiction analysis."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Protocol

import jieba  # type: ignore[import-untyped]

from casefile_ai.application.claim_contracts import (
    CaseClaim,
    ConflictCandidate,
    ExtractedClaim,
)
from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.application.services.claim_coverage import (
    AnswerLookup,
    ClaimLookup,
    DefaultClaimCoverageService,
)

_TOPICS = (
    ("receipt", re.compile(r"收到|到账|入账|收款")),
    ("payment", re.compile(r"支付|付款|交易|转账|汇款")),
    ("contract", re.compile(r"合同|签约|签订")),
    ("investment", re.compile(r"投资|出资")),
)
_VALUE = re.compile(r"\d+(?:\.\d+)?(?:年|月|日|元|万元|亿元)?")
_EVENT_QUALIFIER = re.compile(
    r"(?:另一笔|另外一笔|另一次|第[一二三四五六七八九十百\d]+[笔次])$"
)
_STOPWORDS = {"金额", "日期", "时间", "人物", "主体", "行为", "发生", "是否", "为", "没有"}


class CaseClaimLookup(ClaimLookup, Protocol):
    def list_case_claims(
        self, case_id: str, extraction_version: str, model_version: str
    ) -> tuple[CaseClaim, ...]: ...


def _topics(text: str) -> tuple[str, ...]:
    matched = tuple(name for name, pattern in _TOPICS if pattern.search(text))
    if matched:
        return matched
    redacted = _VALUE.sub("", text)
    return tuple(
        f"term:{token}"
        for token in sorted(
            {
                token
                for token in jieba.cut(redacted)
                if len(token) >= 2 and token not in _STOPWORDS and token.isalpha()
            }
        )
    )


def _omits_evidence_event_qualifier(
    claim: ExtractedClaim, evidence: tuple[AnswerCitation, ...]
) -> bool:
    """Reject a date subclaim whose source occurrences lose an event qualifier."""
    if claim.kind != "date":
        return False
    occurrences: list[bool] = []
    for citation in evidence:
        for match in re.finditer(re.escape(claim.text), citation.quote):
            prefix = "".join(citation.quote[max(0, match.start() - 12):match.start()].split())
            occurrences.append(bool(_EVENT_QUALIFIER.search(prefix)))
    return bool(occurrences) and all(occurrences)


class DefaultConflictCandidateService:
    def __init__(self, qa_repository: AnswerLookup, claim_repository: CaseClaimLookup) -> None:
        self._qa_repository = qa_repository
        self._claim_repository = claim_repository
        self._coverage = DefaultClaimCoverageService(qa_repository, claim_repository)

    def generate(
        self, case_id: str, extraction_version: str, model_version: str
    ) -> tuple[ConflictCandidate, ...]:
        by_id: dict[str, tuple[ExtractedClaim, tuple[AnswerCitation, ...]]] = {}
        checked_answers: dict[str, dict[str, tuple[AnswerCitation, ...]]] = {}
        for item in self._claim_repository.list_case_claims(
            case_id, extraction_version, model_version
        ):
            claim = item.claim
            if (
                item.case_id != case_id
                or claim.extraction_version != extraction_version
                or claim.model_version != model_version
                or claim.kind == "other"
            ):
                continue
            if claim.answer_id not in checked_answers:
                result = self._coverage.evaluate(
                    claim.answer_id, extraction_version, model_version
                )
                checked_answers[claim.answer_id] = {
                    entry.claim_id: entry.evidence
                    for entry in result.claims
                    if entry.issue is None
                }
            evidence = checked_answers[claim.answer_id].get(claim.claim_id)
            if evidence and not _omits_evidence_event_qualifier(claim, evidence):
                by_id[claim.claim_id] = (claim, evidence)

        buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
        for claim_id, (claim, _) in by_id.items():
            for topic in _topics(claim.text):
                buckets[(claim.kind, topic)].append(claim_id)

        pairs: dict[tuple[str, str], ConflictCandidate] = {}
        for (_, topic), ids in sorted(buckets.items()):
            unique_ids = sorted(set(ids))
            for left_index, left_id in enumerate(unique_ids):
                for right_id in unique_ids[left_index + 1 :]:
                    left, left_evidence = by_id[left_id]
                    right, right_evidence = by_id[right_id]
                    if "".join(left.text.split()).rstrip("。") == "".join(
                        right.text.split()
                    ).rstrip("。"):
                        continue
                    pair_id = (left_id, right_id)
                    pairs.setdefault(
                        pair_id,
                        ConflictCandidate(
                            case_id=case_id,
                            left_claim=left,
                            right_claim=right,
                            left_evidence=left_evidence,
                            right_evidence=right_evidence,
                            shared_topic=topic,
                        ),
                    )
        return tuple(pairs[pair_id] for pair_id in sorted(pairs))
