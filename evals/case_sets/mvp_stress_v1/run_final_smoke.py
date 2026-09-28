"""Explicit, pinned-synthetic Qwen acceptance observations; no business rule changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from run_stress import HERE, verify_real_smoke_inputs

from casefile_ai.application.services.claim_coverage import DefaultClaimCoverageService
from casefile_ai.application.services.claims import (
    EXTRACTION_VERSION,
    DefaultClaimExtractionService,
)
from casefile_ai.application.services.conflict_candidates import DefaultConflictCandidateService
from casefile_ai.application.services.contradictions import DefaultContradictionService
from casefile_ai.application.services.qa import DefaultQAService
from casefile_ai.application.services.retrieval import DefaultRetrievalService
from casefile_ai.config import Settings
from casefile_ai.evaluation.downstream import (
    DownstreamGold,
    GoldClaimFakeLLM,
    _select_gold_claim,
)
from casefile_ai.evaluation.gold_fake import GoldEvidenceFakeLLM
from casefile_ai.evaluation.runner import (
    EvaluationContext,
    EvaluationInputError,
    Question,
    _gold_matches,
    _percentile,
    _read_inputs,
    run_evaluation,
)
from casefile_ai.infrastructure.db.claims import SqlAlchemyClaimRepository
from casefile_ai.infrastructure.db.contradictions import SqlAlchemyContradictionRepository
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider

GROUPS = {
    "regular": ("s01", "s02", "s03", "s04", "s38"),
    "table": ("s06", "s07", "s08", "s09", "s10"),
    "hard": ("s11", "s12", "s13", "s14", "s27"),
    "abstain": ("s16", "s17", "s18", "s19", "s20"),
}
CLAIM_PREFERRED = ("s01", "s02", "s03", "s06", "s08", "s09", "s10", "s13", "s38")


def select_questions() -> list[Question]:
    verify_real_smoke_inputs()
    _, questions, _ = _read_inputs(HERE / "real_model_case_set.json", HERE / "questions.jsonl")
    baseline = json.loads((HERE.parents[2] / "docs/evaluation/baselines/"
                           "mvp-stress-v1-2026-09-25.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((HERE / "questions.jsonl").read_bytes()).hexdigest()
    if digest != baseline["inputs"]["questions_sha256"]:
        raise EvaluationInputError("smoke questions differ from the committed baseline")
    by_id = {question.id: question for question in questions}
    return [by_id[identifier] for ids in GROUPS.values() for identifier in ids]


def error_info(exc: Exception) -> dict[str, Any]:
    """Never serialize exception text, request headers, credentials or response bodies."""
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    return {"type": type(exc).__name__, "http_status": status,
            "timeout": isinstance(exc, httpx.TimeoutException),
            "kind": "provider" if isinstance(exc, (httpx.RequestError, httpx.HTTPStatusError))
            else "validation_or_service"}


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ObservedTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []
        self.scope = "startup"
        self.phase = "qa"

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        started = perf_counter()
        record: dict[str, Any] = {"scope": self.scope, "phase": self.phase,
                                  "http_status": None, "error": None}
        try:
            response = self.inner.handle_request(request)
            # Include the response body wait in network latency without saving it.
            response.read()
            record["http_status"] = response.status_code
            return response
        except Exception as exc:
            record["error"] = error_info(exc)
            raise
        finally:
            record["latency_ms"] = round((perf_counter() - started) * 1000, 3)
            self.calls.append(record)

    def close(self) -> None:
        self.inner.close()


class SingleCandidate:
    def __init__(self, candidate: Any) -> None:
        self.candidate = candidate

    def generate(self, *_: Any) -> tuple[Any, ...]:
        return (self.candidate,)


class ObservedProvider:
    def __init__(self, delegate: Any, transport: ObservedTransport,
                 questions: list[Question], output: Path) -> None:
        self.delegate = delegate
        self.transport = transport
        self.by_question = {question.question: question.id for question in questions}
        self.outputs: list[dict[str, Any]] = []
        self.output = output

    @property
    def model_version(self) -> str:
        return str(self.delegate.model_version)

    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_model: type[Any]) -> Any:
        payload = json.loads(user_prompt)
        if "question" in payload and self.transport.phase == "qa":
            self.transport.scope = self.by_question.get(payload["question"], "unknown")
        record: dict[str, Any] = {"scope": self.transport.scope,
                                  "phase": self.transport.phase,
                                  "schema": response_model.__name__,
                                  "output": None, "error": None}
        if "question" in payload:
            record["context_quotes"] = [item["quote"] for item in payload["evidence"]]
        started = perf_counter()
        try:
            result = self.delegate.generate_structured(
                system_prompt=system_prompt, user_prompt=user_prompt, response_model=response_model,
            )
            record["output"] = result.model_dump()
            return result
        except Exception as exc:
            record["error"] = error_info(exc)
            raise
        finally:
            record["latency_ms"] = round((perf_counter() - started) * 1000, 3)
            self.outputs.append(record)
            with (self.output / "model_calls.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{record['phase']} {record['scope']} "
                  f"{'error' if record['error'] else 'ok'} {record['latency_ms']}ms", flush=True)


def enrich_qa(row: dict[str, Any], question: Question,
              observations: list[dict[str, Any]]) -> None:
    calls = [call for call in observations if call["phase"] == "qa" and
             call["scope"] == question.id]
    first_context = next((call["context_quotes"] for call in calls
                          if "context_quotes" in call), [])
    ranks = [next((hit["rank"] for hit in row["retrieval_results"] if _gold_matches(
        hit["doc_key"], hit["page"], hit["text"], gold)), None)
        for gold in question.gold_evidence]
    context_complete = all(any(
        _gold_matches(hit["doc_key"], hit["page"], hit["text"], gold)
        and hit["text"] in first_context for hit in row["retrieval_results"]
    ) for gold in question.gold_evidence) if question.gold_evidence else None
    false_abstain = (question.expected_status == "answered" and
                     row["validation_status"] == "model_abstained")
    false_answer = (question.expected_status == "insufficient_evidence" and
                    row["actual_status"] == "answered")
    model_false_answer = (question.expected_status == "insufficient_evidence" and any(
        call["output"] and call["output"].get("status") == "answered" for call in calls))
    categories = []
    if any(call["error"] and call["error"].get("kind") == "provider" for call in calls):
        categories.append("provider_failure")
    elif any(call["error"] for call in calls):
        categories.append("validation_failure")
    if question.gold_evidence and row["retrieval_all_required_hit"] is False:
        categories.append("retrieval_miss")
    if row["failure_stage"] == "validation" and "validation_failure" not in categories:
        categories.append("validation_failure")
    if false_abstain:
        categories.append("qa_false_abstain")
    if false_answer or model_false_answer:
        categories.append("qa_wrong_answer")
    for field, label in (("document_match", "citation_wrong_document"),
                         ("page_match", "citation_wrong_page"),
                         ("snippet_match", "manual_review_required")):
        if any(citation[field] is False for citation in row["citations"]):
            categories.append(label)
    row.update(smoke_group=next(group for group, ids in GROUPS.items() if question.id in ids),
               gold_ranks=ranks or None, gold_rank=min((rank for rank in ranks if rank is not None),
                                                      default=None),
               gold_in_qa_context=context_complete, false_abstain=false_abstain,
               false_answer=false_answer, model_false_answer=model_false_answer,
               failure_categories=categories, provider_calls=calls,
               qa_outcome=("failure" if row["actual_status"] is None or
                           row["failure_stage"] == "validation" else row["validation_status"]),
               citation_semantic_support="pending_review" if row["citations"] else "not_applicable")


def provider_summary(calls: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [call["http_status"] for call in calls]
    return {"total_model_calls": len(calls),
            "successful_http_calls": sum(status is not None and 200 <= status < 300
                                         for status in statuses),
            "provider_failures": sum(call["error"] is not None or
                                     (call["http_status"] is not None and
                                      call["http_status"] >= 400) for call in calls),
            "timeout": sum(bool(call["error"] and call["error"]["timeout"]) for call in calls),
            "http_429": statuses.count(429), "http_503": statuses.count(503),
            "http_5xx": sum(status is not None and status >= 500 for status in statuses),
            "by_phase": dict(Counter(call["phase"] for call in calls))}


def run(output: Path) -> dict[str, Any]:
    selected = select_questions()
    settings = Settings()
    if not all((settings.llm_base_url, settings.llm_api_key, settings.llm_model)):
        raise EvaluationInputError("Qwen URL, key and model must be configured")
    if "qwen" not in (settings.llm_model or "").lower():
        raise EvaluationInputError("configured model is not Qwen")
    if not settings.embedding_model:
        raise EvaluationInputError("local embedding model must be configured")
    output.mkdir(parents=True, exist_ok=True)
    if (output / "model_calls.jsonl").exists():
        raise EvaluationInputError("output already holds observations; choose a new directory")
    transport = ObservedTransport(httpx.HTTPTransport(retries=0))
    claims_rows: list[dict[str, Any]] = []
    semantic_rows: list[dict[str, Any]] = []
    warm_rows: list[dict[str, Any]] = []
    qa_rows: list[dict[str, Any]] = []
    _, all_questions, _ = _read_inputs(HERE / "case_set.json", HERE / "questions.jsonl")
    gold = DownstreamGold.model_validate_json((HERE / "downstream_gold.json").read_text(
        encoding="utf-8"))
    embeddings = LocalSentenceTransformerEmbeddingProvider(
        model=settings.embedding_model, revision=settings.embedding_revision,
        device=settings.embedding_device, query_instruction=settings.embedding_query_instruction,
    )
    with httpx.Client(timeout=60.0, headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                      transport=transport) as client:
        llm = ObservedProvider(OpenAICompatibleLLMProvider(
            base_url=settings.llm_base_url or "", api_key=settings.llm_api_key or "",
            model=settings.llm_model or "", client=client,
            structured_output_mode=settings.llm_structured_output_mode,
            max_validation_attempts=settings.llm_max_validation_attempts,
        ), transport, selected, output)

        def after_qa(context: EvaluationContext) -> None:
            qa_rows.extend(context.rows)
            for row, question in zip(context.rows, selected, strict=True):
                enrich_qa(row, question, llm.outputs)
            (output / "real_model_results.jsonl").write_text("".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in qa_rows), encoding="utf-8")
            repository = SqlAlchemyQARepository(context.factory)
            retrieval = DefaultRetrievalService(context.chunks, embeddings)
            claim_repository = SqlAlchemyClaimRepository(context.factory)
            extraction = DefaultClaimExtractionService(repository, claim_repository, llm)
            coverage = DefaultClaimCoverageService(repository, claim_repository)
            by_id = {row["id"]: row for row in context.rows}
            preferred = [by_id[identifier] for identifier in CLAIM_PREFERRED]
            eligible = [row for row in preferred + context.rows
                        if row["validation_status"] == "validated" and
                        row["actual_status"] == "answered"]
            seen: set[str] = set()
            transport.phase = "claim"
            for row in eligible:
                if row["id"] in seen or len(seen) >= 9:
                    continue
                seen.add(row["id"])
                transport.scope = row["id"]
                answer = repository.get_answer(row["answer_id"])
                record: dict[str, Any] = {"id": row["id"], "answer": row["actual_answer"],
                                          "tags": row["tags"],
                                          "expected_claims": row["gold_evidence"],
                                          "answer_claims": [asdict(item) for item in answer.claims]
                                          if answer else [], "actual_claims": [],
                                          "error": None, "review": "pending"}
                started = perf_counter()
                try:
                    actual = extraction.extract_answer_claims(row["answer_id"])
                    record["actual_claims"] = [asdict(claim) for claim in actual]
                    record["coverage"] = asdict(coverage.evaluate(
                        row["answer_id"], EXTRACTION_VERSION, llm.model_version))
                except Exception as exc:
                    record["error"] = error_info(exc)
                    # Only fixed service error messages; never arbitrary provider text.
                    if type(exc).__name__ == "ClaimExtractionError":
                        record["service_reason"] = str(exc)
                record["latency_ms"] = round((perf_counter() - started) * 1000, 3)
                claims_rows.append(record)
                write_json(output / "claim_results.json", claims_rows)

            # Fixed gold controls isolate the real semantic judge from QA/extraction failures.
            # Their citations still pass normal QA, Claim persistence and candidate validation.
            fake_qa = DefaultQAService(repository, retrieval, GoldEvidenceFakeLLM(all_questions))
            fake_extraction = DefaultClaimExtractionService(repository, claim_repository,
                                                            GoldClaimFakeLLM(gold))
            questions_by_id = {question.id: question for question in all_questions}
            semantic_pairs = [pair for pair in gold.conflict_pairs
                              if pair.contradiction_type in {"action", "person"}]
            claim_gold = {claim.claim_key: claim for claim in gold.claims}
            fixed_claims = {}
            setup_errors = {}
            for key in sorted({key for pair in semantic_pairs for key in
                               (pair.claim_a, pair.claim_b)}):
                expected = claim_gold[key]
                try:
                    answer = fake_qa.ask_case(context.case_id,
                                              questions_by_id[expected.question_id].question, 5)
                    actual = fake_extraction.extract_answer_claims(answer.answer_id)
                    fixed_claims[key] = _select_gold_claim(actual, expected)
                except Exception as exc:
                    setup_errors[key] = error_info(exc)
            candidate_service = DefaultConflictCandidateService(repository, claim_repository)
            candidates = candidate_service.generate(context.case_id, EXTRACTION_VERSION,
                                                     GoldClaimFakeLLM.model_version)
            result_repository = SqlAlchemyContradictionRepository(context.factory)
            transport.phase = "contradiction"
            for pair in semantic_pairs:
                transport.scope = pair.pair_key
                left, right = fixed_claims.get(pair.claim_a), fixed_claims.get(pair.claim_b)
                candidate = next((item for item in candidates if left and right and
                                  {item.left_claim.claim_id, item.right_claim.claim_id} ==
                                  {left.claim_id, right.claim_id}), None)
                record = {"pair_key": pair.pair_key, "type": pair.contradiction_type,
                          "expected": pair.expected_relation,
                          "expected_note": pair.note, "actual": None,
                          "fixture_mode": "gold-controlled validated claims, real Qwen judge",
                          "candidate": asdict(candidate) if candidate else None,
                          "setup_errors": setup_errors, "error": None}
                started = perf_counter()
                try:
                    if candidate is None:
                        raise EvaluationInputError("fixed gold candidate unavailable")

                    result = DefaultContradictionService(
                        SingleCandidate(candidate), result_repository, llm,
                    ).detect_case(context.case_id, EXTRACTION_VERSION,
                                  GoldClaimFakeLLM.model_version)[0]
                    record["actual"] = asdict(result)
                except Exception as exc:
                    record["error"] = error_info(exc)
                record["latency_ms"] = round((perf_counter() - started) * 1000, 3)
                semantic_rows.append(record)
                write_json(output / "contradiction_results.json", semantic_rows)

            transport.phase = "warm"
            real_qa = DefaultQAService(repository, retrieval, llm)
            for identifier in ("s01", "s06", "s16"):
                row = by_id[identifier]
                if row["actual_status"] is None:
                    continue
                transport.scope = identifier
                before = len(transport.calls)
                started = perf_counter()
                try:
                    answer = real_qa.ask_case(context.case_id, row["question"], 5)
                    warm_rows.append({"id": identifier, "run_mode": "warm",
                                      "cache_hit": answer.metadata.cache_hit if answer.metadata
                                      else None,
                                      "same_answer_id": answer.answer_id == row["answer_id"],
                                      "model_calls": len(transport.calls) - before,
                                      "latency_ms": round((perf_counter() - started) * 1000, 3)})
                except Exception as exc:
                    warm_rows.append({"id": identifier, "error": error_info(exc)})

        with tempfile.TemporaryDirectory(prefix="casefile-final-smoke-input-") as directory:
            questions = Path(directory) / "questions.jsonl"
            questions.write_text("".join(question.model_dump_json() + "\n"
                                        for question in selected), encoding="utf-8")
            summary = run_evaluation(HERE / "real_model_case_set.json", questions,
                                     output / "runner", llm=llm, embeddings=embeddings,
                                     settings=settings, on_complete=after_qa)
        parsed_url = urlsplit(settings.llm_base_url or "")
        summary.update(
            actual_run_date=datetime.now(UTC).date().isoformat(),
            groups=GROUPS, synthetic_pinned_materials=True, allow_cloud_model=True,
            database_isolation="fresh temporary database; no existing user cases",
            endpoint=urlunsplit((parsed_url.scheme, parsed_url.hostname or "",
                                 parsed_url.path, "", "")),
            structured_output_mode=settings.llm_structured_output_mode,
            max_validation_attempts=settings.llm_max_validation_attempts,
            provider=provider_summary(transport.calls),
            structured_calls={"total": len(llm.outputs),
                              "success": sum(call["error"] is None for call in llm.outputs),
                              "errors": sum(call["error"] is not None for call in llm.outputs)},
            qa={"total": len(qa_rows),
                "answered": sum(row["actual_status"] == "answered" for row in qa_rows),
                "correct_abstain": sum(row["expected_status"] == "insufficient_evidence" and
                                       row["validation_status"] == "model_abstained"
                                       for row in qa_rows),
                "false_abstain_ids": [row["id"] for row in qa_rows if row["false_abstain"]],
                "false_answer_ids": [row["id"] for row in qa_rows if row["false_answer"]],
                "model_false_answer_ids": [row["id"] for row in qa_rows
                                           if row["model_false_answer"]],
                "failure_categories": dict(Counter(category for row in qa_rows
                                                    for category in row["failure_categories"]))},
            claims={"tested_answers": len(claims_rows), "results": claims_rows},
            contradictions=semantic_rows, warm=warm_rows,
            observed_latency_ms={
                "qa_p50": _percentile([row["latency_ms"]["qa_ms"] for row in qa_rows
                                       if row["latency_ms"]["qa_ms"] is not None], 0.5),
                "qa_p95": _percentile([row["latency_ms"]["qa_ms"] for row in qa_rows
                                       if row["latency_ms"]["qa_ms"] is not None], 0.95),
                "claim_p50": _percentile([row["latency_ms"] for row in claims_rows], 0.5),
                "claim_p95": _percentile([row["latency_ms"] for row in claims_rows], 0.95)},
            review_status="pending direct evidence review; no additional model judge",
        )
        write_json(output / "http_calls.json", transport.calls)
        # Remove the configured credential even if a wrapped service accidentally included it.
        serialized = json.dumps(summary, ensure_ascii=False, indent=2)
        serialized = serialized.replace(settings.llm_api_key or "", "[REDACTED]")
        (output / "real_model_summary.json").write_text(serialized + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true", required=True)
    parser.add_argument("--output", type=Path,
                        default=Path(".eval-results/mvp-stress-v1-real-model"))
    args = parser.parse_args()
    summary = run(args.output)
    print(json.dumps({"qa": summary["qa"], "provider": summary["provider"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
