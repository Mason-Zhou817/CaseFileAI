"""The synthetic case set is annotated against actual native/OCR page text."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pymupdf
import pytest
from alembic import command
from alembic.config import Config

from casefile_ai.application.bootstrap import build_case_application
from casefile_ai.application.services.documents import DefaultIngestionService
from casefile_ai.application.services.qa import DefaultQAService
from casefile_ai.application.services.retrieval import DefaultRetrievalService
from casefile_ai.evaluation.gold_fake import GoldEvidenceFakeLLM
from casefile_ai.evaluation.runner import (
    CaseSet,
    Question,
    _normalized,
    _read_inputs,
    run_evaluation,
)
from casefile_ai.infrastructure.db.documents import SqlAlchemyDocumentRepository
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.db.session import build_session_factory
from casefile_ai.infrastructure.parsers.pdf import ExtractedPage, extract_pdf_pages
from casefile_ai.infrastructure.retrieval.repository import SqliteChunkRepository

ROOT = Path(__file__).resolve().parents[2]
SET = ROOT / "evals/case_sets/mvp_v1"
Corpus = tuple[CaseSet, list[Question], dict[str, Path], dict[str, list[ExtractedPage]]]


class ConstantEmbedding:
    model_version = "mvp-v1-ci-constant-v1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    spec, questions, paths = _read_inputs(SET / "case_set.json", SET / "questions.jsonl")
    pages = {key: extract_pdf_pages(path) for key, path in paths.items()}
    return spec, questions, paths, pages


def test_schema_ids_tags_and_relative_paths(corpus: Corpus) -> None:
    spec, questions, _, _ = corpus
    assert len(spec.documents) == 8
    assert len(questions) == 30
    assert len({question.id for question in questions}) == len(questions)
    assert all(not Path(document.path).is_absolute() for document in spec.documents)
    assert spec.allow_cloud_model is False
    assert any(item.repeat_uploads == 1 for item in spec.documents)
    assert any(item.version_of == "order_v1" for item in spec.documents)
    tags = Counter(tag for question in questions for tag in question.tags)
    for required in ("text_pdf", "ocr", "ocr_noise", "table", "long_doc", "amount",
                     "date", "person", "action", "abstain", "multi_evidence",
                     "version", "citation_page", "paraphrase"):
        assert tags[required] > 0
    assert sum(question.expected_status == "insufficient_evidence"
               for question in questions) >= 1


def test_gold_pages_and_snippets_match_parsed_sources(
    corpus: Corpus,
) -> None:
    _, questions, _, pages = corpus
    for question in questions:
        for gold in question.gold_evidence:
            assert gold.doc_key in pages, question.id
            assert 1 <= gold.page <= len(pages[gold.doc_key]), question.id
            source = pages[gold.doc_key][gold.page - 1].text
            if question.id == "q20":  # TR-07's date is split across two extracted lines.
                assert _normalized(gold.snippet) in _normalized(source), question.id
            else:
                assert gold.snippet in source, question.id
            # The page label, rather than only the file, must locate the quote.
            assert all(
                _normalized(gold.snippet) not in _normalized(page.text)
                for page in pages[gold.doc_key] if page.page_number != gold.page
            ), question.id


def test_scans_table_and_long_document_are_real_parser_scenarios(
    corpus: Corpus,
) -> None:
    _, questions, paths, pages = corpus
    for key in ("clear_scan", "low_scan"):
        with pymupdf.open(paths[key]) as pdf:  # type: ignore[no-untyped-call]
            assert len(pdf) == 1
            assert not pdf[0].get_text().strip()
        assert pages[key][0].extraction_method == "ocr"
    assert "76000元" in pages["low_scan"][0].text
    assert "周宁" in pages["low_scan"][0].text
    assert "人民币\n" in pages["table"][0].text
    assert "2021年5月\n" in pages["table"][0].text
    assert len(pages["long"]) == 12
    assert len({page.text for page in pages["long"]}) == 12
    assert {gold.page for question in questions if "long_doc" in question.tags
            for gold in question.gold_evidence} == {2, 7, 12}


def test_runner_records_version_and_duplicate_checks(
    tmp_path: Path, corpus: Corpus,
) -> None:
    _, questions, _, _ = corpus
    selected = [item for item in questions if "version" in item.tags]
    questions_path = tmp_path / "version_questions.jsonl"
    questions_path.write_text(
        "".join(json.dumps(item.model_dump(), ensure_ascii=False) + "\n"
                for item in selected), encoding="utf-8"
    )
    output = tmp_path / "results"
    summary = run_evaluation(SET / "case_set.json", questions_path, output,
                             llm=GoldEvidenceFakeLLM(selected), embeddings=ConstantEmbedding())
    checks = {item["doc_key"]: item for item in summary["document_checks"]}
    assert checks["bank"]["repeat_uploads"] == 1
    assert checks["bank"]["duplicate_uploads_passed"] is True
    assert checks["order_v1"]["historical_pages_preserved"] is True
    assert checks["order_v2"]["version_number"] == 2
    assert checks["order_v2"]["document_id"] == checks["order_v1"]["document_id"]
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text(
        encoding="utf-8").splitlines()]
    for row in rows:
        assert all(
            hit["document_version_id"] == checks["order_v2"]["document_version_id"]
            for hit in row["retrieval_results"]
            if hit["document_id"] == checks["order_v1"]["document_id"]
        )


def test_old_validated_answer_keeps_its_original_citation(
    tmp_path: Path, corpus: Corpus,
) -> None:
    _, _, paths, _ = corpus
    database_url = f"sqlite:///{(tmp_path / 'history.db').as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    application = build_case_application(database_url)
    try:
        factory = build_session_factory(application.engine)
        ingestion = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), tmp_path)
        retrieval = DefaultRetrievalService(SqliteChunkRepository(factory), ConstantEmbedding())
        answers = SqlAlchemyQARepository(factory)
        old_question = Question.model_validate({
            "id": "old", "question": "采购订单初版的应付金额是多少？",
            "expected_status": "answered", "expected_answer_any": ["人民币120000元"],
            "gold_evidence": [{"doc_key": "order_v1", "page": 1,
                               "snippet": "应付金额：人民币120000元"}], "tags": ["version"],
        })
        qa = DefaultQAService(answers, retrieval, GoldEvidenceFakeLLM([old_question]))
        case_id = application.service.create_case("合成版本引用测试").case_id
        with paths["order_v1"].open("rb") as stream:
            first = ingestion.upload_document(case_id, stream, paths["order_v1"].name)
        retrieval.index_case(case_id)
        old_answer = qa.ask_case(case_id, old_question.question)
        assert not old_answer.abstained
        assert old_answer.citations[0].document_version_id == first.document_version_id
        with paths["order_v2"].open("rb") as stream:
            second = ingestion.upload_document(case_id, stream, paths["order_v2"].name,
                                               first.document_id)
        retrieval.index_case(case_id)
        current = retrieval.search_case_evidence(case_id, "订单应付金额128000元", 5)
        assert current
        assert all(hit.document_version_id == second.document_version_id for hit in current)
        saved = answers.get_answer(old_answer.answer_id)
        assert saved is not None and saved.citations == old_answer.citations
        assert "120000元" in saved.citations[0].quote
        assert "120000元" in ingestion.get_page(
            first.document_id, 1, first.document_version_id
        ).text
    finally:
        application.engine.dispose()
