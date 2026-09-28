"""Grounded QA over a synthetic PDF table with wrapped cell text."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from test_qa_service import _setup

from casefile_ai.application.qa_contracts import (
    ModelAnswerAlignment,
    ModelGroundedAnswer,
    ModelSupportDecision,
)
from casefile_ai.application.services.qa import DefaultQAService


def _table_pdf() -> BytesIO:
    pdfmetrics.registerFont(TTFont("TableSimHei", r"C:\Windows\Fonts\simhei.ttf"))
    stream = BytesIO()
    pdf = canvas.Canvas(stream)
    pdf.setFont("TableSimHei", 12)
    lines = (
        ("合成培训材料。具体投料量以 SOP 为准。",)
        + ("培训提醒：所有饮品须遵循门店规范，具体比例不在本页给出。",) * 5
        + ("饮品", "配方结构", "生椰拿铁", "冰+厚椰乳到线+深烘浓", "缩",
           "小黄油拿铁", "黄油风味奶+牛奶+浓缩")
        + ("其他饮品使用不同配方，不能混用上述字段。",) * 3
    )
    for line_number, line in enumerate(lines):
        pdf.drawString(60, 760 - line_number * 28, line)
    pdf.save()
    stream.seek(0)
    return stream


class TableLLM:
    model_version = "table-fake-v1"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.evidence_quotes: list[str] = []

    def generate_structured(
        self, *, system_prompt: str, user_prompt: str, response_model: type[Any]
    ) -> Any:
        payload = json.loads(user_prompt)
        if response_model is ModelAnswerAlignment:
            payload = json.loads(user_prompt)
            assert payload["answer"] in {
                "生椰拿铁的配方结构为冰+厚椰乳到线+深烘浓缩。",
                "黄油风味奶+牛奶+浓缩对应小黄油拿铁。",
            }
            assert all(item["document_name"] == "synthetic-table.pdf"
                       and item["page_number"] == 1 for item in payload["evidence"])
            return response_model.model_validate({
                "requested_fact": payload["question"], "requested_scope": "synthetic fixture",
                "answers_requested_fact": "matches", "field_matches": "matches",
                "source_scope_matches": "matches", "reason": "scripted fixture alignment",
            })
        if response_model is ModelSupportDecision:
            assert "生椰拿铁" in payload["evidence"][0]["quote"] or "小黄油拿铁" in (
                payload["evidence"][0]["quote"]
            )
            return response_model.model_validate(
                {"verdict": "supported", "reason": "synthetic row"}
            )
        assert issubclass(response_model, ModelGroundedAnswer)
        self.prompts.append(system_prompt)
        self.evidence_quotes.append(payload["evidence"][0]["quote"])
        assert all(item["document_name"] == "synthetic-table.pdf"
                   and item["page_number"] == 1 for item in payload["evidence"])
        if "马斯克" in payload["question"] or "价格" in payload["question"]:
            return response_model.model_validate({
                "status": "insufficient_evidence", "answer": "", "claims": [],
            })
        if "生椰拿铁" in payload["question"]:
            assert "表格抽取文本" in system_prompt
            assert "配方结构" in system_prompt
            if "本轮只给出" not in system_prompt:
                return response_model.model_validate({
                    "status": "insufficient_evidence", "answer": "", "claims": [],
                })
            subject, fact = "生椰拿铁", "生椰拿铁的配方结构为冰+厚椰乳到线+深烘浓缩。"
        elif "不存在饮品" in payload["question"]:
            return response_model.model_validate({
                "status": "insufficient_evidence", "answer": "", "claims": [],
            })
        else:
            subject, fact = "小黄油拿铁", "黄油风味奶+牛奶+浓缩对应小黄油拿铁。"
        source = next(
            item for item in payload["evidence"] if subject in item["quote"]
        )
        return response_model.model_validate({
            "status": "answered", "answer": fact,
            "claims": [{"text": fact, "citation_ids": [source["citation_id"]],
                        "confidence": 0.9}],
        })


def test_wrapped_table_field_answers_and_outside_question_abstains(tmp_path: Path) -> None:
    application, ingestion, retrieval, repository, _, _ = _setup(tmp_path)
    case = application.service.create_case("合成表格案件")
    source = _table_pdf()
    ingestion.upload_document(case.case_id, source, "synthetic-table.pdf")
    retrieval.index_case(case.case_id)
    llm = TableLLM()
    service = DefaultQAService(repository, retrieval, llm)

    recipe = service.ask_case(case.case_id, "生椰拿铁的配方是什么？", 5)
    assert recipe.answer == "生椰拿铁的配方结构为冰+厚椰乳到线+深烘浓缩。"
    assert recipe.metadata is not None
    assert recipe.metadata.validation_status == "validated"
    assert recipe.metadata.prompt_version.startswith("grounded-qa-citation-alias-v7+")
    assert recipe.citations[0].filename == "synthetic-table.pdf"
    assert recipe.citations[0].page_number == 1
    assert "生椰拿铁\n冰+厚椰乳到线+深烘浓\n缩" in recipe.citations[0].quote
    assert recipe.claims[0].citation_ids == (recipe.citations[0].evidence_id,)
    assert len(llm.prompts) == 2  # Initial QA and focused retry; support is separate.
    assert "本轮只给出" in llm.prompts[1]
    assert llm.evidence_quotes[1] in llm.evidence_quotes[0]
    assert len(llm.evidence_quotes[1]) < len(llm.evidence_quotes[0])

    inverse = service.ask_case(case.case_id, "黄油风味奶+牛奶+浓缩对应什么饮品？", 5)
    assert not inverse.abstained
    assert inverse.metadata is not None and inverse.metadata.validation_status == "validated"
    assert inverse.citations[0].page_number == 1

    outside = service.ask_case(case.case_id, "马斯克出生在哪一年？", 5)
    assert outside.abstained
    assert outside.metadata is not None
    assert outside.metadata.validation_status == "model_abstained"
    assert outside.citations == ()
    before = len(llm.prompts)
    missing = service.ask_case(case.case_id, "不存在饮品的配方是什么？", 5)
    assert missing.abstained
    assert len(llm.prompts) == before + 1
    unknown_field = service.ask_case(case.case_id, "生椰拿铁的价格是多少？", 5)
    assert unknown_field.abstained
    assert unknown_field.metadata is not None
    assert unknown_field.metadata.validation_status == "model_abstained"
    assert len(llm.prompts) == before + 3  # The focused retry also abstains.
    application.engine.dispose()
