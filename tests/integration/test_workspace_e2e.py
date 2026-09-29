from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from sqlalchemy import update
from test_qa_service import _setup

from casefile_ai.application.claim_contracts import ModelClaimExtraction, ModelClaimValidation
from casefile_ai.application.qa_contracts import (
    ModelAnswerAlignment,
    ModelGroundedAnswer,
    ModelSupportDecision,
)
from casefile_ai.application.services.claim_coverage import DefaultClaimCoverageService
from casefile_ai.application.services.claims import DefaultClaimExtractionService
from casefile_ai.application.services.conflict_candidates import DefaultConflictCandidateService
from casefile_ai.application.services.contradictions import DefaultContradictionService
from casefile_ai.application.services.qa import DefaultQAService
from casefile_ai.application.services.timeline import DefaultTimelineService
from casefile_ai.application.services.workspace import CaseWorkspaceService
from casefile_ai.infrastructure.db.claims import SqlAlchemyClaimRepository
from casefile_ai.infrastructure.db.contradictions import SqlAlchemyContradictionRepository
from casefile_ai.infrastructure.db.models import ContradictionResultModel
from casefile_ai.infrastructure.db.session import build_session_factory, session_scope
from casefile_ai.interfaces.api.main import create_app


class EndToEndLLM:
    model_version = "e2e-fake-v1"

    def generate_structured(
        self, *, system_prompt: str, user_prompt: str, response_model: type[Any]
    ) -> Any:
        payload = json.loads(user_prompt)
        if response_model is ModelClaimValidation:
            return response_model.model_validate({
                "verdict": "supported", "reason": "synthetic claim quality",
                "atomicity": "matches", "scope_preservation": "matches",
                "category_matches": "matches",
            })
        if response_model is ModelAnswerAlignment:
            payload = json.loads(user_prompt)
            return response_model.model_validate({
                "requested_fact": payload["question"], "requested_scope": "synthetic fixture",
                "answers_requested_fact": "matches", "field_matches": "matches",
                "source_scope_matches": "matches", "reason": "scripted fixture alignment",
            })
        if response_model is ModelSupportDecision:
            return response_model.model_validate(
                {"verdict": "supported", "reason": "synthetic text"}
            )
        if issubclass(response_model, ModelGroundedAnswer):
            amount = "450000" if "第一" in payload["question"] else "500000"
            chosen = next(x for x in payload["evidence"] if amount in x["quote"])
            fact = f"交易金额为{amount}元。"
            return response_model.model_validate({
                "status": "answered", "answer": fact,
                "claims": [{"text": fact, "citation_ids": [chosen["citation_id"]],
                            "confidence": 0.9}],
            })
        if issubclass(response_model, ModelClaimExtraction):
            return response_model.model_validate({
                "claims": [{"text": payload["answer_claim"], "kind": "amount",
                            "citation_ids": ["E1"]}],
            })
        raise AssertionError("unexpected model schema")


class DateScopedAmountLLM(EndToEndLLM):
    """Reproduce a real split that drops the requested date from an amount fact."""

    def generate_structured(
        self, *, system_prompt: str, user_prompt: str, response_model: type[Any]
    ) -> Any:
        payload = json.loads(user_prompt)
        full = "2026年3月1日的付款金额为人民币450000元。"
        if response_model is ModelClaimValidation:
            complete = payload["claim"] == full
            return response_model.model_validate({
                "verdict": "supported", "reason": "synthetic evidence",
                "atomicity": "matches",
                "scope_preservation": "matches" if complete else "mismatch",
                "category_matches": "matches",
            })
        if issubclass(response_model, ModelGroundedAnswer):
            chosen = next(item for item in payload["evidence"] if "450000" in item["quote"])
            return response_model.model_validate({
                "status": "answered", "answer": full,
                "claims": [{"text": full, "citation_ids": [chosen["citation_id"]],
                            "confidence": 0.9}],
            })
        if issubclass(response_model, ModelClaimExtraction):
            return response_model.model_validate({"claims": [
                {"text": "2026年3月1日的付款", "kind": "date", "citation_ids": ["E1"]},
                {"text": "付款金额为人民币450000元", "kind": "amount",
                 "citation_ids": ["E1"]},
            ]})
        return super().generate_structured(
            system_prompt=system_prompt, user_prompt=user_prompt, response_model=response_model
        )


