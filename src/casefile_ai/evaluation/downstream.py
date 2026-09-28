"""Gold-labelled downstream evaluation over the isolated application-service run."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date as calendar_date
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from casefile_ai.application.claim_contracts import (
    ConflictCandidate,
    ExtractedClaim,
    ModelClaimExtraction,
    ModelClaimValidation,
)
from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.application.contradiction_contracts import (
    ContradictionResult,
    ModelSemanticDecision,
)
from casefile_ai.application.retrieval_contracts import EmbeddingProvider
from casefile_ai.application.services.chunking import chunk_page
from casefile_ai.application.services.claims import (
    EXTRACTION_VERSION,
    DefaultClaimExtractionService,
)
from casefile_ai.application.services.conflict_candidates import DefaultConflictCandidateService
from casefile_ai.application.services.contradictions import (
    DefaultContradictionService,
    _amount_value,
    _date_value,
)
from casefile_ai.application.services.timeline import DefaultTimelineService, TimelineEvent
from casefile_ai.evaluation.gold_fake import GoldEvidenceFakeLLM
from casefile_ai.evaluation.runner import (
    EvaluationContext,
    EvaluationInputError,
    GoldEvidence,
    Question,
    _normalized,
    _read_inputs,
    run_evaluation,
)
from casefile_ai.infrastructure.db.claims import SqlAlchemyClaimRepository
from casefile_ai.infrastructure.db.contradictions import SqlAlchemyContradictionRepository
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.parsers.pdf import extract_pdf_pages


class NormalizedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int | None = Field(default=None, gt=0)
    currency: Literal["CNY", "USD"] | None = None
    date: str | None = Field(default=None, pattern=r"^\d{4}(-\d{2})?(-\d{2})?$")
    person: str | None = Field(default=None, min_length=1)
    action: Literal["paid", "unpaid", "conditional"] | None = None

    @model_validator(mode="after")
    def valid_calendar_date(self) -> NormalizedFact:
        if self.date is not None:
            _check_calendar_date(self.date)
        return self


def _check_calendar_date(value: str) -> None:
    parts = [int(part) for part in value.split("-")]
    try:
        calendar_date(parts[0], parts[1] if len(parts) > 1 else 1,
                      parts[2] if len(parts) > 2 else 1)
    except ValueError as exc:
        raise ValueError(f"invalid calendar date {value!r}") from exc


class ClaimGold(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_key: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    category: Literal["amount", "date", "person", "action"]
    expected_text_any: list[str] = Field(min_length=1)
    normalized: NormalizedFact
    source: GoldEvidence

    @model_validator(mode="after")
    def one_normalized_value(self) -> ClaimGold:
        values = self.normalized.model_dump(exclude_none=True)
        allowed = ({"amount", "currency"} if self.category == "amount" and
                   self.normalized.currency is not None else {self.category})
        if set(values) != allowed or not values[self.category]:
            raise ValueError("normalized must contain exactly the claim category")
        if any(not text.strip() for text in self.expected_text_any):
            raise ValueError("expected_text_any cannot contain blank text")
        return self


class PairGold(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pair_key: str = Field(min_length=1)
    claim_a: str = Field(min_length=1)
    claim_b: str = Field(min_length=1)
    expected_relation: Literal["contradiction", "non_contradiction", "uncertain"]
    contradiction_type: Literal["amount", "date", "person", "action"]
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_claims(self) -> PairGold:
        if self.claim_a == self.claim_b:
            raise ValueError("conflict pair must reference distinct claims")
        return self


class TimelineGold(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_key: str = Field(min_length=1)
    claim_key: str = Field(min_length=1)
    expected_date: str = Field(pattern=r"^\d{4}(-\d{2})?(-\d{2})?$")
    date_precision: Literal["exact", "partial"]

    @model_validator(mode="after")
    def coherent_precision(self) -> TimelineGold:
        if (self.date_precision == "exact") != (len(self.expected_date) == 10):
            raise ValueError("date_precision does not match expected_date")
        _check_calendar_date(self.expected_date)
        return self


class DownstreamGold(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    claims: list[ClaimGold] = Field(min_length=1)
    conflict_pairs: list[PairGold] = Field(min_length=1)
    timeline_events: list[TimelineGold] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_references(self) -> DownstreamGold:
        claims = {item.claim_key: item for item in self.claims}
        if len(claims) != len(self.claims):
            raise ValueError("claim_key values must be unique")
        if len({item.pair_key for item in self.conflict_pairs}) != len(self.conflict_pairs):
            raise ValueError("pair_key values must be unique")
        if len({item.event_key for item in self.timeline_events}) != len(self.timeline_events):
            raise ValueError("event_key values must be unique")
        pair_claims: set[frozenset[str]] = set()
        for pair in self.conflict_pairs:
            if pair.claim_a not in claims or pair.claim_b not in claims:
                raise ValueError(f"pair {pair.pair_key} references an unknown claim")
            if (claims[pair.claim_a].category != pair.contradiction_type
                    or claims[pair.claim_b].category != pair.contradiction_type):
                raise ValueError(f"pair {pair.pair_key} claim categories differ")
            identity = frozenset((pair.claim_a, pair.claim_b))
            if identity in pair_claims:
                raise ValueError("duplicate unordered conflict pair")
            pair_claims.add(identity)
        for event in self.timeline_events:
            if event.claim_key not in claims or claims[event.claim_key].category != "date":
                raise ValueError(f"timeline event {event.event_key} needs a date claim")
        return self


def load_downstream_gold(path: Path, case_set_path: Path,
                         questions_path: Path) -> tuple[DownstreamGold, list[Question]]:
    """Reject missing references and gold quotes before importing any document."""
    case_set, questions, paths = _read_inputs(case_set_path, questions_path)
    try:
        gold = DownstreamGold.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise EvaluationInputError(f"invalid downstream gold {path}: {exc}") from exc
    by_question = {item.id: item for item in questions}
    for item in gold.claims:
        question = by_question.get(item.question_id)
        if question is None or question.expected_status != "answered":
            raise EvaluationInputError(f"claim {item.claim_key}: missing answered question")
        if item.source not in question.gold_evidence:
            raise EvaluationInputError(f"claim {item.claim_key}: source differs from question")
        if item.source.doc_key not in paths:
            raise EvaluationInputError(f"claim {item.claim_key}: unknown document")
    for key in {item.source.doc_key for item in gold.claims}:
        pages = extract_pdf_pages(paths[key])
        for item in (claim for claim in gold.claims if claim.source.doc_key == key):
            if item.source.page > len(pages) or _normalized(item.source.snippet) not in (
                _normalized(pages[item.source.page - 1].text)
            ):
                raise EvaluationInputError(f"claim {item.claim_key}: gold page or quote missing")
    if case_set.allow_cloud_model:
        raise EvaluationInputError("downstream baseline must forbid cloud QA")
    return gold, questions


class GoldClaimFakeLLM:
    """Return an extractive claim only when its answer and source quote are present."""

    model_version = "downstream-extractive-fake-v1"

    def __init__(self, gold: DownstreamGold) -> None:
        self._claims = gold.claims

    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_model: type[Any]) -> Any:
        payload = json.loads(user_prompt)
        if response_model is ModelClaimValidation:
            supported = any(_normalized(payload["claim"]) in _normalized(item["quote"])
                            for item in payload["evidence"])
            return response_model.model_validate({
                "verdict": "supported" if supported else "unsupported",
                "reason": "extractive quote check",
                "atomicity": "matches", "scope_preservation": "matches",
                "category_matches": "matches",
            })
        if not issubclass(response_model, ModelClaimExtraction):
            raise TypeError("unexpected claim response model")
        answer = payload["answer_claim"]
        outputs: list[dict[str, Any]] = []
        for gold in self._claims:
            text = next((allowed for allowed in gold.expected_text_any
                         if allowed in answer), None)
            if text is None:
                continue
            citation = next((item["citation_id"] for item in payload["evidence"]
                             if gold.source.snippet in item["quote"]),
                            None)
            if citation is not None:
                outputs.append({"text": text, "kind": gold.category,
                                "citation_ids": [citation]})
        return response_model.model_validate({"claims": outputs})


class RuleSemanticFakeLLM:
    """Deterministic semantic boundary for action/person; the real judge still validates."""

    model_version = "downstream-semantic-rules-v1"

    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_model: type[Any]) -> Any:
        if response_model is not ModelSemanticDecision:
            raise TypeError("unexpected semantic response model")
        payload = json.loads(user_prompt)
        left, right = payload["left"]["claim"], payload["right"]["claim"]
        same_event = ("本次" in left and "本次" in right)
        verdict: str = "uncertain"
        left_occurrence = right_occurrence = "unknown"
        left_identity = right_identity = ""
        if payload["type"] == "action":
            def occurrence(text: str) -> str:
                if "未支付" in text:
                    return "did_not_occur"
                return "occurred" if "已支付" in text else "unknown"

            left_occurrence, right_occurrence = occurrence(left), occurrence(right)
            if same_event and "unknown" not in (left_occurrence, right_occurrence):
                verdict = ("contradiction" if left_occurrence != right_occurrence
                           else "non_contradiction")
        else:
            left_match = re.search(r"唯一付款人[是为]([^，。\s]+)", left)
            right_match = re.search(r"唯一付款人[是为]([^，。\s]+)", right)
            left_identity = left_match[1] if left_match else ""
            right_identity = right_match[1] if right_match else ""
            if same_event and left_identity and right_identity:
                verdict = ("contradiction" if left_identity != right_identity
                           else "non_contradiction")
        return response_model.model_validate({
            "verdict": verdict, "same_event": same_event,
            "same_fact_slot": same_event,
            "reason": "synthetic semantic boundary rule",
            "left_occurrence": left_occurrence, "right_occurrence": right_occurrence,
            "left_identity": left_identity, "right_identity": right_identity,
        })


class _CandidateSnapshot:
    def __init__(self, candidates: tuple[ConflictCandidate, ...]) -> None:
        self.candidates = candidates

    def generate(self, case_id: str, extraction_version: str,
                 model_version: str) -> tuple[ConflictCandidate, ...]:
        return self.candidates


def _fact_matches(claim: ExtractedClaim, gold: ClaimGold) -> bool:
    if claim.kind != gold.category:
        return False
    text = claim.text
    value = gold.normalized
    if gold.category == "amount":
        amount = _amount_value(text)
        return (amount is not None and amount[0] == value.amount and
                amount[1] == (value.currency or "CNY"))
    if gold.category == "date":
        date = _date_value(text)
        if date is None:
            return False
        numbers = date[0]
        actual = str(numbers[0]) + "".join(f"-{part:02d}" for part in numbers[1:3])
        return actual == value.date
    if gold.category == "person":
        return value.person is not None and value.person in text
    if gold.category == "action":
        if value.action == "conditional":
            return "后支付" in text and "已支付" not in text and "未支付" not in text
        if value.action == "unpaid":
            return "未支付" in text
        return "已支付" in text and "未支付" not in text
    return False


def _select_gold_claim(claims: tuple[ExtractedClaim, ...],
                       gold: ClaimGold) -> ExtractedClaim | None:
    """Prefer the annotated assertion when a page contains the same value twice."""
    matching = [claim for claim in claims if _fact_matches(claim, gold)]
    return next((claim for claim in matching if any(
        _normalized(claim.text) == _normalized(expected)
        for expected in gold.expected_text_any
    )), matching[0] if matching else None)


def _rate(records: list[dict[str, Any]], eligible: str, success: str) -> dict[str, Any]:
    selected = [item for item in records if item[eligible] is not None]
    numerator = sum(item[success] is True for item in selected)
    return {"numerator": numerator, "denominator": len(selected),
            "value": round(numerator / len(selected), 4) if selected else None}


def _first_failure(record: dict[str, Any], stages: tuple[tuple[str, str], ...]) -> str | None:
    for key, label in stages:
        if record[key] is False:
            return label
        if record[key] is None:
            return None
    return None


def _source_scores(citations: list[AnswerCitation], source: GoldEvidence,
                   document_version_id: str | None) -> tuple[bool, bool, bool]:
    document = any(citation.document_version_id == document_version_id
                   for citation in citations) if document_version_id else False
    page = any(citation.document_version_id == document_version_id and
               citation.page_number == source.page for citation in citations
               ) if document_version_id else False
    snippet = any(citation.document_version_id == document_version_id and
                  citation.page_number == source.page and
                  _normalized(source.snippet) in _normalized(citation.quote)
                  for citation in citations) if document_version_id else False
    return document, page, snippet


_CLAIM_STAGES = (
    ("document_present", "document"), ("parsed", "parse"),
    ("chunked", "chunking"), ("retrieved", "retrieval"),
    ("evidence", "evidence"), ("answer_validated", "validated_answer"),
    ("claim_extracted", "claim_extraction"), ("claim_fact_match", "claim_fact"),
    ("claim_source_document_match", "claim_source_document"),
    ("claim_source_page_match", "claim_source_page"),
    ("claim_evidence_snippet_match", "claim_evidence_snippet"),
)


def _claim_records(context: EvaluationContext, gold: DownstreamGold,
                   extractor: DefaultClaimExtractionService,
                   qa_repo: SqlAlchemyQARepository,
                   claim_repo: SqlAlchemyClaimRepository,
                   ) -> tuple[list[dict[str, Any]], dict[str, ExtractedClaim], int]:
    rows = {row["id"]: row for row in context.rows}
    extracted: dict[str, tuple[ExtractedClaim, ...]] = {}
    errors: dict[str, str] = {}
    for item in gold.claims:
        row = rows[item.question_id]
        answer_id = row["answer_id"]
        if row["validation_status"] != "validated" or not answer_id:
            continue
        try:
            extracted[item.question_id] = extractor.extract_answer_claims(answer_id)
        except Exception as exc:
            errors[item.question_id] = f"{type(exc).__name__}: {exc}"
    linked: dict[str, ExtractedClaim] = {}
    records: list[dict[str, Any]] = []
    for item in gold.claims:
        row = rows[item.question_id]
        source = item.source
        version = context.versions.get(source.doc_key)
        document_present = version is not None
        page_text: str | None = None
        if version is not None:
            try:
                page_text = context.ingestion.get_page(
                    version.document_id, source.page, version.document_version_id
                ).text
            except Exception:
                pass
        parsed = (page_text is not None and
                  _normalized(source.snippet) in _normalized(page_text))
        chunks = [chunk for page in context.chunks.pages_for_case(context.case_id)
                  if version is not None and
                  page.document_version_id == version.document_version_id and
                  page.page_number == source.page
                  for chunk in chunk_page(page)]
        matching_chunks = [chunk for chunk in chunks
                           if _normalized(source.snippet) in _normalized(chunk.text)]
        chunked = bool(matching_chunks) and not context.chunks.missing_chunks(
            [chunk.chunk_id for chunk in matching_chunks]
        )
        matching_hits = [hit for hit in row["retrieval_results"]
                         if version is not None and
                         hit["document_version_id"] == version.document_version_id and
                         hit["page"] == source.page and
                         _normalized(source.snippet) in _normalized(hit["text"])]
        retrieved = bool(matching_hits)
        evidence = any(hit["evidence_id"] in row["qa_evidence_ids"]
                       for hit in matching_hits)
        answer = qa_repo.get_answer(row["answer_id"]) if row["answer_id"] else None
        cited = [citation for citation in answer.citations
                 if version is not None and
                 citation.document_version_id == version.document_version_id and
                 citation.page_number == source.page and
                 _normalized(source.snippet) in _normalized(citation.quote)] if answer else []
        validated = row["validation_status"] == "validated" and bool(cited)
        claims = extracted.get(item.question_id, ())
        matched = _select_gold_claim(claims, item)
        claim_extracted = bool(claims)
        fact_match = matched is not None
        claim_citations = [citation for citation in answer.citations
                           if matched is not None and
                           citation.evidence_id in matched.evidence_ids] if answer else []
        source_doc: bool | None = None
        source_page: bool | None = None
        source_snippet: bool | None = None
        if fact_match:
            source_doc, source_page, source_snippet = _source_scores(
                claim_citations, source, version.document_version_id if version else None
            )
        record: dict[str, Any] = {
            "kind": "claim", "gold_id": item.claim_key, "category": item.category,
            "question_id": item.question_id,
            "document_present": document_present, "parsed": parsed, "chunked": chunked,
            "retrieved": retrieved, "evidence": evidence,
            "answer_validated": validated, "claim_extracted": claim_extracted,
            "claim_fact_match": fact_match,
            "claim_source_document_match": source_doc,
            "claim_source_page_match": source_page,
            "claim_evidence_snippet_match": source_snippet,
            "candidate_found": None, "contradiction_evaluated": None,
            "contradiction_correct": None, "timeline_found": None,
            "actual_claim_id": matched.claim_id if matched else None,
            "actual_claim_text": matched.text if matched else None,
            "actual_claims": [
                {"claim_id": claim.claim_id, "kind": claim.kind, "text": claim.text}
                for claim in claims
            ],
            "error": errors.get(item.question_id),
        }
        record["first_failure_stage"] = _first_failure(record, _CLAIM_STAGES)
        if fact_match and source_doc and source_page and source_snippet and matched:
            linked[item.claim_key] = matched
        records.append(record)
    # Read through the repository to ensure the persisted ClaimService boundary was used.
    actual_count = len(claim_repo.list_case_claims(
        context.case_id, EXTRACTION_VERSION, GoldClaimFakeLLM.model_version
    ))
    return records, linked, actual_count


def _pair_records(context: EvaluationContext, gold: DownstreamGold,
                  claim_records: list[dict[str, Any]], linked: dict[str, ExtractedClaim],
                  claim_repo: SqlAlchemyClaimRepository,
                  qa_repo: SqlAlchemyQARepository,
                  contradiction_repo: SqlAlchemyContradictionRepository,
                  ) -> tuple[list[dict[str, Any]], int | None]:
    generator = DefaultConflictCandidateService(qa_repo, claim_repo)
    candidate_error: str | None = None
    judge_error: str | None = None
    try:
        candidates = generator.generate(
            context.case_id, EXTRACTION_VERSION, GoldClaimFakeLLM.model_version
        )
    except Exception as exc:
        candidates = ()
        candidate_error = f"{type(exc).__name__}: {exc}"
    candidate_ids = {frozenset((item.left_claim.claim_id, item.right_claim.claim_id)):
                     item.candidate_id for item in candidates}
    results: dict[str, ContradictionResult] = {}
    if candidate_error is None:
        judge = DefaultContradictionService(
            _CandidateSnapshot(candidates), contradiction_repo, RuleSemanticFakeLLM()
        )
        try:
            results = {item.candidate_id: item for item in judge.detect_case(
                context.case_id, EXTRACTION_VERSION, GoldClaimFakeLLM.model_version
            )}
        except Exception as exc:
            judge_error = f"{type(exc).__name__}: {exc}"
    return (_score_pairs(gold, claim_records, linked, candidate_ids, results,
                         candidate_error, judge_error),
            len(candidates) if candidate_error is None else None)


def _score_pairs(gold: DownstreamGold, claim_records: list[dict[str, Any]],
                 linked: dict[str, ExtractedClaim],
                 candidate_ids: dict[frozenset[str], str],
                 results: dict[str, ContradictionResult],
                 candidate_error: str | None = None,
                 judge_error: str | None = None) -> list[dict[str, Any]]:
    by_claim = {item["gold_id"]: item for item in claim_records}
    records: list[dict[str, Any]] = []
    for pair in gold.conflict_pairs:
        left, right = by_claim[pair.claim_a], by_claim[pair.claim_b]
        failures = [item["first_failure_stage"] for item in (left, right)
                    if item["first_failure_stage"] is not None]
        order = [label for _, label in _CLAIM_STAGES]
        first = min(failures, key=order.index) if failures else None
        claim_a, claim_b = linked.get(pair.claim_a), linked.get(pair.claim_b)
        eligible = claim_a is not None and claim_b is not None
        candidate_id = candidate_ids.get(frozenset((claim_a.claim_id, claim_b.claim_id))) if (
            claim_a is not None and claim_b is not None
        ) else None
        result = results.get(candidate_id) if candidate_id else None
        record: dict[str, Any] = {
            "kind": "conflict_pair", "gold_id": pair.pair_key,
            "category": pair.contradiction_type,
            "claim_keys": [pair.claim_a, pair.claim_b],
            "expected_relation": pair.expected_relation,
            "document_present": all(item["document_present"] for item in (left, right)),
            "parsed": all(item["parsed"] for item in (left, right)),
            "chunked": all(item["chunked"] for item in (left, right)),
            "retrieved": all(item["retrieved"] for item in (left, right)),
            "evidence": all(item["evidence"] for item in (left, right)),
            "answer_validated": all(item["answer_validated"] for item in (left, right)),
            "claim_found": eligible, "claim_pair_ready": eligible,
            "candidate_found": candidate_id is not None if eligible else None,
            "contradiction_evaluated": result is not None if candidate_id else None,
            "contradiction_correct": (result.verdict == pair.expected_relation
                                      if result is not None else None),
            "actual_relation": result.verdict if result else None,
            "decision_basis": result.basis if result else None,
            "error": (candidate_error if candidate_id is None else judge_error),
            "timeline_found": None,
            "first_failure_stage": first,
        }
        if first is None:
            record["first_failure_stage"] = _first_failure(record, (
                ("candidate_found", "candidate_generation"),
                ("contradiction_evaluated", "contradiction_judge"),
                ("contradiction_correct", "contradiction_decision"),
            ))
        records.append(record)
    return records


def _timeline_records(context: EvaluationContext, gold: DownstreamGold,
                      claim_records: list[dict[str, Any]], linked: dict[str, ExtractedClaim],
                      qa_repo: SqlAlchemyQARepository,
                      claim_repo: SqlAlchemyClaimRepository,
                      contradiction_repo: SqlAlchemyContradictionRepository,
                      ) -> tuple[list[dict[str, Any]], dict[str, TimelineEvent]]:
    timeline_error: str | None = None
    try:
        events = DefaultTimelineService(qa_repo, claim_repo, contradiction_repo).build(
            context.case_id, EXTRACTION_VERSION, GoldClaimFakeLLM.model_version
        )
    except Exception as exc:
        events = ()
        timeline_error = f"{type(exc).__name__}: {exc}"
    by_actual = {event.claim_id: event for event in events}
    by_claim = {item["gold_id"]: item for item in claim_records}
    matched_events: dict[str, TimelineEvent] = {}
    records: list[dict[str, Any]] = []
    for item in gold.timeline_events:
        claim_row = by_claim[item.claim_key]
        claim = linked.get(item.claim_key)
        event = by_actual.get(claim.claim_id) if claim else None
        found = event is not None if claim else None
        source = next(claim_gold.source for claim_gold in gold.claims
                      if claim_gold.claim_key == item.claim_key)
        version = context.versions.get(source.doc_key)
        source_match = (any(version is not None and
                            citation.document_version_id == version.document_version_id and
                            citation.page_number == source.page and
                            _normalized(source.snippet) in _normalized(citation.quote)
                            for citation in event.evidence) if event else None)
        date_match = (event.date_value == item.expected_date and
                      event.date_status == item.date_precision) if event else None
        record: dict[str, Any] = {
            "kind": "timeline_event", "gold_id": item.event_key,
            "claim_key": item.claim_key,
            "document_present": claim_row["document_present"],
            "parsed": claim_row["parsed"], "chunked": claim_row["chunked"],
            "retrieved": claim_row["retrieved"], "evidence": claim_row["evidence"],
            "answer_validated": claim_row["answer_validated"],
            "claim_found": claim is not None,
            "candidate_found": None, "contradiction_evaluated": None,
            "timeline_found": found,
            "timeline_date_match": date_match, "timeline_source_match": source_match,
            "expected_date": item.expected_date,
            "actual_event_id": event.event_id if event else None,
            "actual_fact": event.fact if event else None,
            "actual_date": event.date_value if event else None,
            "error": timeline_error,
            "first_failure_stage": claim_row["first_failure_stage"],
        }
        if record["first_failure_stage"] is None:
            record["first_failure_stage"] = _first_failure(record, (
                ("timeline_found", "timeline"),
                ("timeline_date_match", "timeline_date"),
                ("timeline_source_match", "timeline_source"),
            ))
        if event:
            matched_events[item.event_key] = event
        records.append(record)
    return records, matched_events


def _summary(gold: DownstreamGold, records: list[dict[str, Any]],
             actual_claim_count: int, events: dict[str, TimelineEvent],
             gold_sha256: str, candidate_total: int | None = None) -> dict[str, Any]:
    claims = [item for item in records if item["kind"] == "claim"]
    pairs = [item for item in records if item["kind"] == "conflict_pair"]
    timeline = [item for item in records if item["kind"] == "timeline_event"]
    conflicts = [item for item in pairs if item["expected_relation"] == "contradiction"]
    order_checks: list[dict[str, bool | None]] = []
    expected_events = {item.event_key: item for item in gold.timeline_events}
    for left, right in combinations(gold.timeline_events, 2):
        if left.expected_date == right.expected_date:
            continue
        actual_left, actual_right = events.get(left.event_key), events.get(right.event_key)
        if actual_left is None or actual_right is None:
            continue
        actual_order = (actual_left.date_value or "") < (actual_right.date_value or "")
        gold_order = (expected_events[left.event_key].expected_date <
                      expected_events[right.event_key].expected_date)
        order_checks.append({"eligible": True, "correct": actual_order == gold_order})
    # For recall, every gold item is applicable; missing upstream stages count as misses.
    def recall(items: list[dict[str, Any]], key: str) -> dict[str, Any]:
        count = sum(item[key] is True for item in items)
        return {"numerator": count, "denominator": len(items),
                "value": round(count / len(items), 4) if items else None}

    by_type = {kind: {
        "expected_conflicting_pairs": sum(item["category"] == kind for item in conflicts),
        "candidate_pair_recall": recall(
            [item for item in conflicts if item["category"] == kind and
             item["claim_pair_ready"] is True], "candidate_found"
        ),
        "decision_accuracy_given_candidate": _rate(
            [item for item in pairs if item["category"] == kind and
             item["candidate_found"] is True], "contradiction_evaluated",
            "contradiction_correct"
        ),
        "end_to_end_contradiction_recall": recall(
            [item for item in conflicts if item["category"] == kind],
            "contradiction_correct"
        ),
    } for kind in ("amount", "date", "person", "action")}
    return {
        "evaluation_set": gold.name, "gold_sha256": gold_sha256,
        "provider_versions": {
            "qa": GoldEvidenceFakeLLM.model_version,
            "claim": GoldClaimFakeLLM.model_version,
            "semantic": RuleSemanticFakeLLM.model_version,
        },
        "claim": {
            "expected_count": len(claims), "actual_count": actual_claim_count,
            "fact_recall": recall(claims, "claim_fact_match"),
            "source_document_accuracy": _rate(
                claims, "claim_source_document_match", "claim_source_document_match"),
            "source_page_accuracy": _rate(
                claims, "claim_source_page_match", "claim_source_page_match"),
            "evidence_snippet_accuracy": _rate(
                claims, "claim_evidence_snippet_match", "claim_evidence_snippet_match"),
            "by_category": {kind: recall(
                [item for item in claims if item["category"] == kind], "claim_fact_match"
            ) for kind in ("amount", "date", "person", "action")},
        },
        "candidate": {
            "expected_conflicting_pairs": len(conflicts),
            "claim_pairs_ready": sum(item["claim_pair_ready"] is True
                                     for item in conflicts),
            "actual_pairs": candidate_total,
            "annotated_pairs_found": sum(item["candidate_found"] is True
                                         for item in pairs),
            "unlabelled_pairs": (candidate_total - sum(
                item["candidate_found"] is True for item in pairs
            ) if candidate_total is not None else None),
            "precision": None,
            "pair_recall": recall(
                [item for item in conflicts if item["claim_pair_ready"] is True],
                "candidate_found"
            ),
        },
        "contradiction": {
            "expected_pairs_all_relations": len(pairs),
            "decision_accuracy_given_candidate": _rate(
                [item for item in pairs if item["candidate_found"] is True],
                "contradiction_evaluated", "contradiction_correct"),
            "end_to_end_contradiction_recall": recall(
                conflicts, "contradiction_correct"),
            "by_type": by_type,
        },
        "timeline": {
            "expected_events": len(timeline),
            "event_recall": recall(timeline, "timeline_found"),
            "date_accuracy": _rate(timeline, "timeline_date_match", "timeline_date_match"),
            "source_accuracy": _rate(
                timeline, "timeline_source_match", "timeline_source_match"),
            "order_accuracy": _rate(order_checks, "eligible", "correct"),
            "same_date_gold_pairs_excluded_from_order": sum(
                left.expected_date == right.expected_date
                for left, right in combinations(gold.timeline_events, 2)
            ),
            "duplicate_matched_event_count": (
                len(events) - len({
                    (item.date_value, _normalized(item.fact)) for item in events.values()
                }) if all(hasattr(item, "fact") for item in events.values()) else None
            ),
        },
        "pipeline": {
            "first_failure_stage_counts": dict(sorted(Counter(
                item["first_failure_stage"] or "none" for item in records
            ).items())),
            "record_count": len(records),
        },
        "metric_notes": {
            "claim_source_denominator": "fact-matched claims only",
            "candidate_recall_denominator": (
                "gold contradiction pairs with both facts and sources matched; "
                "claim_pairs_ready reports this denominator"
            ),
            "decision_denominator": "gold pairs entering candidate set and evaluated by judge",
            "end_to_end_denominator": "all gold contradiction pairs",
            "timeline_recall_denominator": "all gold timeline events",
            "timeline_order_denominator": (
                "comparable gold event pairs with both events present; ties excluded"
            ),
            "fake_boundary": (
                "gold-gated QA and claim extraction; rule semantic fake for action/person"
            ),
            "business_services": "real claim validation/persistence, candidate generation, "
                                 "amount/date judge, semantic decision safeguards, timeline",
        },
    }


def run_downstream_evaluation(case_set_path: Path, questions_path: Path,
                              gold_path: Path, output_dir: Path, *,
                              embeddings: EmbeddingProvider | None = None,
                              ) -> dict[str, Any]:
    """Run all stages in one ephemeral case; write separate readable reports."""
    gold, questions = load_downstream_gold(gold_path, case_set_path, questions_path)
    downstream_records: list[dict[str, Any]] = []
    downstream_summary: dict[str, Any] = {}

    def after_questions(context: EvaluationContext) -> None:
        qa_repo = SqlAlchemyQARepository(context.factory)
        claim_repo = SqlAlchemyClaimRepository(context.factory)
        contradiction_repo = SqlAlchemyContradictionRepository(context.factory)
        extractor = DefaultClaimExtractionService(
            qa_repo, claim_repo, GoldClaimFakeLLM(gold)
        )
        claims, linked, actual_count = _claim_records(
            context, gold, extractor, qa_repo, claim_repo
        )
        pairs, candidate_total = _pair_records(
            context, gold, claims, linked, claim_repo, qa_repo, contradiction_repo
        )
        timeline, events = _timeline_records(
            context, gold, claims, linked, qa_repo, claim_repo, contradiction_repo
        )
        downstream_records.extend((*claims, *pairs, *timeline))
        downstream_summary.update(_summary(
            gold, downstream_records, actual_count, events,
            hashlib.sha256(gold_path.read_bytes()).hexdigest(),
            candidate_total,
        ))

    qa_summary = run_evaluation(case_set_path, questions_path, output_dir,
                                llm=GoldEvidenceFakeLLM(questions),
                                embeddings=embeddings, on_complete=after_questions)
    if not downstream_records:
        question_rows = [json.loads(line) for line in (output_dir / "results.jsonl").read_text(
            encoding="utf-8").splitlines()]
        failure = next((row["failure_stage"] for row in question_rows
                        if row["failure_stage"] in {"import", "index"}), "evaluation")
        for claim_gold in gold.claims:
            downstream_records.append({
                "kind": "claim", "gold_id": claim_gold.claim_key,
                "category": claim_gold.category,
                "question_id": claim_gold.question_id, "document_present": True,
                "parsed": None, "chunked": None, "retrieved": None,
                "evidence": None, "answer_validated": None,
                "claim_extracted": None, "claim_fact_match": None,
                "claim_source_document_match": None,
                "claim_source_page_match": None,
                "claim_evidence_snippet_match": None,
                "candidate_found": None, "contradiction_evaluated": None,
                "timeline_found": None, "first_failure_stage": failure,
            })
        for pair in gold.conflict_pairs:
            downstream_records.append({
                "kind": "conflict_pair", "gold_id": pair.pair_key,
                "category": pair.contradiction_type,
                "claim_keys": [pair.claim_a, pair.claim_b],
                "expected_relation": pair.expected_relation,
                "claim_pair_ready": False, "candidate_found": None,
                "contradiction_evaluated": None, "contradiction_correct": None,
                "first_failure_stage": failure,
            })
        for event_gold in gold.timeline_events:
            downstream_records.append({
                "kind": "timeline_event", "gold_id": event_gold.event_key,
                "claim_key": event_gold.claim_key, "timeline_found": None,
                "timeline_date_match": None, "timeline_source_match": None,
                "first_failure_stage": failure,
            })
        downstream_summary.update(_summary(
            gold, downstream_records, 0, {},
            hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        ))
    downstream_summary.update({
        "run_timestamp": qa_summary["run_timestamp"],
        "inputs": qa_summary["inputs"],
        "retrieval_k": qa_summary["retrieval_k"],
        "qa_failure_count": qa_summary["failure_count"],
    })
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "downstream_results.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n"
                for item in downstream_records), encoding="utf-8"
    )
    (output_dir / "downstream_summary.json").write_text(
        json.dumps(downstream_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return downstream_summary
