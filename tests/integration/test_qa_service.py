from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from casefile_ai.application.bootstrap import build_case_application
from casefile_ai.application.qa_contracts import (
    ModelAnswerAlignment,
    ModelAnswerClaim,
    ModelGroundedAnswer,
    ModelSupportDecision,
)
from casefile_ai.application.services.documents import DefaultIngestionService
from casefile_ai.application.services.qa import DefaultQAService
from casefile_ai.application.services.retrieval import DefaultRetrievalService
from casefile_ai.infrastructure.db.documents import SqlAlchemyDocumentRepository
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.db.session import build_session_factory
from casefile_ai.infrastructure.retrieval.repository import SqliteChunkRepository
from casefile_ai.interfaces.api.main import create_app

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/synthetic_case"
TEXT_PDF = FIXTURES / "01_询问笔录_王海.pdf"
SCAN_PDF = FIXTURES / "02_银行凭证_扫描件.pdf"


class SemanticEmbedding:
    model_version = "qa-semantic-v1"

    @staticmethod
    def _vector(value: str) -> list[float]:
        vector = np.asarray(
            [
                float(any(term in value for term in ("失败", "未入账", "没有成功到账"))),
                float("陈军" in value),
                float(any(term in value for term in ("450000", "45万元"))),
                float("2021年3月5日" in value),
                0.01,
            ],
            dtype=np.float32,
        )
        vector /= np.linalg.norm(vector)
        return vector.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(item) for item in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class AdaptiveFakeLLM:
    model_version = "adaptive-fake-v1"

    def __init__(self) -> None:
        self.primary_calls = 0
        self.support_calls = 0
        self.forced_citation: str | None = None
        self.empty_citations = False
        self.support_verdict = "supported"
        self.raise_error = False
        self.last_schema_aliases: list[str] = []
        self.last_prompt_evidence: list[dict[str, str]] = []

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[Any],
    ) -> Any:
        if self.raise_error:
            raise RuntimeError("provider secret must not reach the API")
        payload = json.loads(user_prompt)
        if response_model is ModelAnswerAlignment:
            payload = json.loads(user_prompt)
            return response_model.model_validate({
                "requested_fact": payload["question"], "requested_scope": "synthetic fixture",
                "answers_requested_fact": "matches", "field_matches": "matches",
                "source_scope_matches": "matches", "reason": "scripted fixture alignment",
            })
        if response_model is ModelSupportDecision:
            self.support_calls += 1
            return response_model.model_validate(
                {"verdict": self.support_verdict, "reason": "合成测试判定"}
            )
        assert issubclass(response_model, ModelGroundedAnswer)
        self.primary_calls += 1
        question = payload["question"]
        evidence = payload["evidence"]
        self.last_prompt_evidence = evidence
        schema_items = response_model.model_json_schema()["$defs"]["AllowedAnswerClaim"][
            "properties"
        ]["citation_ids"]["items"]
        self.last_schema_aliases = schema_items.get("enum") or [schema_items["const"]]
        if "房屋租赁" in question:
            return response_model.model_validate(
                {"status": "insufficient_evidence", "answer": "材料不足", "claims": []}
            )
        if "日期" in question:
            phrase, claim = "2021年3月5日", "交易日期为2021年3月5日。"
        elif "金额" in question:
            phrase, claim = "450000元", "交易金额为450000元。"
        elif "人物" in question:
            phrase, claim = "陈军", "陈军是付款人。"
        elif "成功到账" in question:
            phrase, claim = "失败，未入账", "2021年3月5日的交易失败，未入账。"
        else:
            phrase, claim = "王海", "王海与案件有关。"
        selected = next(
            (item["citation_id"] for item in evidence if phrase in item["quote"]),
            evidence[0]["citation_id"],
        )
        citation_ids = [self.forced_citation or selected]
        if self.empty_citations or self.forced_citation is not None:
            claim_model = ModelAnswerClaim.model_construct(
                text=claim,
                citation_ids=[] if self.empty_citations else citation_ids,
                confidence=0.9,
            )
            return response_model.model_construct(
                status="answered", answer="夹带未经引用的额外结论。", claims=[claim_model]
            )
        return response_model.model_validate(
            {
                "status": "answered",
                "answer": f"{claim}夹带未经引用的额外结论。",
                "claims": [{"text": claim, "citation_ids": citation_ids, "confidence": 0.9}],
            }
        )