def _pdf(text: str) -> bytes:
    font = Path(r"C:\Windows\Fonts\simhei.ttf")
    pdfmetrics.registerFont(TTFont("E2ESimHei", str(font)))
    stream = BytesIO()
    document = canvas.Canvas(stream)
    document.setFont("E2ESimHei", 14)
    document.drawString(60, 750, text)
    document.save()
    return stream.getvalue()


def test_gui_api_full_pipeline_provenance_and_case_isolation(tmp_path: Path) -> None:
    application, ingestion, retrieval, answers, _, _ = _setup(tmp_path)
    factory = build_session_factory(application.engine)
    llm = EndToEndLLM()
    qa = DefaultQAService(answers, retrieval, llm)
    claims = SqlAlchemyClaimRepository(factory)
    results = SqlAlchemyContradictionRepository(factory)
    candidates = DefaultConflictCandidateService(answers, claims)
    workspace = CaseWorkspaceService(
        application.service, answers, claims, results,
        DefaultClaimExtractionService(answers, claims, llm),
        DefaultClaimCoverageService(answers, claims), candidates,
        DefaultContradictionService(candidates, results, llm),
        DefaultTimelineService(answers, claims, results), llm.model_version,
    )
    with TestClient(create_app(application.service, ingestion, retrieval, qa, workspace)) as client:
        assert client.get("/").status_code == 200
        case_a = client.post("/api/cases", json={"name": "合成案件 A"}).json()["case_id"]
        case_b = client.post("/api/cases", json={"name": "合成案件 B"}).json()["case_id"]
        for amount in ("450000", "500000"):
            response = client.post(
                f"/api/cases/{case_a}/documents",
                files={"file": (f"synthetic-{amount}.pdf", _pdf(f"交易金额为{amount}元。"),
                                "application/pdf")},
            )
            assert response.status_code == 200, response.text
        assert len(client.get(f"/api/cases/{case_a}/documents").json()) == 2
        assert client.get(f"/api/cases/{case_b}/documents").json() == []
        assert client.post(f"/api/cases/{case_a}/index").status_code == 200
        assert client.get(f"/api/cases/{case_a}/search", params={"q": "交易金额"}).json()

        answer_ids = []
        for prompt in ("第一笔交易金额？", "第二笔交易金额？"):
            response = client.post(f"/api/cases/{case_a}/ask", json={"question": prompt})
            assert response.status_code == 200, response.text
            answer = response.json()
            assert answer["status"] == "answered"
            assert answer["metadata"]["validation_status"] == "validated"
            assert answer["citations"]
            for citation in answer["citations"]:
                page = client.get(
                    f"/api/documents/{citation['document_id']}/pages/{citation['page_number']}"
                ).json()
                assert page["case_id"] == case_a
                assert citation["quote"] in page["text"]
            answer_ids.append(answer["answer_id"])
            extracted = client.post(
                f"/api/cases/{case_a}/answers/{answer['answer_id']}/claims"
            )
            assert extracted.status_code == 200, extracted.text
            assert len(extracted.json()) == 1
            coverage = client.get(
                f"/api/cases/{case_a}/answers/{answer['answer_id']}/coverage"
            ).json()
            assert coverage["complete"] and coverage["claims"][0]["valid_evidence"]

        assert len(client.get(f"/api/cases/{case_a}/answers").json()) == 2
        assert len(client.get(f"/api/cases/{case_a}/claims").json()) == 2
        candidates_json = client.get(f"/api/cases/{case_a}/conflict-candidates").json()
        assert len(candidates_json) == 1
        detected = client.post(f"/api/cases/{case_a}/contradictions")
        assert detected.status_code == 200, detected.text
        verdict = detected.json()[0]
        assert verdict["verdict"] == "contradiction"
        assert verdict["left_evidence"] and verdict["right_evidence"]
        assert client.get(f"/api/cases/{case_a}/contradictions").json() == detected.json()
        timeline = client.get(f"/api/cases/{case_a}/timeline").json()
        assert len(timeline) == 2
        assert all(item["date_status"] == "unknown" and item["evidence"] for item in timeline)

        # Historical detector decisions remain persisted but are excluded from current views.
        with session_scope(factory) as session:
            session.execute(
                update(ContradictionResultModel)
                .where(ContradictionResultModel.result_id == verdict["result_id"])
                .values(detector_version="candidate-contradiction-v1")
            )
        assert len(results.list_case_results(case_a)) == 1
        assert client.get(f"/api/cases/{case_a}/contradictions").json() == []
        assert all(
            not item["contradiction_result_ids"]
            for item in client.get(f"/api/cases/{case_a}/timeline").json()
        )

        for path in ("answers", "claims", "conflict-candidates", "contradictions", "timeline"):
            assert client.get(f"/api/cases/{case_b}/{path}").json() == []
        assert client.post(
            f"/api/cases/{case_b}/answers/{answer_ids[0]}/claims"
        ).status_code == 404
    application.engine.dispose()


