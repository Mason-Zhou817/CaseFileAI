"""Synthetic stress diagnostics layered on the existing isolated evaluation run."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from casefile_ai.application.claim_contracts import ExtractedClaim
from casefile_ai.application.retrieval_contracts import EmbeddingProvider, IndexablePage
from casefile_ai.application.services.chunking import chunk_page
from casefile_ai.evaluation.downstream import (
    DownstreamGold,
    _fact_matches,
    load_downstream_gold,
    run_downstream_evaluation,
)
from casefile_ai.evaluation.runner import (
    EvaluationInputError,
    GoldEvidence,
    Question,
    _normalized,
    _percentile,
    _read_inputs,
)
from casefile_ai.infrastructure.parsers.pdf import ExtractedPage, extract_pdf_pages


class OCRToken(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    token_id: str = Field(min_length=1)
    doc_key: str = Field(min_length=1)
    page: int = Field(ge=1)
    expected: str = Field(min_length=1)
    category: str = Field(min_length=1)


class StressGold(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    ocr_tokens: list[OCRToken] = Field(min_length=1)
    long_doc_key: str = Field(min_length=1)
    long_gold_pages: list[int] = Field(min_length=5)

    @model_validator(mode="after")
    def unique_tokens(self) -> StressGold:
        if len({item.token_id for item in self.ocr_tokens}) != len(self.ocr_tokens):
            raise ValueError("ocr token_id values must be unique")
        if len(set(self.long_gold_pages)) != len(self.long_gold_pages):
            raise ValueError("long_gold_pages must be unique")
        return self


def load_stress_gold(path: Path, case_set_path: Path,
                     questions_path: Path) -> StressGold:
    case_set, _, paths = _read_inputs(case_set_path, questions_path)
    try:
        gold = StressGold.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise EvaluationInputError(f"invalid stress gold {path}: {exc}") from exc
    if gold.long_doc_key not in paths:
        raise EvaluationInputError("stress long_doc_key is not in case set")
    for item in gold.ocr_tokens:
        if item.doc_key not in paths:
            raise EvaluationInputError(f"OCR token {item.token_id}: unknown doc_key")
    if case_set.allow_cloud_model:
        raise EvaluationInputError("default stress evaluation must forbid cloud QA")
    return gold


def _rate(values: list[bool]) -> dict[str, Any]:
    numerator = sum(values)
    return {"numerator": numerator, "denominator": len(values),
            "value": round(numerator / len(values), 4) if values else None}


def _gold_hit(hit: dict[str, Any], gold: GoldEvidence) -> bool:
    return (hit["doc_key"] == gold.doc_key and hit["page"] == gold.page and
            _normalized(gold.snippet) in _normalized(hit["text"]))


def _question_diagnostic(question: Question, row: dict[str, Any],
                         pages: dict[str, list[ExtractedPage]]) -> dict[str, Any]:
    if question.expected_status == "insufficient_evidence":
        row.update(parsed_text_match=None, gold_token_preservation=None,
                   gold_ranks=None, gold_rank=None, hit_at_1=None, hit_at_3=None,
                   hit_at_5=None, all_required_hit_at_1=None,
                   all_required_hit_at_3=None, all_required_hit_at_5=None,
                   reciprocal_rank=None, distractor_count_at_5=None,
                   evidence_match=None, semantic_support_mismatch=None)
        row["first_failure_stage"] = (
            "qa" if row["actual_status"] == "answered" else
            row["failure_stage"] if row["failure_stage"] else None
        )
        return row
    page_matches: list[bool] = []
    source_methods: list[str | None] = []
    chunk_matches: list[bool] = []
    for source in question.gold_evidence:
        document = pages.get(source.doc_key, [])
        page = document[source.page - 1] if 1 <= source.page <= len(document) else None
        page_matches.append(page is not None and
                            _normalized(source.snippet) in _normalized(page.text))
        source_methods.append(page.extraction_method if page is not None else None)
        if page is None:
            chunk_matches.append(False)
            continue
        indexable = IndexablePage("stress", source.doc_key, "version", "page",
                                  source.page, page.text)
        chunk_matches.append(any(
            _normalized(source.snippet) in _normalized(chunk.text)
            for chunk in chunk_page(indexable)
        ))
    ranks = [next((hit["rank"] for hit in row["retrieval_results"]
                   if _gold_hit(hit, source)), None)
             for source in question.gold_evidence]
    first = min((rank for rank in ranks if rank is not None), default=None)
    top_five = row["retrieval_results"][:5]
    matched_hits = [hit for hit in top_five
                    if any(_gold_hit(hit, source) for source in question.gold_evidence)]
    evidence_match = all(any(
        _gold_hit(hit, source) and hit["evidence_id"] in row["qa_evidence_ids"]
        for hit in row["retrieval_results"]
    ) for source in question.gold_evidence)
    row.update(
        parsed_text_match=all(page_matches),
        gold_token_preservation=page_matches,
        source_extraction_methods=source_methods,
        chunk_text_match=all(chunk_matches),
        gold_ranks=ranks, gold_rank=first,
        hit_at_1=first is not None and first <= 1,
        hit_at_3=first is not None and first <= 3,
        hit_at_5=first is not None and first <= 5,
        all_required_hit_at_1=all(rank is not None and rank <= 1 for rank in ranks),
        all_required_hit_at_3=all(rank is not None and rank <= 3 for rank in ranks),
        all_required_hit_at_5=all(rank is not None and rank <= 5 for rank in ranks),
        reciprocal_rank=round(1 / first, 5) if first is not None and first <= 5 else 0.0,
        distractor_count_at_5=len(top_five) - len(matched_hits),
        evidence_match=evidence_match,
    )
    # Lexical citation matching is auditable; semantic entailment is not decided here.
    row["semantic_support_mismatch"] = None
    if row["actual_status"] == "answered":
        row["manual_review_required"] = True
    if not all(page_matches):
        failing_methods = [method for method, found in zip(source_methods, page_matches,
                                                            strict=True) if not found]
        first_failure = "ocr" if "ocr" in failing_methods else "parse"
    elif not all(chunk_matches):
        first_failure = "chunk"
    elif not row["all_required_hit_at_5"]:
        first_failure = "retrieval"
    elif not evidence_match:
        first_failure = "evidence"
    elif row["actual_status"] != "answered" or row["validation_status"] != "validated":
        first_failure = "qa"
    elif any(item.get("document_match") is False or item.get("page_match") is False or
             item.get("snippet_match") is False for item in row["citations"]):
        first_failure = "citation"
    else:
        first_failure = None
    row["first_failure_stage"] = first_failure
    return row


def _ocr_tokens(gold: StressGold, pages: dict[str, list[ExtractedPage]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in gold.ocr_tokens:
        document = pages.get(item.doc_key, [])
        page = document[item.page - 1] if 1 <= item.page <= len(document) else None
        results.append({
            "token_id": item.token_id, "category": item.category,
            "doc_key": item.doc_key, "page": item.page,
            "expected": item.expected,
            "preserved": page is not None and item.expected in page.text,
            "extraction_method": page.extraction_method if page else None,
        })
    return results


_STAGE_MAP = {
    "document": "parse", "parse": "parse", "chunking": "chunk",
    "retrieval": "retrieval", "evidence": "evidence",
    "validated_answer": "qa", "claim_extraction": "claim", "claim_fact": "claim",
    "claim_source_document": "claim", "claim_source_page": "claim",
    "claim_evidence_snippet": "claim", "candidate_generation": "candidate",
    "contradiction_judge": "contradiction", "contradiction_decision": "contradiction",
    "timeline": "timeline", "timeline_date": "timeline", "timeline_source": "timeline",
}
_STAGES = ("parse", "ocr", "chunk", "retrieval", "evidence", "qa", "citation",
           "claim", "candidate", "contradiction", "timeline")


def _failure_distribution(question_rows: list[dict[str, Any]],
                          downstream_rows: list[dict[str, Any]],
                          questions: list[Question], gold: DownstreamGold,
                          pages: dict[str, list[ExtractedPage]]) -> dict[str, Any]:
    question_tags = {item.id: item.tags for item in questions}
    claim_question = {item.claim_key: item.question_id for item in gold.claims}
    affected: dict[str, set[str]] = defaultdict(set)
    tags: dict[str, set[str]] = defaultdict(set)

    def add(stage: str | None, gold_id: str, item_tags: list[str]) -> None:
        if stage not in _STAGES:
            return
        affected[stage].add(gold_id)
        tags[stage].update(item_tags)

    for row in question_rows:
        add(row["first_failure_stage"], row["id"], row["tags"])
    for row in downstream_rows:
        failure = row["first_failure_stage"]
        mapped = _STAGE_MAP.get(failure)
        if failure == "parse" and row["kind"] == "claim":
            source = next((item.source for item in gold.claims
                           if item.claim_key == row["gold_id"]), None)
            if source is not None:
                page = pages[source.doc_key][source.page - 1]
                mapped = "ocr" if page.extraction_method == "ocr" else "parse"
        if row["kind"] == "claim":
            ids = [claim_question[row["gold_id"]]]
        elif row["kind"] == "conflict_pair":
            ids = [claim_question[key] for key in row["claim_keys"]]
        else:
            ids = [claim_question[row["claim_key"]]]
        item_tags = sorted({tag for identifier in ids for tag in question_tags[identifier]})
        add(mapped, row["gold_id"], item_tags)
    return {stage: {"failure_count": len(affected[stage]),
                    "affected_gold_ids": sorted(affected[stage]),
                    "affected_tags": sorted(tags[stage])}
            for stage in _STAGES}


def _claim_stress(gold: DownstreamGold,
                  rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_key = {item["gold_id"]: item for item in rows if item["kind"] == "claim"}
    actual_by_id: dict[str, dict[str, Any]] = {}
    category_checks: list[bool] = []
    for expected in gold.claims:
        row = by_key[expected.claim_key]
        for actual in row.get("actual_claims", []):
            actual_by_id[actual["claim_id"]] = actual
        values = [actual for actual in row.get("actual_claims", [])
                  if _fact_matches(ExtractedClaim(
                      actual["claim_id"], "answer", "source", actual["text"],
                      expected.category, (), "evaluation", "evaluation"
                  ), expected)]
        if values:
            category_checks.append(any(actual["kind"] == expected.category
                                       for actual in values))
    non_atomic = [item["claim_id"] for item in actual_by_id.values() if (
        bool(re.search(r"\d+(?:\.\d+)?(?:万)?元", item["text"])) and
        bool(re.search(r"\d{4}年\d{1,2}月", item["text"]))
    ) or ("付款人" in item["text"] and
          ("已支付" in item["text"] or "未支付" in item["text"]))]
    return {
        "category_accuracy_given_fact": _rate(category_checks),
        "atomicity": _rate([item["claim_id"] not in non_atomic
                            for item in actual_by_id.values()]),
        "non_atomic_claim_ids": non_atomic,
    }


def _latencies(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [float(row["latency_ms"][key]) for row in rows
              if row["latency_ms"][key] is not None]
    return {"count": len(values), "p50_ms": _percentile(values, 0.5),
            "p95_ms": _percentile(values, 0.95)}


def run_stress_evaluation(case_set_path: Path, questions_path: Path,
                          downstream_gold_path: Path, stress_gold_path: Path,
                          output_dir: Path, *,
                          embeddings: EmbeddingProvider | None = None,
                          ) -> dict[str, Any]:
    stress_gold = load_stress_gold(stress_gold_path, case_set_path, questions_path)
    downstream_gold, questions = load_downstream_gold(
        downstream_gold_path, case_set_path, questions_path
    )
    _, _, paths = _read_inputs(case_set_path, questions_path)
    downstream_summary = run_downstream_evaluation(
        case_set_path, questions_path, downstream_gold_path, output_dir,
        embeddings=embeddings,
    )
    pages = {key: extract_pdf_pages(path) for key, path in paths.items()}
    question_rows = [json.loads(line) for line in (output_dir / "results.jsonl").read_text(
        encoding="utf-8").splitlines()]
    downstream_rows = [json.loads(line) for line in (
        output_dir / "downstream_results.jsonl").read_text(encoding="utf-8").splitlines()]
    question_rows = [_question_diagnostic(question, row, pages)
                     for question, row in zip(questions, question_rows, strict=True)]
    tokens = _ocr_tokens(stress_gold, pages)
    distribution = _failure_distribution(
        question_rows, downstream_rows, questions, downstream_gold, pages
    )
    answerable = [row for row in question_rows if row["expected_status"] == "answered"]
    long_rows = [row for row in question_rows if "long_doc" in row["tags"]]
    other_rows = [row for row in question_rows if "long_doc" not in row["tags"]]
    qa_summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    wrong_decisions = [item for item in downstream_rows if item["kind"] == "conflict_pair"
                       and item["candidate_found"] is True and
                       item["contradiction_correct"] is False]
    stress = {
        "stress_gold_sha256": hashlib.sha256(stress_gold_path.read_bytes()).hexdigest(),
        "materials": len(paths), "pages": sum(len(item) for item in pages.values()),
        "questions": len(questions),
        "tag_counts": dict(sorted(Counter(tag for question in questions
                                          for tag in question.tags).items())),
        "retrieval": {
            "any_hit_at_1": _rate([row["hit_at_1"] for row in answerable]),
            "any_hit_at_3": _rate([row["hit_at_3"] for row in answerable]),
            "any_hit_at_5": _rate([row["hit_at_5"] for row in answerable]),
            "all_required_hit_at_1": _rate(
                [row["all_required_hit_at_1"] for row in answerable]),
            "all_required_hit_at_3": _rate(
                [row["all_required_hit_at_3"] for row in answerable]),
            "all_required_hit_at_5": _rate(
                [row["all_required_hit_at_5"] for row in answerable]),
            "mrr_at_5": round(sum(row["reciprocal_rank"] for row in answerable) /
                              len(answerable), 4) if answerable else None,
            "mean_distractors_at_5": round(sum(row["distractor_count_at_5"]
                                                for row in answerable) /
                                           len(answerable), 3) if answerable else None,
            "missed_gold_ids": [row["id"] for row in answerable
                                if not row["all_required_hit_at_5"]],
        },
        "ocr": {
            "tokens": tokens,
            "token_preservation": _rate([item["preserved"] for item in tokens]),
            "damaged_token_ids": [item["token_id"] for item in tokens
                                  if not item["preserved"]],
        },
        "qa": {
            "deterministic_only": True,
            "false_abstain": [row["id"] for row in answerable
                              if row["actual_status"] == "insufficient_evidence"],
            "false_abstain_with_all_gold_retrieved": [row["id"] for row in answerable
                if row["actual_status"] == "insufficient_evidence" and
                row["all_required_hit_at_5"]],
            "false_answer": [row["id"] for row in question_rows
                             if row["expected_status"] == "insufficient_evidence" and
                             row["actual_status"] == "answered"],
            "provider_failure": [row["id"] for row in question_rows
                                 if row["failure_stage"] == "qa"],
        },
        "citation": {
            "document_accuracy": qa_summary["citation_document_accuracy"],
            "page_accuracy": qa_summary["citation_page_accuracy"],
            "snippet_accuracy": qa_summary["citation_snippet_accuracy"],
            "semantic_support_mismatch": None,
            "manual_review_count": sum(row["manual_review_required"]
                                       for row in question_rows),
        },
        "claim": {**downstream_summary["claim"],
                  **_claim_stress(downstream_gold, downstream_rows)},
        "candidate": downstream_summary["candidate"],
        "contradiction": {
            **downstream_summary["contradiction"],
            "false_contradiction": [item["gold_id"] for item in wrong_decisions
                                    if item["actual_relation"] == "contradiction"],
            "false_compatible": [item["gold_id"] for item in wrong_decisions
                                 if item["actual_relation"] == "non_contradiction"],
            "false_uncertain": [item["gold_id"] for item in wrong_decisions
                                if item["actual_relation"] == "uncertain"],
        },
        "timeline": downstream_summary["timeline"],
        "stage_failures": distribution,
        "top_failure_categories": [
            {"stage": stage, **item} for stage, item in sorted(
                distribution.items(), key=lambda pair: (-pair[1]["failure_count"],
                                                        _STAGES.index(pair[0]))
            ) if item["failure_count"] > 0
        ][:5],
        "long_document": {
            "doc_key": stress_gold.long_doc_key,
            "pages": len(pages[stress_gold.long_doc_key]),
            "gold_pages": stress_gold.long_gold_pages,
            "shared_index_ms": qa_summary["latency_ms"]["index_ms"],
            "shared_chunks_total": (qa_summary["index_statistics"] or {}).get("chunks_total"),
            "long_retrieval": _latencies(long_rows, "retrieval_ms"),
            "other_retrieval": _latencies(other_rows, "retrieval_ms"),
            "long_qa": _latencies(long_rows, "qa_ms"),
            "other_qa": _latencies(other_rows, "qa_ms"),
            "gold_ranks": {row["id"]: row["gold_rank"] for row in long_rows},
            "all_required_hit_at_5": _rate(
                [row["all_required_hit_at_5"] for row in long_rows]),
        },
        "metric_notes": {
            "qa": "gold-gated FakeLLM, not real-model reliability",
            "semantic_support": "not deterministically decidable; answered rows need review",
            "candidate_precision": "gold pairs are not exhaustive; unlabelled is not false",
            "indexing": "shared index time/chunks include all 11 documents",
            "failure_counts": "gold records, including dependent downstream records",
            "retrieval_mrr": "first gold hit in top 5; misses contribute zero",
        },
    }
    qa_summary["stress"] = stress
    (output_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in question_rows),
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(qa_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    downstream_summary["stress_stage_failures"] = distribution
    (output_dir / "downstream_summary.json").write_text(
        json.dumps(downstream_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return dict(qa_summary)
