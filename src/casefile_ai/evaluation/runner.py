"""Run adjudicated questions through the existing application services."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator
from sqlalchemy.orm import Session, sessionmaker

from casefile_ai.application.bootstrap import build_case_application
from casefile_ai.application.document_contracts import DocumentVersion
from casefile_ai.application.qa_contracts import StructuredLLMProvider
from casefile_ai.application.retrieval_contracts import EmbeddingProvider
from casefile_ai.application.services.documents import DefaultIngestionService
from casefile_ai.application.services.qa import DefaultQAService
from casefile_ai.application.services.retrieval import DefaultRetrievalService
from casefile_ai.config import Settings
from casefile_ai.infrastructure.db.documents import SqlAlchemyDocumentRepository
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.db.session import build_session_factory
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider
from casefile_ai.infrastructure.retrieval.repository import SqliteChunkRepository

ROOT = Path(__file__).resolve().parents[3]


class EvaluationInputError(ValueError):
    """Invalid evaluation configuration or fixture data."""


class DocumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    doc_key: str = Field(min_length=1)
    path: str = Field(min_length=1)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    version_of: str | None = None
    repeat_uploads: int = Field(default=0, ge=0, le=3)


class CaseSet(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    documents: list[DocumentSpec] = Field(min_length=1)
    allow_cloud_model: StrictBool
    retrieval_k: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def unique_documents(self) -> CaseSet:
        keys = [document.doc_key for document in self.documents]
        if len(keys) != len(set(keys)):
            raise ValueError("document doc_key values must be unique")
        seen: set[str] = set()
        for document in self.documents:
            if document.version_of is not None and document.version_of not in seen:
                raise ValueError("version_of must reference an earlier document key")
            seen.add(document.doc_key)
        return self


class GoldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    doc_key: str = Field(min_length=1)
    page: int = Field(ge=1)
    snippet: str = Field(min_length=1)

    @model_validator(mode="after")
    def snippet_has_content(self) -> GoldEvidence:
        if not any(char.isalnum() for char in self.snippet):
            raise ValueError("gold snippet must contain a letter or number")
        return self


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=1000)
    expected_status: Literal["answered", "insufficient_evidence"]
    expected_answer_any: list[str]
    gold_evidence: list[GoldEvidence]
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def expectation_is_coherent(self) -> Question:
        if any(not any(char.isalnum() for char in value)
               for value in self.expected_answer_any):
            raise ValueError("expected_answer_any values must contain a letter or number")
        if self.expected_status == "answered" and (
            not self.expected_answer_any or not self.gold_evidence
        ):
            raise ValueError("answered questions require expected answers and gold evidence")
        if self.expected_status == "insufficient_evidence" and (
            self.expected_answer_any or self.gold_evidence
        ):
            raise ValueError("insufficient_evidence questions require empty expectations")
        return self


@dataclass(frozen=True)
class EvaluationContext:
    """Live, isolated services available before the evaluation database is removed."""

    case_id: str
    case_set: CaseSet
    questions: list[Question]
    rows: list[dict[str, Any]]
    versions: dict[str, DocumentVersion]
    ingestion: DefaultIngestionService
    chunks: SqliteChunkRepository
    factory: sessionmaker[Session]


def _read_inputs(case_set_path: Path, questions_path: Path) -> tuple[CaseSet, list[Question],
                                                                    dict[str, Path]]:
    try:
        case_set = CaseSet.model_validate_json(case_set_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise EvaluationInputError(f"invalid case set {case_set_path}: {exc}") from exc
    try:
        lines = questions_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationInputError(f"cannot read questions {questions_path}: {exc}") from exc
    questions: list[Question] = []
    seen: set[str] = set()
    keys = {document.doc_key for document in case_set.documents}
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            raise EvaluationInputError(f"questions line {number}: blank JSONL record")
        try:
            item = Question.model_validate_json(line)
        except (ValidationError, ValueError) as exc:
            raise EvaluationInputError(f"questions line {number}: {exc}") from exc
        if item.id in seen:
            raise EvaluationInputError(f"questions line {number}: duplicate id {item.id!r}")
        unknown = {gold.doc_key for gold in item.gold_evidence} - keys
        if unknown:
            raise EvaluationInputError(
                f"questions line {number}: unknown gold doc_key {sorted(unknown)}"
            )
        seen.add(item.id)
        questions.append(item)
    if not questions:
        raise EvaluationInputError("questions JSONL must contain at least one record")
    paths: dict[str, Path] = {}
    digests: set[str] = set()
    for document in case_set.documents:
        raw = Path(document.path)
        path = (raw if raw.is_absolute() else case_set_path.resolve().parent / raw).resolve()
        if not path.is_file():
            raise EvaluationInputError(f"document {document.doc_key!r} is missing: {path}")
        if path.suffix.lower() != ".pdf":
            raise EvaluationInputError(f"document {document.doc_key!r} must be a PDF")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if document.expected_sha256 is not None and digest != document.expected_sha256.lower():
            raise EvaluationInputError(f"document {document.doc_key!r} SHA-256 mismatch")
        if digest in digests:
            raise EvaluationInputError(
                "case set contains duplicate PDF content under different keys"
            )
        digests.add(digest)
        paths[document.doc_key] = path
    return case_set, questions, paths


def _elapsed(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _normalized(value: str) -> str:
    canonical = unicodedata.normalize("NFKC", value).casefold()
    kept: list[str] = []
    for index, char in enumerate(canonical):
        decimal_point = (char == "." and 0 < index < len(canonical) - 1
                         and canonical[index - 1].isdigit()
                         and canonical[index + 1].isdigit())
        if decimal_point or (not char.isspace() and
                             not unicodedata.category(char).startswith("P")):
            kept.append(char)
    return "".join(kept)


def _answer_check(actual: str, expected: list[str]) -> tuple[bool | None, str, bool]:
    actual_norm = _normalized(actual)
    for allowed in expected:
        expected_norm = _normalized(allowed)
        if actual_norm == expected_norm:
            return True, "normalized_exact", False
        left_boundary = r"(?<!\d)" if expected_norm[0].isdigit() else ""
        right_boundary = r"(?!\d)" if expected_norm[-1].isdigit() else ""
        if re.search(left_boundary + re.escape(expected_norm) + right_boundary,
                     actual_norm):
            return True, "normalized_contains_allowed_fact", False
    return None, "unresolved_by_deterministic_rules", True


def _gold_matches(doc_key: str | None, page: int, text: str, gold: GoldEvidence) -> bool:
    return (doc_key == gold.doc_key and page == gold.page
            and _normalized(gold.snippet) in _normalized(text))


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).replace(chr(10), ' ')[:300]}"


def _base_row(question: Question, case_set: CaseSet, import_ms: float | None,
              index_ms: float | None) -> dict[str, Any]:
    return {
        "id": question.id, "question": question.question, "tags": question.tags,
        "expected_status": question.expected_status, "actual_status": None,
        "expected_answer_any": question.expected_answer_any, "actual_answer": None,
        "answer_match": None, "answer_match_method": "not_applicable",
        "manual_review_required": False, "abstain_correct": None,
        "gold_evidence": [gold.model_dump() for gold in question.gold_evidence],
        "answer_id": None, "qa_evidence_ids": [],
        "citations": [], "retrieval_results": [], "retrieval_hit": None,
        "retrieval_any_hit": None, "retrieval_all_required_hit": None,
        "retrieval_hit_at_k": None, "retrieval_k": case_set.retrieval_k,
        "latency_ms": {"import_ms": import_ms, "index_ms": index_ms,
                       "retrieval_ms": None, "qa_ms": None, "total_ms": None},
        "run_mode": "cold", "cache_hit": None,
        "qa_versions": None,
        "validation_status": None, "refusal_reason": None,
        "failure_stage": None, "failure_reason": None,
    }


def _run_question(question: Question, case_set: CaseSet, case_id: str,
                  doc_keys: dict[str, str], retrieval: DefaultRetrievalService,
                  qa: DefaultQAService, import_ms: float, index_ms: float) -> dict[str, Any]:
    row = _base_row(question, case_set, import_ms, index_ms)
    started = perf_counter()
    retrieval_started = perf_counter()
    try:
        hits = retrieval.search_case_evidence(case_id, question.question, case_set.retrieval_k)
    except Exception as exc:
        row.update(failure_stage="retrieval", failure_reason=_error(exc))
        row["latency_ms"]["total_ms"] = _elapsed(started)
        return row
    row["latency_ms"]["retrieval_ms"] = _elapsed(retrieval_started)
    row["retrieval_results"] = [
        {"rank": hit.rank, "evidence_id": hit.evidence_id,
         "doc_key": doc_keys.get(hit.document_id),
         "document_id": hit.document_id, "document_version_id": hit.document_version_id,
         "filename": hit.filename, "page": hit.page_number, "text": hit.quote,
         "retrieval_sources": list(hit.retrieval_sources)} for hit in hits
    ]
    if question.expected_status == "answered":
        matches = [any(_gold_matches(doc_keys.get(hit.document_id), hit.page_number,
                                     hit.quote, gold) for hit in hits)
                   for gold in question.gold_evidence]
        row["retrieval_any_hit"] = any(matches)
        row["retrieval_all_required_hit"] = all(matches)
        row["retrieval_hit"] = all(matches)
        row["retrieval_hit_at_k"] = all(matches)
    qa_started = perf_counter()
    try:
        answer = qa.ask_case(case_id, question.question, case_set.retrieval_k)
    except Exception as exc:
        row.update(failure_stage="qa", failure_reason=_error(exc))
        row["latency_ms"]["qa_ms"] = _elapsed(qa_started)
        row["latency_ms"]["total_ms"] = _elapsed(started)
        return row
    row["latency_ms"]["qa_ms"] = _elapsed(qa_started)
    row["latency_ms"]["total_ms"] = _elapsed(started)
    row["actual_status"] = "insufficient_evidence" if answer.abstained else "answered"
    row["answer_id"] = answer.answer_id
    row["actual_answer"] = answer.answer
    row["abstain_correct"] = (answer.abstained if question.expected_status ==
                              "insufficient_evidence" else not answer.abstained)
    if answer.metadata is not None:
        row["qa_evidence_ids"] = list(answer.metadata.evidence_ids)
        row["cache_hit"] = answer.metadata.cache_hit
        row["run_mode"] = "warm" if answer.metadata.cache_hit else "cold"
        row["qa_versions"] = {
            "model": answer.metadata.model_version,
            "prompt": answer.metadata.prompt_version,
            "retrieval": answer.metadata.retrieval_version,
            "embedding": answer.metadata.embedding_version,
            "chunking": answer.metadata.chunking_version,
        }
        row["validation_status"] = answer.metadata.validation_status
        row["refusal_reason"] = answer.metadata.refusal_reason
    row["citations"] = [
        {"doc_key": doc_keys.get(citation.document_id),
         "document_id": citation.document_id,
         "document_version_id": citation.document_version_id,
         "filename": citation.filename, "page": citation.page_number, "text": citation.quote,
         "evidence_id": citation.evidence_id,
         "document_match": any(doc_keys.get(citation.document_id) == gold.doc_key
                               for gold in question.gold_evidence)
         if question.gold_evidence else None,
         "page_match": any(doc_keys.get(citation.document_id) == gold.doc_key
                           and citation.page_number == gold.page
                           for gold in question.gold_evidence)
         if question.gold_evidence else None,
         "snippet_match": any(_gold_matches(doc_keys.get(citation.document_id),
                                             citation.page_number, citation.quote, gold)
                              for gold in question.gold_evidence)
         if question.gold_evidence else None}
        for citation in answer.citations
    ]
    if question.expected_status == "answered" and not answer.abstained:
        (row["answer_match"], row["answer_match_method"],
         row["manual_review_required"]) = _answer_check(
            answer.answer, question.expected_answer_any
        )
    if row["validation_status"] in {
        "hard_validation_failed", "semantic_validation_failed", "alignment_validation_failed",
    }:
        row.update(failure_stage="validation", failure_reason=row["refusal_reason"])
    elif row["actual_status"] != question.expected_status:
        row.update(failure_stage="evaluation", failure_reason="answer_status_mismatch")
    elif any(citation["document_match"] is False for citation in row["citations"]):
        row.update(failure_stage="evaluation", failure_reason="citation_document_mismatch")
    elif any(citation["page_match"] is False for citation in row["citations"]):
        row.update(failure_stage="evaluation", failure_reason="citation_page_mismatch")
    elif any(citation["snippet_match"] is False for citation in row["citations"]):
        row.update(failure_stage="evaluation", failure_reason="citation_snippet_mismatch")
    row["latency_ms"]["total_ms"] = _elapsed(started)
    return row


def _rate(rows: list[dict[str, Any]], eligible: Any, success: Any) -> dict[str, Any]:
    selected = [row for row in rows if eligible(row)]
    numerator = sum(bool(success(row)) for row in selected)
    return {"numerator": numerator, "denominator": len(selected),
            "value": round(numerator / len(selected), 4) if selected else None}


def _citation_rate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    citations = [citation for row in rows if row["expected_status"] == "answered"
                 for citation in row["citations"] if citation[key] is not None]
    numerator = sum(bool(citation[key]) for citation in citations)
    return {"numerator": numerator, "denominator": len(citations),
            "value": round(numerator / len(citations), 4) if citations else None}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _summary(case_set: CaseSet, rows: list[dict[str, Any]], timestamp: str,
             import_ms: float | None, index_ms: float | None, run_ms: float,
             provider_version: str, embedding_version: str, retrieval_version: str,
             inputs: dict[str, Any], document_checks: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [float(row["latency_ms"]["total_ms"]) for row in rows
              if row["latency_ms"]["total_ms"] is not None]
    return {
        "evaluation_set": case_set.name, "run_timestamp": timestamp,
        "provider_version": provider_version, "embedding_version": embedding_version,
        "retrieval_version": retrieval_version, "run_mode": "fresh_case", "inputs": inputs,
        "document_checks": document_checks,
        "retrieval_k": case_set.retrieval_k, "total_questions": len(rows),
        "answer_status_accuracy": _rate(rows, lambda _: True,
                                        lambda row: row["actual_status"] ==
                                        row["expected_status"]),
        "correct_abstain_rate": _rate(
            rows, lambda row: row["expected_status"] == "insufficient_evidence",
            lambda row: row["actual_status"] == "insufficient_evidence"
        ),
        "retrieval_all_required_hit_at_k": _rate(
            rows, lambda row: row["retrieval_all_required_hit"] is not None,
            lambda row: row["retrieval_all_required_hit"]
        ),
        "retrieval_any_hit_at_k": _rate(
            rows, lambda row: row["retrieval_any_hit"] is not None,
            lambda row: row["retrieval_any_hit"]
        ),
        "citation_document_accuracy": _citation_rate(rows, "document_match"),
        "citation_page_accuracy": _citation_rate(rows, "page_match"),
        "citation_snippet_accuracy": _citation_rate(rows, "snippet_match"),
        "answer_match_rate": _rate(
            rows, lambda row: row["answer_match"] is not None,
            lambda row: row["answer_match"]
        ),
        "manual_review_count": sum(bool(row["manual_review_required"]) for row in rows),
        "failure_count": sum(row["failure_stage"] is not None for row in rows),
        "latency_ms": {"import_ms": import_ms, "index_ms": index_ms,
                       "run_total_ms": run_ms, "question_total_p50": _percentile(totals, 0.5),
                       "question_total_p95": _percentile(totals, 0.95)},
        "metric_notes": {
            "retrieval_denominator": "answerable questions with completed retrieval only",
            "citation_denominator": "returned citations for answerable questions only",
            "answer_match_denominator": "deterministically decidable answered comparisons only",
            "question_latency": "retrieval through evaluation; excludes shared import and index",
            "citation_snippet": (
                "lexical gold excerpt match on the gold document and page; "
                "not semantic proof"
            ),
        },
    }


def run_evaluation(case_set_path: Path, questions_path: Path, output_dir: Path, *,
                   llm: StructuredLLMProvider | None = None,
                   embeddings: EmbeddingProvider | None = None,
                   settings: Settings | None = None,
                   on_complete: Callable[[EvaluationContext], None] | None = None,
                   ) -> dict[str, Any]:
    """Validate all inputs, then run a cold isolated case and write two local reports."""
    case_set, questions, paths = _read_inputs(case_set_path, questions_path)
    if llm is None and not case_set.allow_cloud_model:
        raise EvaluationInputError(
            "case set forbids cloud QA; supply an explicit local/test provider"
        )
    if isinstance(llm, OpenAICompatibleLLMProvider) and not case_set.allow_cloud_model:
        raise EvaluationInputError("case set forbids the OpenAI-compatible cloud provider")
    settings = settings or Settings()
    if embeddings is None and settings.embedding_model is None:
        raise EvaluationInputError("CASEFILE_EMBEDDING_MODEL is required")
    if llm is None and not all((settings.llm_base_url, settings.llm_api_key,
                                settings.llm_model)):
        raise EvaluationInputError("cloud QA requires configured model URL, key and name")
    if llm is None:
        llm = OpenAICompatibleLLMProvider(
            base_url=settings.llm_base_url or "", api_key=settings.llm_api_key or "",
            model=settings.llm_model or "",
            structured_output_mode=settings.llm_structured_output_mode,
            max_validation_attempts=settings.llm_max_validation_attempts,
        )
    if embeddings is None:
        embeddings = LocalSentenceTransformerEmbeddingProvider(
            model=settings.embedding_model or "", revision=settings.embedding_revision,
            device=settings.embedding_device,
            query_instruction=settings.embedding_query_instruction,
        )
    timestamp = datetime.now(UTC).isoformat()
    run_started = perf_counter()
    import_ms: float | None = None
    index_ms: float | None = None
    rows: list[dict[str, Any]] = []
    document_checks: list[dict[str, Any]] = []
    index_statistics: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(prefix="casefile-eval-") as directory:
        temp = Path(directory)
        database_url = f"sqlite:///{(temp / 'evaluation.db').as_posix()}"
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        config.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(config, "head")
        application = build_case_application(database_url)
        try:
            factory = build_session_factory(application.engine)
            ingestion = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), temp)
            chunks = SqliteChunkRepository(factory)
            retrieval = DefaultRetrievalService(chunks, embeddings)
            qa = DefaultQAService(SqlAlchemyQARepository(factory), retrieval, llm)
            case_id = application.service.create_case(case_set.name).case_id
            doc_keys: dict[str, str] = {}
            versions: dict[str, DocumentVersion] = {}
            page_hashes: dict[str, tuple[str, ...]] = {}
            stage: Literal["import", "index"] = "import"
            try:
                import_started = perf_counter()
                for document in case_set.documents:
                    previous = versions.get(document.version_of) if document.version_of else None
                    with paths[document.doc_key].open("rb") as stream:
                        result = ingestion.upload_document(
                            case_id, stream, paths[document.doc_key].name,
                            previous.document_id if previous else None,
                        )
                    if previous and (result.document_id != previous.document_id
                                     or result.version_number <= previous.version_number):
                        raise RuntimeError("version upload did not advance the document")
                    versions[document.doc_key] = result
                    doc_keys[result.document_id] = document.doc_key
                    page_hashes[document.doc_key] = tuple(
                        ingestion.get_page(result.document_id, page,
                                           result.document_version_id).text_hash
                        for page in range(1, result.page_count + 1)
                    )
                    for _ in range(document.repeat_uploads):
                        with paths[document.doc_key].open("rb") as stream:
                            repeated = ingestion.upload_document(
                                case_id, stream, paths[document.doc_key].name,
                                result.document_id,
                            )
                        if (not repeated.duplicate or
                            repeated.document_version_id != result.document_version_id):
                            raise RuntimeError("repeated upload did not deduplicate")
                    document_checks.append({
                        "doc_key": document.doc_key,
                        "version_of": document.version_of,
                        "document_id": result.document_id,
                        "document_version_id": result.document_version_id,
                        "version_number": result.version_number,
                        "repeat_uploads": document.repeat_uploads,
                        "duplicate_uploads_passed": True,
                        "historical_pages_preserved": None,
                    })
                for check in document_checks:
                    key = str(check["doc_key"])
                    version = versions[key]
                    current_hashes = tuple(
                        ingestion.get_page(version.document_id, page,
                                           version.document_version_id).text_hash
                        for page in range(1, version.page_count + 1)
                    )
                    check["historical_pages_preserved"] = current_hashes == page_hashes[key]
                    if not check["historical_pages_preserved"]:
                        raise RuntimeError("historical page source chain changed")
                import_ms = _elapsed(import_started)
                stage = "index"
                index_started = perf_counter()
                index_statistics = asdict(retrieval.index_case(case_id))
                index_ms = _elapsed(index_started)
            except Exception as exc:
                if stage == "import":
                    import_ms = _elapsed(import_started)
                else:
                    index_ms = _elapsed(index_started)
                rows = [_base_row(question, case_set, import_ms, index_ms)
                        for question in questions]
                for row in rows:
                    row.update(failure_stage=stage, failure_reason=_error(exc))
            else:
                for question in questions:
                    try:
                        rows.append(_run_question(question, case_set, case_id, doc_keys,
                                                  retrieval, qa, import_ms, index_ms))
                    except Exception as exc:
                        row = _base_row(question, case_set, import_ms, index_ms)
                        row.update(failure_stage="evaluation", failure_reason=_error(exc))
                        rows.append(row)
                if on_complete is not None:
                    on_complete(EvaluationContext(
                        case_id, case_set, questions, rows, versions, ingestion, chunks, factory
                    ))
        finally:
            application.engine.dispose()
    inputs = {
        "case_set_sha256": hashlib.sha256(case_set_path.read_bytes()).hexdigest(),
        "questions_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest(),
        "documents": {key: hashlib.sha256(path.read_bytes()).hexdigest()
                      for key, path in paths.items()},
    }
    summary = _summary(case_set, rows, timestamp, import_ms, index_ms,
                       _elapsed(run_started), llm.model_version,
                       embeddings.model_version, retrieval.retrieval_version, inputs,
                       document_checks)
    summary["index_statistics"] = index_statistics
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary
