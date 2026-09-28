"""Fixed synthetic Claim stage regression; real QA/contradiction are never called."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
from run_alignment_smoke import ContextProvider, UsageTransport
from run_final_smoke import HERE, error_info, provider_summary, select_questions, write_json

from casefile_ai.application.qa_contracts import (
    ModelAnswerAlignment,
    ModelGroundedAnswer,
    ModelSupportDecision,
)
from casefile_ai.application.services.claims import (
    EXTRACTION_VERSION,
    DefaultClaimExtractionService,
)
from casefile_ai.application.services.conflict_candidates import DefaultConflictCandidateService
from casefile_ai.config import Settings
from casefile_ai.evaluation.claim_quality import (
    ClaimQualityGold,
    assess_claim,
    fact_coverage,
    quality_summary,
)
from casefile_ai.evaluation.runner import (
    EvaluationContext,
    EvaluationInputError,
    _normalized,
    _read_inputs,
    run_evaluation,
)
from casefile_ai.infrastructure.db.claims import SqlAlchemyClaimRepository
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider

FIXTURE = HERE / "claim_quality_set.json"
SUBSET = (
    "s06",
    "s09",
    "s10",
    "s38",
    "s01",
    "s02",
    "s03",
    "s08",
    "s13",
    "s04",
    "s05",
    "s45",
    "s37",
    "s40",
)


def load_samples() -> list[dict[str, Any]]:
    select_questions()  # Existing pinned PDF/path/cloud boundary + question hash checks.
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    samples = fixture["samples"]
    if tuple(sample["id"] for sample in samples) != SUBSET:
        raise EvaluationInputError("Claim sample selection differs from the fixed subset")
    _, questions, _ = _read_inputs(HERE / "case_set.json", HERE / "questions.jsonl")
    by_id = {question.id: question for question in questions}
    for sample in samples:
        if sample["question"] != by_id[sample["id"]].question:
            raise EvaluationInputError("Claim fixture question differs from pinned question")
        for gold in sample["gold"]:
            ClaimQualityGold.model_validate(gold)
    return samples


class ReplayQAProvider:
    """Reviewed fixtures through the real QA whitelist/lineage/validation service.

    This is a deterministic setup boundary, never labelled real QA performance.
    Claim extraction and validation use Qwen only in explicit real-model mode.
    """

    model_version = "claim-quality-reviewed-qa-fixtures-v1"

    def __init__(self, samples: list[dict[str, Any]], names: dict[str, str]) -> None:
        self.samples = {sample["question"]: sample for sample in samples}
        self.names = names

    def generate_structured(
        self, *, system_prompt: str, user_prompt: str, response_model: type[Any]
    ) -> Any:
        payload = json.loads(user_prompt)
        if response_model is ModelSupportDecision:
            return response_model.model_validate(
                {
                    "verdict": "supported",
                    "reason": "reviewed synthetic answer fixture",
                }
            )
        if response_model is ModelAnswerAlignment:
            sample = self.samples[payload["question"]]
            expected = "\n".join(source["text"] for source in sample["source_claims"])
            if payload["answer"] != expected:
                raise EvaluationInputError("QA setup changed reviewed fixture text")
            return response_model.model_validate(
                {
                    "requested_fact": payload["question"],
                    "requested_scope": "reviewed synthetic scope",
                    "answers_requested_fact": "matches",
                    "field_matches": "matches",
                    "source_scope_matches": "matches",
                    "reason": "reviewed fixture scope",
                }
            )
        if not issubclass(response_model, ModelGroundedAnswer):
            raise TypeError("unexpected QA setup schema")
        sample = self.samples[payload["question"]]
        claims = []
        for source in sample["source_claims"]:
            aliases = []
            for reference in source["evidence"]:
                found = next(
                    (
                        item
                        for item in payload["evidence"]
                        if item["document_name"] == self.names[reference["doc_key"]]
                        and item["page_number"] == reference["page"]
                        and _normalized(reference["snippet"]) in _normalized(item["quote"])
                    ),
                    None,
                )
                if found is None:
                    raise EvaluationInputError(
                        "reviewed fixture evidence not offered in QA context"
                    )
                if found["citation_id"] not in aliases:
                    aliases.append(found["citation_id"])
            claims.append({"text": source["text"], "citation_ids": aliases, "confidence": 1.0})
        return response_model.model_validate(
            {
                "status": "answered",
                "answer": "\n".join(claim["text"] for claim in claims),
                "claims": claims,
            }
        )


def run(output: Path, *, real_model: bool) -> dict[str, Any]:
    samples = load_samples()
    spec, questions, _ = _read_inputs(HERE / "case_set.json", HERE / "questions.jsonl")
    selected = [next(question for question in questions if question.id == key) for key in SUBSET]
    settings = Settings()
    if real_model and (
        not all((settings.llm_base_url, settings.llm_api_key, settings.llm_model))
        or "qwen" not in (settings.llm_model or "").lower()
    ):
        raise EvaluationInputError("Qwen endpoint/key/model must be configured")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise EvaluationInputError(
            "use a new empty output directory to preserve first observations"
        )
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    qa_provider = ReplayQAProvider(
        samples, {doc.doc_key: Path(doc.path).name for doc in spec.documents}
    )
    transport = UsageTransport(httpx.HTTPTransport(retries=0))
    records: list[dict[str, Any]] = []
    candidate_stats: dict[str, Any] = {}
    with httpx.Client(
        timeout=60.0,
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        transport=transport,
    ) as client:
        llm = ContextProvider(
            OpenAICompatibleLLMProvider(
                base_url=settings.llm_base_url or "",
                api_key=settings.llm_api_key or "",
                model=settings.llm_model or "",
                client=client,
                structured_output_mode=settings.llm_structured_output_mode,
                max_validation_attempts=settings.llm_max_validation_attempts,
            ),
            transport,
            selected,
            output,
        )

        def after_qa(context: EvaluationContext) -> None:
            failed = [row["id"] for row in context.rows if row["validation_status"] != "validated"]
            if failed:
                raise EvaluationInputError(f"reviewed QA fixture setup failed: {','.join(failed)}")
            if not real_model:
                return
            qa_repo = SqlAlchemyQARepository(context.factory)
            claim_repo = SqlAlchemyClaimRepository(context.factory)
            extractor = DefaultClaimExtractionService(qa_repo, claim_repo, llm)
            version_keys = {
                version.document_version_id: key for key, version in context.versions.items()
            }
            transport.phase = "claim"
            for sample, row in zip(samples, context.rows, strict=True):
                transport.scope = sample["id"]
                answer = qa_repo.get_answer(row["answer_id"])
                assert answer is not None
                record: dict[str, Any] = {
                    "id": sample["id"],
                    "source_mode": sample["source_mode"],
                    "validated_answer": answer.answer,
                    "answer_claims": [asdict(source) for source in answer.claims],
                    "citations": [asdict(citation) for citation in answer.citations],
                    "expected_core_claim_count": sample["expected_core_claim_count"],
                    "actual_claims": [],
                    "quality": [],
                    "error": None,
                }
                started = perf_counter()
                before = len(transport.calls)
                try:
                    actual = extractor.extract_answer_claims(answer.answer_id)
                    record["actual_claims"] = [asdict(claim) for claim in actual]
                    annotations = [ClaimQualityGold.model_validate(gold) for gold in sample["gold"]]
                    record["quality"] = [
                        assess_claim(claim, list(answer.citations), annotations, version_keys)
                        for claim in actual
                    ]
                except Exception as exc:
                    record["error"] = error_info(exc)
                    if type(exc).__name__ == "ClaimExtractionError":
                        record["service_reason"] = str(exc)
                record["latency_ms"] = round((perf_counter() - started) * 1000, 3)
                record["http_calls"] = len(transport.calls) - before
                records.append(record)
                (output / "results.jsonl").write_text(
                    "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
                    encoding="utf-8",
                )
            candidates = DefaultConflictCandidateService(qa_repo, claim_repo).generate(
                context.case_id,
                EXTRACTION_VERSION,
                llm.model_version,
            )
            candidate_stats.update(
                total=len(candidates),
                by_kind={
                    kind: sum(candidate.left_claim.kind == kind for candidate in candidates)
                    for kind in ("amount", "date", "person", "action")
                },
                judge_invoked=False,
            )
            write_json(output / "http_calls.json", transport.calls)

        with tempfile.TemporaryDirectory(prefix="casefile-claim-quality-") as directory:
            path = Path(directory) / "questions.jsonl"
            path.write_text(
                "".join(question.model_dump_json() + "\n" for question in selected),
                encoding="utf-8",
            )
            setup = run_evaluation(
                HERE / "case_set.json",
                path,
                output / "qa_setup",
                llm=qa_provider,
                settings=settings,
                on_complete=after_qa,
            )
        report = {
            "evaluation_set": "MVP stress Claim quality v1",
            "subset": SUBSET,
            "run_timestamp": setup["run_timestamp"],
            "real_claim_model": real_model,
            "claim_model": settings.llm_model if real_model else None,
            "extraction_version": EXTRACTION_VERSION,
            "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "isolation": (
                "new temporary case/database, pinned synthetic PDFs; deterministic QA setup"
            ),
            "qa_setup": setup,
            "results": records,
            "expected_core_claim_count": sum(
                sample["expected_core_claim_count"] for sample in samples
            ),
            "quality": quality_summary([quality for row in records for quality in row["quality"]]),
            "fact_coverage": fact_coverage(
                [ClaimQualityGold.model_validate(gold) for sample in samples
                 for gold in sample["gold"]],
                [quality for row in records for quality in row["quality"]],
            ) if real_model else None,
            "failure_count": sum(row["error"] is not None for row in records),
            "provider": provider_summary(transport.calls),
            "candidates": candidate_stats,
            "real_qa_or_contradiction_calls": 0,
            "review_status": "pending direct inspection; model's quality flags are not gold",
        }
        write_json(output / "summary.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--real-model", action="store_true")
    mode.add_argument("--check-setup", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    summary = run(args.output, real_model=args.real_model)
    print(
        json.dumps(
            {
                "quality": summary["quality"],
                "failures": summary["failure_count"],
                "provider": summary["provider"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
