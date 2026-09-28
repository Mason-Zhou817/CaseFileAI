"""Auditable Claim quality annotations; no LLM evaluation judge.

Literal scope anchors and exact annotated atomic forms are deterministic checks.
Other paraphrases remain unresolved for direct review rather than guessed correct.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from casefile_ai.application.claim_contracts import ClaimKind, ExtractedClaim
from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.evaluation.runner import GoldEvidence, _normalized


class ClaimQualityGold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: str = Field(min_length=1)
    category: ClaimKind
    core_match_groups: list[list[str]] = Field(min_length=1)
    required_scope_groups: list[list[str]]
    atomic_text_any: list[str] = Field(min_length=1)
    source: GoldEvidence
    exact_core_match: bool = False
    core_required: bool = True

    @model_validator(mode="after")
    def annotations_have_content(self) -> ClaimQualityGold:
        for group in self.core_match_groups + self.required_scope_groups + [self.atomic_text_any]:
            if not group or any(not _normalized(value) for value in group):
                raise ValueError("quality annotation groups must contain nonblank values")
        return self


def matching_gold(text: str, gold: ClaimQualityGold) -> bool:
    if gold.exact_core_match:
        return _normalized(text) in {_normalized(value) for value in gold.atomic_text_any}
    return all(
        any(_normalized(value) in _normalized(text) for value in group)
        for group in gold.core_match_groups
    )


def assess_claim(
    claim: ExtractedClaim,
    citations: list[AnswerCitation],
    annotations: list[ClaimQualityGold],
    version_keys: dict[str, str],
) -> dict[str, Any]:
    matches = [gold for gold in annotations if matching_gold(claim.text, gold)]
    row: dict[str, Any] = {
        "claim_id": claim.claim_id,
        "text": claim.text,
        "kind": claim.kind,
        "fact_key": None,
        "atomicity": None,
        "category_correct": None,
        "scope_preserved": None,
        "source_correct": None,
        "supported": None,
        "manual_review_required": True,
        "method": "unresolved",
    }
    if len(matches) != 1:
        return row
    gold = matches[0]
    selected = [
        citation
        for citation in citations
        if citation.evidence_id in claim.evidence_ids
        and citation.claim_id == claim.source_answer_claim_id
    ]
    row.update(
        fact_key=gold.fact_key,
        category_correct=claim.kind == gold.category,
        scope_preserved=all(
            any(_normalized(value) in _normalized(claim.text) for value in group)
            for group in gold.required_scope_groups
        ),
        atomicity=True
        if _normalized(claim.text) in {_normalized(value) for value in gold.atomic_text_any}
        else None,
        source_correct=bool(selected)
        and len(selected) == len(claim.evidence_ids)
        and all(
            version_keys.get(citation.document_version_id) == gold.source.doc_key
            and citation.page_number == gold.source.page
            and _normalized(gold.source.snippet) in _normalized(citation.quote)
            for citation in selected
        ),
        method="annotated core/scope/source rules; atomicity only exact approved forms",
    )
    row["manual_review_required"] = row["atomicity"] is None or row["supported"] is None
    return row


def quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def metric(field: str, *, inverse: bool = False) -> dict[str, Any]:
        resolved = [row[field] for row in rows if row[field] is not None]
        count = sum(not value if inverse else bool(value) for value in resolved)
        return {
            "numerator": count,
            "denominator": len(resolved),
            "value": round(count / len(resolved), 4) if resolved else None,
        }

    return {
        "actual_claim_count": len(rows),
        "claim_atomicity_accuracy": metric("atomicity"),
        "claim_category_accuracy": metric("category_correct"),
        "claim_scope_preservation": metric("scope_preserved"),
        "claim_source_accuracy": metric("source_correct"),
        "unsupported_claim_rate": metric("supported", inverse=True),
        "manual_review_count": sum(row["manual_review_required"] for row in rows),
        "metric_note": "rates cover resolved returned claims only; report missing gold/extraction "
        "failures separately; model's quality verdict is not a gold judgement",
    }


def fact_coverage(
    annotations: list[ClaimQualityGold], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Returned-Claim quality cannot conceal missing required facts or duplicates."""
    required = {gold.fact_key for gold in annotations if gold.core_required}
    found = [row["fact_key"] for row in rows if row["fact_key"] is not None]
    matched = required.intersection(found)
    return {
        "expected_core_count": len(required),
        "expected_all_count": len(annotations),
        "core_fact_recall": {
            "numerator": len(matched), "denominator": len(required),
            "value": round(len(matched) / len(required), 4) if required else None,
        },
        "missing_core_fact_keys": sorted(required.difference(found)),
        "duplicate_fact_keys": sorted({key for key in found if found.count(key) > 1}),
        "unmatched_claim_count": sum(row["fact_key"] is None for row in rows),
    }
