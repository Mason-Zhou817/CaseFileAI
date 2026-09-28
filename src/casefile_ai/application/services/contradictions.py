"""Conservative, provenance-bound decisions over generated ConflictCandidates."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Protocol

from pydantic import ValidationError

from casefile_ai.application.claim_contracts import ConflictCandidate
from casefile_ai.application.contradiction_contracts import (
    ContradictionRepository,
    ContradictionResult,
    ContradictionValidationError,
    ModelSemanticDecision,
    Verdict,
)
from casefile_ai.application.qa_contracts import StructuredLLMProvider
from casefile_ai.domain.ids import derived_id

DETECTOR_VERSION = "candidate-contradiction-v2"
PROMPT_VERSION = "semantic-contradiction-v1"
_DATE = re.compile(
    r"(?<!\d)(\d{4})(?:年(?:([01]?\d)月(?:([0-3]?\d)日?)?)?|[-/]([01]?\d)(?:[-/]([0-3]?\d))?)(?!\d)"
)
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_AMOUNT = re.compile(
    r"(?<![\d.,])(?:(人民币|RMB|CNY|¥|￥|美元|USD|\$)\s*)?"
    r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*"
    r"(亿|万|千)?\s*(元|美元)?(?![\d.,])",
    re.IGNORECASE,
)
_SPACE_PUNCT = re.compile(r"[\s，,。；;：:（）()\[\]【】]+")
_EXCLUSIVE_IDENTITY = re.compile(r"唯一|仅有|只有|独自|仅由")
_SEMANTIC_PROMPT = """只比较给定的两个已验证事实和原文证据，不引入外部事实。
判断是否同一事件、同一事实槽位。候选仅表示值得比较，不代表必然矛盾。
所有字段都必须输出。行为类：分别填写 left_occurrence 和 right_occurrence 为
occurred / did_not_occur / unknown；left_identity 和 right_identity 填空字符串。
人物类：分别从两侧原文填写 left_identity 和 right_identity；
left_occurrence 和 right_occurrence 填 unknown。别名/OCR变化须保守。
不同事件、不同角色返回 non_contradiction；证据不足或无法确定返回 uncertain。
只有同一事件、同一槽位且明确不兼容才能返回 contradiction。
不要输出或发明任何 Claim、Evidence、citation ID，只输出指定结构。"""


class CandidateSource(Protocol):
    def generate(
        self, case_id: str, extraction_version: str, model_version: str
    ) -> tuple[ConflictCandidate, ...]: ...


def _scope(text: str, value_span: tuple[int, int]) -> str:
    remaining = text[: value_span[0]] + text[value_span[1] :]
    remaining = _TIME.sub("", remaining)
    return _SPACE_PUNCT.sub("", remaining).rstrip("为是于的")


def _date_value(text: str) -> tuple[tuple[int, ...], tuple[int, int], str] | None:
    matches = list(_DATE.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    year = int(match[1])
    month_raw = match[2] or match[4]
    day_raw = match[3] or match[5]
    month = int(month_raw) if month_raw else None
    day = int(day_raw) if day_raw else None
    try:
        date(year, month or 1, day or 1)
    except ValueError:
        return None
    value = (year,) + ((month,) if month is not None else ()) + ((day,) if day is not None else ())
    time_matches = list(_TIME.finditer(text))
    if len(time_matches) > 1:
        return None
    if time_matches and day is not None:
        value += (int(time_matches[0][1]), int(time_matches[0][2]))
    return value, match.span(), _scope(text, match.span())


def _amount_value(text: str) -> tuple[Decimal, str, str] | None:
    matches = list(_AMOUNT.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    if match.start() > 0 and text[match.start() - 1] in "-−负":
        return None
    prefix = (match[1] or "").upper()
    suffix = match[5] or ""
    prefix_currency = (
        "USD" if prefix in {"美元", "USD", "$"} else
        "CNY" if prefix in {"人民币", "RMB", "CNY", "¥", "￥"} else None
    )
    suffix_currency = "USD" if suffix == "美元" else "CNY" if suffix == "元" else None
    if prefix_currency and suffix_currency and prefix_currency != suffix_currency:
        return None
    currency = prefix_currency or suffix_currency or "unspecified"
    try:
        number = Decimal(match[2].replace(",", "") + ("." + match[3] if match[3] else ""))
    except InvalidOperation:
        return None
    factor = {None: 1, "千": 1000, "万": 10000, "亿": 100000000}[match[4]]
    return number * factor, currency, _scope(text, match.span())


def _clock_value(text: str) -> tuple[tuple[int, int], str] | None:
    matches = list(_TIME.finditer(text))
    if len(matches) != 1 or _DATE.search(text):
        return None
    match = matches[0]
    return (int(match[1]), int(match[2])), _scope(text, match.span())


def _deterministic(candidate: ConflictCandidate) -> tuple[Verdict, str, str]:
    left_text, right_text = candidate.left_claim.text, candidate.right_claim.text
    if candidate.left_claim.kind == "date":
        left, right = _date_value(left_text), _date_value(right_text)
        if left is None or right is None:
            left_clock, right_clock = _clock_value(left_text), _clock_value(right_text)
            if (left_clock is not None and right_clock is not None
                and left_clock[1] == right_clock[1] and left_clock[1]):
                return (
                    ("contradiction", "同一时间槽位的明确时刻不兼容", "normalized_time_mismatch")
                    if left_clock[0] != right_clock[0] else
                    ("non_contradiction", "规范化后的时刻相同", "normalized_time_equal")
                )
            return "uncertain", "日期无法可靠规范化", "date_parse_incomplete"
        if left[2] != right[2] or not left[2]:
            return "uncertain", "事件或日期槽位无法可靠对齐", "date_scope_unaligned"
        common = min(len(left[0]), len(right[0]))
        conflict = left[0][:common] != right[0][:common]
        return (
            ("contradiction", "同一日期槽位的明确时间值不兼容", "normalized_date_mismatch")
            if conflict else
            ("non_contradiction", "规范化后的日期相容", "normalized_date_compatible")
        )
    left_amount, right_amount = _amount_value(left_text), _amount_value(right_text)
    if left_amount is None or right_amount is None:
        return "uncertain", "金额或单位无法可靠规范化", "amount_parse_incomplete"
    if left_amount[2] != right_amount[2] or not left_amount[2]:
        return "uncertain", "交易事件或金额字段无法可靠对齐", "amount_scope_unaligned"
    if left_amount[1] != right_amount[1]:
        return "uncertain", "币种不一致或一侧未注明", "currency_unaligned"
    return (
        ("contradiction", "同一金额槽位的数值不兼容", "normalized_amount_mismatch")
        if left_amount[0] != right_amount[0] else
        ("non_contradiction", "规范化后的金额相同", "normalized_amount_equal")
    )


class DefaultContradictionService:
    def __init__(
        self,
        candidates: CandidateSource,
        repository: ContradictionRepository,
        llm: StructuredLLMProvider | None = None,
    ) -> None:
        self._candidates = candidates
        self._repository = repository
        self._llm = llm

    def detect_case(
        self, case_id: str, extraction_version: str, model_version: str
    ) -> tuple[ContradictionResult, ...]:
        results: list[ContradictionResult] = []
        for candidate in self._candidates.generate(case_id, extraction_version, model_version):
            self._validate_candidate(candidate, case_id, extraction_version, model_version)
            kind = candidate.left_claim.kind
            result_id = derived_id(
                "contradiction-result", candidate.candidate_id, DETECTOR_VERSION,
                PROMPT_VERSION if kind in {"action", "person"} else "deterministic",
                self._llm.model_version if kind in {"action", "person"} and self._llm else "none",
            )
            existing = self._repository.get_result(result_id)
            if existing is not None:
                if (
                    existing.case_id != case_id
                    or existing.candidate_id != candidate.candidate_id
                    or existing.left_claim_id != candidate.left_claim.claim_id
                    or existing.right_claim_id != candidate.right_claim.claim_id
                    or existing.contradiction_type != kind
                    or existing.left_evidence != candidate.left_evidence
                    or existing.right_evidence != candidate.right_evidence
                    or existing.extraction_version != extraction_version
                    or existing.claim_model_version != model_version
                ):
                    raise ContradictionValidationError("cached result does not match candidate")
                results.append(existing)
                continue
            if kind in {"date", "amount"}:
                verdict, reason, basis = _deterministic(candidate)
                prompt_version, decision_model = "none", "none"
            else:
                verdict, reason, basis = self._semantic(candidate)
                prompt_version = PROMPT_VERSION
                decision_model = self._llm.model_version if self._llm else "unavailable"
            result = ContradictionResult(
                result_id, case_id, candidate.candidate_id,
                candidate.left_claim.claim_id, candidate.right_claim.claim_id,
                kind, verdict, reason, basis,
                candidate.left_evidence, candidate.right_evidence,
                DETECTOR_VERSION, prompt_version, decision_model,
                extraction_version, model_version,
            )
            if basis in {"model_unavailable", "model_output_invalid"}:
                results.append(result)
            else:
                results.append(self._repository.save_result(result))
        return tuple(results)

    @staticmethod
    def _validate_candidate(
        candidate: ConflictCandidate, case_id: str, extraction_version: str,
        model_version: str,
    ) -> None:
        left, right = candidate.left_claim, candidate.right_claim
        if (candidate.case_id != case_id or left.claim_id == right.claim_id
            or left.kind != right.kind or left.kind not in {"date", "amount", "action", "person"}
            or any(claim.extraction_version != extraction_version or
                   claim.model_version != model_version for claim in (left, right))):
            raise ContradictionValidationError("candidate case, claims, or factual type invalid")
        for claim, evidence in ((left, candidate.left_evidence), (right, candidate.right_evidence)):
            ids = tuple(item.evidence_id for item in evidence)
            if not ids or len(ids) != len(set(ids)) or set(ids) != set(claim.evidence_ids):
                raise ContradictionValidationError(
                    "candidate evidence differs from validated claim"
                )
            if any(
                item.claim_id != claim.source_answer_claim_id
                or not item.filename or item.page_number < 1 or not item.quote
                for item in evidence
            ):
                raise ContradictionValidationError("candidate citation provenance invalid")

    def _semantic(self, candidate: ConflictCandidate) -> tuple[Verdict, str, str]:
        if self._llm is None:
            return "uncertain", "未配置语义判断模型", "model_unavailable"
        payload = {
            "type": candidate.left_claim.kind,
            "shared_topic": candidate.shared_topic,
            "left": {"claim": candidate.left_claim.text,
                     "evidence": [item.quote for item in candidate.left_evidence]},
            "right": {"claim": candidate.right_claim.text,
                      "evidence": [item.quote for item in candidate.right_evidence]},
        }
        try:
            raw = self._llm.generate_structured(
                system_prompt=_SEMANTIC_PROMPT,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                response_model=ModelSemanticDecision,
            )
            if not isinstance(raw, ModelSemanticDecision):
                raise TypeError("wrong model decision type")
            decision = ModelSemanticDecision.model_validate(raw.model_dump())
        except (Exception, ValidationError):
            return "uncertain", "模型结构化输出无效或不可用", "model_output_invalid"
        if decision.verdict == "uncertain":
            return "uncertain", decision.reason, "model_uncertain"
        if not decision.same_event or not decision.same_fact_slot:
            return "non_contradiction", decision.reason, "different_event_or_slot"
        if decision.verdict == "non_contradiction":
            return "non_contradiction", decision.reason, "model_compatible"
        if candidate.left_claim.kind == "action":
            opposite = {decision.left_occurrence, decision.right_occurrence} == {
                "occurred", "did_not_occur"
            }
            if not opposite:
                return "uncertain", "行为发生状态未构成明确对立", "action_polarity_unverified"
        else:
            left, right = decision.left_identity.strip(), decision.right_identity.strip()
            if not left or not right or left == right:
                return "uncertain", "主体身份未构成明确不兼容", "identity_unverified"
            if (len(left) > 100 or len(right) > 100
                or any(mark in value for value in (left, right) for mark in "，。；：")
                or left not in candidate.left_claim.text
                or right not in candidate.right_claim.text):
                return "uncertain", "主体身份并非两侧 Claim 的原文身份", "identity_not_extractive"
            if (left in right or right in left
                or SequenceMatcher(None, left, right).ratio() >= 0.8):
                return "uncertain", "主体名称可能是简称或 OCR 变化", "identity_alias_possible"
            if not any(
                _EXCLUSIVE_IDENTITY.search(claim.text)
                for claim in (candidate.left_claim, candidate.right_claim)
            ):
                return "uncertain", "原文未明确该主体槽位具有排他性", "identity_not_exclusive"
        return "contradiction", decision.reason, "model_incompatible_same_slot"
