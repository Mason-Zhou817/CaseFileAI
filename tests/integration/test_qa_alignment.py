"""Real QA/DB/citation pipeline with scripted independent semantic decisions.

These test orchestration and fail-closed contracts, not a fake's semantic ability.
Actual Qwen semantics are observed separately on pinned synthetic smoke inputs.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from test_qa_service import _setup

from casefile_ai.application.qa_contracts import (
    ModelAnswerAlignment,
    ModelGroundedAnswer,
    ModelSupportDecision,
    QAUnavailableError,
)
from casefile_ai.application.services.qa import DefaultQAService

FACTS = (
    "付款日期为2026年4月2日。", "实际转账日期为2026年4月2日。",
    "合同签订日期为2026年4月2日。", "实付金额为980元。",
    "付款人为周甲。", "租赁价款为每月980元。",
)


def material(pages: int) -> BytesIO:
    # Same platform font discovery as the existing synthetic table fixture.
    pdfmetrics.registerFont(TTFont("AlignmentFont", str(Path("C:/Windows/Fonts/simhei.ttf"))))
    stream = BytesIO()
    pdf = canvas.Canvas(stream)
    for page in range(1, pages + 1):
        pdf.setFont("AlignmentFont", 12)
        lines = FACTS if pages == 1 or page in (2, 10) else (
            "合成档案封面及目录，仅用于定位页码，不记载本次付款字段。",
            "此页面由测试构造，无真实人员案件或敏感卷宗内容，所有数据均为合成。",
        )
        for line, text in enumerate(lines):
            pdf.drawString(50, 760 - line * 24, text)
        pdf.showPage()
    pdf.save()
    stream.seek(0)
    return stream


def decision(**changes: Any) -> dict[str, str]:
    return {"requested_fact": "scripted requested field", "requested_scope": "scripted scope",
            "answers_requested_fact": "matches", "field_matches": "matches",
            "source_scope_matches": "matches", "reason": "independent annotated decision",
            **changes}


class ScriptedLLM:
    model_version = "alignment-scripted-v1"

    def __init__(self, answer: str, *, document: str = "A.pdf", page: int = 2,
                 result: dict[str, str] | None = None) -> None:
        self.answer = answer
        self.document = document
        self.page = page
        self.result = result if result is not None else decision()
        self.calls: list[str] = []
        self.payloads: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.unchecked = False
        self.support = "supported"
        self.abstain = False

    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_model: type[Any]) -> Any:
        payload = json.loads(user_prompt)
        self.calls.append(response_model.__name__)
        self.payloads.append(payload)
        if response_model is ModelSupportDecision:
            assert self.answer in payload["evidence"][0]["quote"]
            return response_model.model_validate({
                "verdict": self.support, "reason": "literal fact",
            })
        if response_model is ModelAnswerAlignment:
            assert payload["answer"] == self.answer
            assert "question" in payload
            if self.error:
                raise self.error
            if self.unchecked:
                return response_model.model_construct(**self.result)
            return response_model.model_validate(self.result)
        assert issubclass(response_model, ModelGroundedAnswer)
        if self.abstain:
            return response_model.model_validate({
                "status": "insufficient_evidence", "answer": "", "claims": [],
            })
        source = next(item for item in payload["evidence"]
                      if item["document_name"] == self.document
                      and item["page_number"] == self.page)
        return response_model.model_validate({
            "status": "answered", "answer": self.answer + "uncited extra fact",
            "claims": [{"text": self.answer, "citation_ids": [source["citation_id"]],
                        "confidence": 1.0}],
        })


@pytest.fixture
def pipeline(tmp_path: Path):
    application, ingestion, retrieval, repository, _, _ = _setup(tmp_path)
    case = application.service.create_case("对齐合成案件")
    ingestion.upload_document(case.case_id, material(10), "A.pdf")
    ingestion.upload_document(case.case_id, material(1), "B.pdf")
    retrieval.index_case(case.case_id)
    yield case.case_id, repository, retrieval
    application.engine.dispose()


@pytest.mark.parametrize(("question", "fact", "dimension", "allowed"), [
    ("付款日期是什么？", FACTS[0], None, True),  # A
    ("最终到账日期是什么？", FACTS[1], "field_matches", False),  # B
    ("合同生效日期是什么？", FACTS[2], "field_matches", False),  # C
    ("应付金额是多少？", FACTS[3], "field_matches", False),  # D
    ("收款人是谁？", FACTS[4], "field_matches", False),  # E
    ("付款日期是什么？", FACTS[4], "answers_requested_fact", False),  # J
    ("每月租金是多少？", FACTS[5], None, True),  # I: synonymous field
    ("租赁价款如何支付？", FACTS[5], "answers_requested_fact", False),  # action vs amount
])
def test_requested_field_is_separate_from_supported_fact(
    pipeline, question: str, fact: str, dimension: str | None, allowed: bool,
) -> None:
    case, repository, retrieval = pipeline
    llm = ScriptedLLM(fact, result=decision(**({dimension: "mismatch"} if dimension else {})))
    answer = DefaultQAService(repository, retrieval, llm).ask_case(case, question, 20)
    assert llm.calls == ["AllowedGroundedAnswer", "ModelSupportDecision", "ModelAnswerAlignment"]
    assert answer.metadata is not None
    if allowed:
        assert answer.metadata.validation_status == "validated"
        assert answer.answer == fact
        assert answer.citations[0].filename == "A.pdf"
    else:
        assert answer.metadata.validation_status == "alignment_validation_failed"
        assert answer.abstained
        assert answer.claims == answer.citations == ()
        assert "mismatch" in (answer.metadata.refusal_reason or "")
        assert repository.get_answer(answer.answer_id) == answer


@pytest.mark.parametrize(("question", "document", "page", "allowed"), [
    ("A.pdf 的付款日期是什么？", "A.pdf", 2, True),  # F
    ("A.pdf 的付款日期是什么？", "B.pdf", 1, False),
    ("A.pdf 第2页的付款日期是什么？", "A.pdf", 2, True),  # G
    ("A.pdf 第2页的付款日期是什么？", "A.pdf", 10, False),
    ("付款日期是什么？", "B.pdf", 1, True),  # H unrestricted equivalent source
])
def test_document_page_scope_and_unrestricted_sources(
    pipeline, question: str, document: str, page: int, allowed: bool,
) -> None:
    case, repository, retrieval = pipeline
    llm = ScriptedLLM(FACTS[0], document=document, page=page,
                      result=decision(source_scope_matches="matches" if allowed else "mismatch"))
    answer = DefaultQAService(repository, retrieval, llm).ask_case(case, question, 20)
    assert answer.metadata is not None
    payload = llm.payloads[-1]
    alias = payload["claims"][0]["citation_ids"][0]
    source = next(item for item in payload["evidence"] if item["citation_id"] == alias)
    assert source["document_name"] == document and source["page_number"] == page
    if allowed:
        assert answer.metadata.validation_status == "validated"
        assert answer.citations[0].filename == document and answer.citations[0].page_number == page
        assert source["document_version_id"] == answer.citations[0].document_version_id
    else:
        assert answer.abstained and answer.claims == answer.citations == ()
        assert answer.metadata.refusal_reason == "alignment_source_scope_mismatch"


@pytest.mark.parametrize("dimension", [
    "answers_requested_fact", "field_matches", "source_scope_matches",
])
def test_every_uncertain_dimension_fails_closed(pipeline, dimension: str) -> None:
    case, repository, retrieval = pipeline
    llm = ScriptedLLM(FACTS[0], result=decision(**{dimension: "uncertain"}))
    answer = DefaultQAService(repository, retrieval, llm).ask_case(case, "付款日期是什么？", 20)
    assert answer.abstained and answer.metadata is not None
    assert answer.metadata.validation_status == "alignment_validation_failed"
    assert "uncertain" in (answer.metadata.refusal_reason or "")


@pytest.mark.parametrize("failure", ["timeout", "invalid_schema", "unchecked_model"])
def test_alignment_errors_never_persist_a_validated_answer(pipeline, failure: str) -> None:
    case, repository, retrieval = pipeline
    llm = ScriptedLLM(FACTS[0])
    if failure == "timeout":
        llm.error = TimeoutError("private provider error")
    else:
        llm.result["field_matches"] = "yes"
        llm.unchecked = failure == "unchecked_model"
    qa = DefaultQAService(repository, retrieval, llm)
    with pytest.raises(QAUnavailableError, match="answer alignment model unavailable"):
        qa.ask_case(case, "付款日期是什么？", 20)
    # A transient error must not become a cached refusal on the next request.
    llm.error = None
    llm.unchecked = False
    llm.result = decision()
    answer = qa.ask_case(case, "付款日期是什么？", 20)
    assert answer.metadata is not None and not answer.metadata.cache_hit
    assert answer.metadata.validation_status == "validated"


def test_alignment_only_runs_after_support_and_cache_version_invalidates(pipeline, monkeypatch):
    import casefile_ai.application.services.qa as module

    case, repository, retrieval = pipeline
    llm = ScriptedLLM(FACTS[0])
    qa = DefaultQAService(repository, retrieval, llm)
    first = qa.ask_case(case, "付款日期是什么？", 20)
    count = len(llm.calls)
    cached = qa.ask_case(case, "付款日期是什么？", 20)
    assert cached.metadata is not None and cached.metadata.cache_hit
    assert cached.answer_id == first.answer_id and len(llm.calls) == count
    monkeypatch.setattr(module, "ALIGNMENT_PROMPT_VERSION", "answer-alignment-next")
    changed = qa.ask_case(case, "付款日期是什么？", 20)
    assert changed.answer_id != first.answer_id and len(llm.calls) == count + 3
    llm.support = "unsupported"
    before = llm.calls.count("ModelAnswerAlignment")
    refused = qa.ask_case(case, "请提供付款日期", 20)
    assert refused.metadata is not None and refused.metadata.refusal_reason == "claim_unsupported"
    assert llm.calls.count("ModelAnswerAlignment") == before
    llm.abstain = True
    qa.ask_case(case, "出生日期是什么？", 20)
    assert llm.calls.count("ModelAnswerAlignment") == before


def test_alignment_schema_is_strict_and_has_no_defaults() -> None:
    for invalid in ({}, decision(field_matches=True), decision(extra="forged")):
        with pytest.raises(ValidationError):
            ModelAnswerAlignment.model_validate(invalid)