def test_gui_claim_post_preserves_date_scoped_amount_and_case_isolation(tmp_path: Path) -> None:
    application, ingestion, retrieval, answers, _, _ = _setup(tmp_path)
    factory = build_session_factory(application.engine)
    llm = DateScopedAmountLLM()
    claims = SqlAlchemyClaimRepository(factory)
    results = SqlAlchemyContradictionRepository(factory)
    candidates = DefaultConflictCandidateService(answers, claims)
    workspace = CaseWorkspaceService(
        application.service, answers, claims, results,
        DefaultClaimExtractionService(answers, claims, llm),
        DefaultClaimCoverageService(answers, claims), candidates,
        DefaultContradictionService(candidates, results, llm),
        DefaultTimelineService(answers, claims, results), llm.model_version,
    )
    qa = DefaultQAService(answers, retrieval, llm)
    with TestClient(create_app(application.service, ingestion, retrieval, qa, workspace)) as client:
        case = client.post("/api/cases", json={"name": "日期限定金额合成案件"}).json()["case_id"]
        other = client.post("/api/cases", json={"name": "其他案件"}).json()["case_id"]
        upload = client.post(
            f"/api/cases/{case}/documents",
            files={"file": ("synthetic.pdf", _pdf("2026年3月1日的付款金额为人民币450000元。"),
                            "application/pdf")},
        )
        assert upload.status_code == 200, upload.text
        assert client.post(f"/api/cases/{case}/index").status_code == 200
        answer = client.post(
            f"/api/cases/{case}/ask", json={"question": "2026年3月1日的付款金额是多少？"}
        ).json()
        assert answer["status"] == "answered"
        assert answer["metadata"]["validation_status"] == "validated"
        route = f"/api/cases/{case}/answers/{answer['answer_id']}"
        assert not client.get(route + "/coverage").json()["complete"]
        cross_case = f"/api/cases/{other}/answers/{answer['answer_id']}"
        assert client.post(cross_case + "/claims").status_code == 404

        # Exactly the GUI request: POST with case_id and answer_id in the path, no JSON body.
        extracted = client.post(route + "/claims")
        assert extracted.status_code == 200, extracted.text
        amount = next(item for item in extracted.json() if item["kind"] == "amount")
        assert amount["text"] == "2026年3月1日的付款金额为人民币450000元。"
        assert amount["answer_id"] == answer["answer_id"]
        assert set(amount["evidence_ids"]) <= {
            item["evidence_id"] for item in answer["citations"]
        }
        assert amount in client.get(f"/api/cases/{case}/claims").json()
        assert client.get(f"/api/cases/{other}/claims").json() == []
        coverage = client.get(route + "/coverage").json()
        assert coverage["complete"]
        assert coverage["claims"] and all(item["valid_evidence"] for item in coverage["claims"])
        assert client.get(cross_case + "/coverage").status_code == 404
    application.engine.dispose()