def _setup(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'qa-service.db').as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    application = build_case_application(url)
    factory = build_session_factory(application.engine)
    ingestion = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), tmp_path)
    retrieval = DefaultRetrievalService(SqliteChunkRepository(factory), SemanticEmbedding())
    repository = SqlAlchemyQARepository(factory)
    llm = AdaptiveFakeLLM()
    qa = DefaultQAService(repository, retrieval, llm)
    return application, ingestion, retrieval, repository, llm, qa


def test_answer_abstention_sources_cache_and_index_invalidation(tmp_path: Path) -> None:
    application, ingestion, retrieval, repository, llm, qa = _setup(tmp_path)
    case = application.service.create_case("可溯源问答案件")
    text_document = ingestion.import_document(case.case_id, TEXT_PDF)
    ingestion.import_document(case.case_id, SCAN_PDF)
    retrieval.index_case(case.case_id)

    questions = (
        "交易日期是什么？",
        "银行凭证中的金额是多少？",
        "付款人物是谁？",
        "哪笔交易没有成功到账？",
    )
    for question in questions:
        result = qa.ask_case(case.case_id, question, 4)
        assert result.abstained is False
        assert result.claims and result.citations
        assert all(citation.filename and citation.page_number >= 1 for citation in result.citations)
        assert all(citation.document_version_id for citation in result.citations)
        assert "夹带未经引用" not in result.answer
        assert result.metadata is not None
        assert result.metadata.validation_status == "validated"
        assert llm.last_schema_aliases == ["E1", "E2", "E3", "E4"]

    unrelated = qa.ask_case(case.case_id, "房屋租赁期限和租金是多少？", 4)
    assert unrelated.abstained is True
    assert unrelated.claims == unrelated.citations == ()
    assert unrelated.metadata is not None
    assert unrelated.metadata.refusal_reason == "model_insufficient_evidence"

    before_cache = llm.primary_calls
    cached = qa.ask_case(case.case_id, questions[0], 4)
    assert cached.metadata is not None and cached.metadata.cache_hit is True
    assert llm.primary_calls == before_cache

    with SCAN_PDF.open("rb") as source:
        ingestion.upload_document(case.case_id, source, SCAN_PDF.name, text_document.document_id)
    incomplete = qa.ask_case(case.case_id, questions[0], 4)
    assert incomplete.abstained is True
    assert incomplete.metadata is not None
    assert incomplete.metadata.refusal_reason == "index_incomplete"
    assert llm.primary_calls == before_cache
    retrieval.index_case(case.case_id)
    refreshed = qa.ask_case(case.case_id, questions[0], 4)
    assert refreshed.metadata is not None and refreshed.metadata.cache_hit is False
    assert llm.primary_calls == before_cache + 1

    with TestClient(create_app(application.service, ingestion, retrieval, qa)) as client:
        response = client.post(
            f"/api/cases/{case.case_id}/ask",
            json={"question": "银行凭证中的金额是多少？", "retrieval_limit": 3},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "answered"
        assert body["claims"] and body["citations"]
        assert {
            "evidence_id",
            "document_id",
            "document_version_id",
            "filename",
            "page_id",
            "page_number",
            "quote",
        }.issubset(body["citations"][0])
        assert body["metadata"]["validation_status"] == "validated"

        llm.raise_error = True
        unavailable = client.post(
            f"/api/cases/{case.case_id}/ask",
            json={"question": "模型故障测试", "retrieval_limit": 3},
        )
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": "QA model service unavailable"}
        still_valid = repository.get_answer(body["answer_id"])
        assert still_valid is not None
        assert still_valid.answer == body["answer"]
        assert still_valid.citations
    application.engine.dispose()


def test_aliases_are_round_local_and_map_to_the_current_case(tmp_path: Path) -> None:
    application, ingestion, retrieval, repository, llm, qa = _setup(tmp_path)
    first = application.service.create_case("别名案件一")
    second = application.service.create_case("别名案件二")
    ingestion.import_document(first.case_id, TEXT_PDF)
    ingestion.import_document(second.case_id, TEXT_PDF)
    retrieval.index_case(first.case_id)
    retrieval.index_case(second.case_id)

    first_answer = qa.ask_case(first.case_id, "测试别名映射", 1)
    assert not first_answer.abstained
    assert llm.last_schema_aliases == ["E1"]
    assert [item["citation_id"] for item in llm.last_prompt_evidence] == ["E1"]
    assert all("evidence_id" not in item for item in llm.last_prompt_evidence)
    first_id = first_answer.citations[0].evidence_id
    assert first_id in repository.resolve_evidence(first.case_id, (first_id,))

    second_answer = qa.ask_case(second.case_id, "测试别名映射", 1)
    assert not second_answer.abstained
    second_id = second_answer.citations[0].evidence_id
    assert second_id != first_id
    assert second_id in repository.resolve_evidence(second.case_id, (second_id,))
    assert repository.resolve_evidence(first.case_id, (second_id,)) == {}

    llm.forced_citation = "E2"
    outside_round = qa.ask_case(first.case_id, "测试轮外别名", 1)
    assert outside_round.abstained and outside_round.citations == ()
    assert outside_round.metadata is not None
    assert outside_round.metadata.refusal_reason == "citation_not_allowed"

    llm.forced_citation = second_id
    cross_case = qa.ask_case(first.case_id, "测试跨案件真实ID", 1)
    assert cross_case.abstained and cross_case.citations == ()
    assert cross_case.metadata is not None
    assert cross_case.metadata.refusal_reason == "citation_not_allowed"
    application.engine.dispose()


def test_hard_and_semantic_citation_failures_are_not_exposed(tmp_path: Path) -> None:
    application, ingestion, retrieval, repository, llm, qa = _setup(tmp_path)
    case = application.service.create_case("引用校验案件")
    other = application.service.create_case("其他案件")
    ingestion.import_document(case.case_id, TEXT_PDF)
    ingestion.import_document(other.case_id, SCAN_PDF)
    retrieval.index_case(case.case_id)
    retrieval.index_case(other.case_id)

    all_evidence = repository.materialize_evidence(
        case.case_id, retrieval.search_case_evidence(case.case_id, "案件", 4)
    )
    other_evidence = repository.materialize_evidence(
        other.case_id, retrieval.search_case_evidence(other.case_id, "陈军", 2)
    )

    llm.forced_citation = "00000000-0000-0000-0000-000000000000"
    forged = qa.ask_case(case.case_id, "测试伪造引用", 2)
    assert forged.abstained and forged.citations == ()
    assert forged.metadata is not None
    assert forged.metadata.refusal_reason == "citation_not_allowed"

    llm.forced_citation = other_evidence[0].evidence_id
    cross_case = qa.ask_case(case.case_id, "测试跨案件引用", 2)
    assert cross_case.abstained and cross_case.citations == ()

    round_ids = {hit.evidence_id for hit in retrieval.search_case_evidence(case.case_id, "陈军", 1)}
    outside_round = next(
        item.evidence_id for item in all_evidence if item.evidence_id not in round_ids
    )
    llm.forced_citation = outside_round
    outside = qa.ask_case(case.case_id, "测试本案轮外引用", 1)
    assert outside.abstained and outside.citations == ()

    llm.forced_citation = None
    llm.empty_citations = True
    uncited = qa.ask_case(case.case_id, "测试核心结论无引用", 2)
    assert uncited.abstained and uncited.citations == ()
    assert uncited.metadata is not None
    assert uncited.metadata.refusal_reason == "citation_not_allowed"

    llm.empty_citations = False
    llm.support_verdict = "unsupported"
    unsupported = qa.ask_case(case.case_id, "测试语义不支持", 2)
    assert unsupported.abstained and unsupported.citations == ()
    assert unsupported.metadata is not None
    assert unsupported.metadata.refusal_reason == "claim_unsupported"

    llm.support_verdict = "partially_supported"
    partial = qa.ask_case(case.case_id, "测试部分支持", 2)
    assert partial.abstained and partial.citations == ()
    assert partial.metadata is not None
    assert partial.metadata.refusal_reason == "claim_partially_supported"
    application.engine.dispose()
