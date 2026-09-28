"""Isolated synthetic-only live Qwen smoke; writes no credentials or raw prompts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from casefile_ai.application.claim_contracts import ConflictCandidate, ExtractedClaim
from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.application.services.contradictions import DefaultContradictionService
from casefile_ai.config import Settings
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider
from casefile_ai.interfaces.api.main import create_app
from scripts.evaluation_fixture import ProbeRepository, RecordingLLM, SingleCandidate

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    ROOT / "tests/fixtures/synthetic_case/01_询问笔录_王海.pdf",
    ROOT / "tests/fixtures/synthetic_case/02_银行凭证_扫描件.pdf",
)
REPORT = Path(os.getenv("CASEFILE_EVAL_REPORT", str(ROOT / "docs/evaluation/live-qwen-mvp.json")))


def _candidate() -> ConflictCandidate:
    facts = ("公司于2021年3月5日收到投资款。", "公司于2021年3月5日未收到投资款。")
    claims = tuple(
        ExtractedClaim(f"live-c{i}", f"live-a{i}", f"live-s{i}", fact,
                       "action", (f"live-e{i}",), "synthetic-v1", "synthetic-v1")
        for i, fact in enumerate(facts, 1)
    )
    citations = tuple(
        AnswerCitation(f"live-e{i}", f"live-s{i}", f"live-d{i}", f"live-p{i}",
                       "合成行为材料.pdf", 1, fact)
        for i, fact in enumerate(facts, 1)
    )
    return ConflictCandidate("synthetic-probe", claims[0], claims[1],
                             (citations[0],), (citations[1],), "receipt")


def main() -> None:
    settings = Settings()
    if not all((settings.llm_api_key, settings.llm_base_url, settings.llm_model)):
        raise RuntimeError("Qwen configuration is required")
    if "qwen" not in settings.llm_model.lower():
        raise RuntimeError("configured model is not Qwen")
    qa_only = os.getenv("CASEFILE_EVAL_QA_ONLY") == "1"
    report: dict[str, Any] = {
        "dataset": "synthetic_fixture_only", "model": settings.llm_model,
        "qa_attempts": [], "claim_extraction": None, "contradiction": None,
    }
    with tempfile.TemporaryDirectory(prefix="casefile-live-eval-") as directory:
        root = Path(directory)
        database_url = f"sqlite:///{(root / 'eval.db').as_posix()}"
        os.environ["CASEFILE_DATA_DIR"] = str(root / "data")
        os.environ["CASEFILE_DATABASE_URL"] = database_url
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        config.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(config, "head")
        app = create_app()
        with TestClient(app) as client:
            case_response = client.post("/api/cases", json={"name": "Qwen 合成评估"})
            case_id = case_response.json()["case_id"]
            for fixture in FIXTURES:
                with fixture.open("rb") as stream:
                    uploaded = client.post(
                        f"/api/cases/{case_id}/documents",
                        files={"file": (fixture.name, stream, "application/pdf")},
                    )
                if uploaded.status_code != 200:
                    raise RuntimeError(f"upload failed: HTTP {uploaded.status_code}")
            indexed = client.post(f"/api/cases/{case_id}/index")
            if indexed.status_code != 200:
                raise RuntimeError(f"index failed: HTTP {indexed.status_code}")
            report["index"] = indexed.json()
            accepted: str | None = None
            for question in (
                "银行凭证中的金额是多少？",
                "银行凭证中的交易金额是多少？",
                "银行交易凭证载明的金额是多少元？",
                "请引用银行凭证原文回答交易金额。",
            ):
                started = perf_counter()
                response = client.post(f"/api/cases/{case_id}/ask", json={"question": question})
                elapsed = round((perf_counter() - started) * 1000, 2)
                item: dict[str, Any] = {"question": question, "http_status": response.status_code,
                                        "latency_ms": elapsed}
                if response.status_code == 200:
                    answer = response.json()
                    citations = answer["citations"]
                    lineage = []
                    for citation in citations:
                        page = client.get(
                            f"/api/documents/{citation['document_id']}/pages/{citation['page_number']}"
                        ).json()
                        lineage.append(page["case_id"] == case_id and
                                       citation["quote"] in page["text"])
                    item.update({
                        "status": answer["status"],
                        "schema_valid": True,
                        "validation_status": answer["metadata"]["validation_status"],
                        "refusal_reason": answer["metadata"]["refusal_reason"],
                        "claim_count": len(answer["claims"]),
                        "citation_count": len(citations),
                        "citation_lineage_valid": all(lineage) if lineage else None,
                        "support_verdicts": [claim["support_status"] for claim in answer["claims"]],
                        "generation_ms": answer["metadata"]["generation_ms"],
                        "validation_ms": answer["metadata"]["validation_ms"],
                        "answer_mentions_source_amount": "450000" in answer["answer"],
                        "outside_fact_detected": (
                            not all("450000" in claim["text"] for claim in answer["claims"])
                            if answer["claims"] else None
                        ),
                    })
                    if (answer["status"] == "answered" and item["citation_lineage_valid"] and
                        all(v == "supported" for v in item["support_verdicts"])):
                        accepted = answer["answer_id"]
                else:
                    item["failure_type"] = "http_or_model_unavailable"
                report["qa_attempts"].append(item)
                print(json.dumps({"qa_attempt": item}, ensure_ascii=False), flush=True)
                if accepted or qa_only:
                    break
            if accepted is None and not qa_only:
                # Evaluate Qwen extraction separately on a fully validated synthetic
                # answer when Qwen QA did not produce an acceptable source answer.
                sys.path.insert(0, str(ROOT / "tests/integration"))
                from test_qa_service import AdaptiveFakeLLM

                from casefile_ai.application.services.qa import DefaultQAService
                from casefile_ai.application.services.retrieval import DefaultRetrievalService
                from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
                from casefile_ai.infrastructure.db.session import build_session_factory
                from casefile_ai.infrastructure.llm.local_embeddings import (
                    LocalSentenceTransformerEmbeddingProvider,
                )
                from casefile_ai.infrastructure.retrieval.repository import SqliteChunkRepository

                factory = build_session_factory(app.state.database_engine)
                embedding = LocalSentenceTransformerEmbeddingProvider(
                    model=settings.embedding_model or "",
                    revision=settings.embedding_revision,
                    device=settings.embedding_device,
                    query_instruction=settings.embedding_query_instruction,
                )
                fallback = DefaultQAService(
                    SqlAlchemyQARepository(factory),
                    DefaultRetrievalService(SqliteChunkRepository(factory), embedding),
                    AdaptiveFakeLLM(),
                ).ask_case(case_id, "银行凭证中的金额是多少？", 5)
                if not fallback.abstained and fallback.metadata is not None:
                    accepted = fallback.answer_id
                    report["claim_source"] = "FakeLLM QA; persisted and validated citation chain"
            elif accepted is not None:
                report["claim_source"] = "Qwen QA; persisted and validated citation chain"
            if accepted:
                started = perf_counter()
                response = client.post(f"/api/cases/{case_id}/answers/{accepted}/claims")
                claim_report: dict[str, Any] = {
                    "http_status": response.status_code,
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                }
                if response.status_code == 200:
                    claims = response.json()
                    coverage = client.get(
                        f"/api/cases/{case_id}/answers/{accepted}/coverage"
                    ).json()
                    claim_report.update({
                        "schema_valid": True, "claim_count": len(claims),
                        "evidence_valid": all(c["valid_evidence"] for c in coverage["claims"]),
                        "coverage_complete": coverage["complete"],
                        "outside_answer_fact_detected": any(
                            claim["text"] not in coverage["sources"][0]["source_text"]
                            for claim in claims
                        ),
                    })
                else:
                    claim_report["failure_type"] = "claim_or_support_validation_failed"
                report["claim_extraction"] = claim_report
                print(
                    json.dumps({"claim_extraction": claim_report}, ensure_ascii=False),
                    flush=True,
                )
            else:
                report["claim_extraction"] = {"status": "not_run_no_validated_qwen_answer"}
        app.state.database_engine.dispose()

    if qa_only:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        print(json.dumps({"report": str(REPORT), "qa": report["qa_attempts"]},
                         ensure_ascii=False))
        return

    provider = OpenAICompatibleLLMProvider(
        base_url=settings.llm_base_url or "", api_key=settings.llm_api_key or "",
        model=settings.llm_model or "", structured_output_mode=settings.llm_structured_output_mode,
        max_validation_attempts=settings.llm_max_validation_attempts,
    )
    recording = RecordingLLM(provider)
    started = perf_counter()
    candidate = _candidate()
    result = DefaultContradictionService(
        SingleCandidate(candidate), ProbeRepository(), recording
    ).detect_case("synthetic-probe", "synthetic-v1", "synthetic-v1")[0]
    report["contradiction"] = {
        "schema_valid": recording.output is not None,
        "model_decision": recording.output,
        "backend_verdict": result.verdict,
        "backend_reason_code": result.basis,
        "evidence_unchanged": result.left_evidence == candidate.left_evidence and
            result.right_evidence == candidate.right_evidence,
        "latency_ms": round((perf_counter() - started) * 1000, 2),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(REPORT), "qa": report["qa_attempts"],
                      "claim": report["claim_extraction"],
                      "contradiction": report["contradiction"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
