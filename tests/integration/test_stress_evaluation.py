"""The pinned synthetic stress labels and offline diagnostic boundaries."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pymupdf
import pytest

from casefile_ai.application.claim_contracts import ExtractedClaim
from casefile_ai.evaluation.downstream import _select_gold_claim, load_downstream_gold
from casefile_ai.evaluation.runner import EvaluationInputError, _normalized, _read_inputs
from casefile_ai.evaluation.stress import (
    _failure_distribution,
    _question_diagnostic,
    load_stress_gold,
    run_stress_evaluation,
)
from casefile_ai.infrastructure.parsers.pdf import extract_pdf_pages

SET = Path(__file__).resolve().parents[2] / "evals/case_sets/mvp_stress_v1"


class ConstantEmbedding:
    model_version = "stress-ci-constant-v1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("stress_cli", SET / "run_stress.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def corpus() -> tuple[Any, Any, Any, Any, Any]:
    config = SET / "case_set.json"
    questions = SET / "questions.jsonl"
    case_set, items, paths = _read_inputs(config, questions)
    downstream, _ = load_downstream_gold(SET / "downstream_gold.json", config, questions)
    stress = load_stress_gold(SET / "stress_gold.json", config, questions)
    pages = {key: extract_pdf_pages(path) for key, path in paths.items()}
    return case_set, items, paths, pages, (downstream, stress)


def test_stress_schema_and_scenario_coverage(corpus: tuple[Any, Any, Any, Any, Any]) -> None:
    case_set, items, paths, pages, (downstream, stress) = corpus
    assert len(case_set.documents) == 11
    assert sum(len(document) for document in pages.values()) == 66
    assert len(items) == 51
    assert len({item.id for item in items}) == len(items)
    assert not case_set.allow_cloud_model
    assert all(not Path(document.path).is_absolute() for document in case_set.documents)
    assert all(path.parent == (SET / "documents").resolve() for path in paths.values())
    assert any(item.version_of == "contract_v1" for item in case_set.documents)
    assert len(downstream.claims) == 22
    assert len(downstream.conflict_pairs) == 12
    assert len(downstream.timeline_events) == 10
    assert len(stress.ocr_tokens) == 12
    assert len(pages[stress.long_doc_key]) == 50
    assert stress.long_gold_pages == [1, 13, 25, 38, 50]
    assert all(1 <= page <= len(pages[stress.long_doc_key])
               for page in stress.long_gold_pages)
    assert all(1 <= item.page <= len(pages[item.doc_key]) and
               pages[item.doc_key][item.page - 1].extraction_method == "ocr"
               for item in stress.ocr_tokens)
    tags = Counter(tag for item in items for tag in item.tags)
    for required in ("ocr", "mixed_layer", "table", "broken_line", "adjacent_row",
                     "long_doc", "multi_evidence", "version", "paraphrase", "abstain",
                     "short_query", "long_query", "distractor_query", "time_range"):
        assert tags[required] > 0
    assert {item.expected_relation for item in downstream.conflict_pairs} == {
        "contradiction", "non_contradiction", "uncertain"
    }
    assert {item.contradiction_type for item in downstream.conflict_pairs} == {
        "amount", "date", "action", "person"
    }
    assert all(item.expected_status == "insufficient_evidence"
               for item in items if "smoke_abstain" in item.tags)
    assert sum("smoke_abstain" in item.tags for item in items) == 5


def test_gold_quotes_are_pinned_to_real_pages(corpus: tuple[Any, Any, Any, Any, Any]) -> None:
    _, items, _, pages, (downstream, _) = corpus
    damaged_visual_gold = {"s21", "s22"}
    for item in items:
        for quote in item.gold_evidence:
            assert 1 <= quote.page <= len(pages[quote.doc_key]), item.id
            if item.id not in damaged_visual_gold:
                assert _normalized(quote.snippet) in _normalized(
                    pages[quote.doc_key][quote.page - 1].text
                ), item.id
    for claim in downstream.claims:
        assert _normalized(claim.source.snippet) in _normalized(
            pages[claim.source.doc_key][claim.source.page - 1].text
        ), claim.claim_key


def test_scans_really_use_ocr_and_mixed_page_switches(
    corpus: tuple[Any, Any, Any, Any, Any],
) -> None:
    _, _, paths, pages, (_, stress) = corpus
    with pymupdf.open(paths["ocr"]) as pdf:  # type: ignore[no-untyped-call]
        assert all(not page.get_text().strip() for page in pdf)
    with pymupdf.open(paths["mixed"]) as pdf:  # type: ignore[no-untyped-call]
        assert pdf[0].get_text().strip()
        assert not pdf[1].get_text().strip()
    assert [page.extraction_method for page in pages["ocr"]] == ["ocr", "ocr"]
    assert [page.extraction_method for page in pages["mixed"]] == ["native", "ocr"]
    assert len({page.text for page in pages["long"]}) == 50
    assert "O0" not in pages["ocr"][0].text
    assert "1580.50" in pages["ocr"][0].text
    assert {item.category for item in stress.ocr_tokens} >= {
        "O/0", "1/l/I", "5/S", "8/B", "blurred_chinese_name",
        "decimal_amount", "date_separator",
    }


def test_diagnostic_separates_ocr_retrieval_and_candidate(
    corpus: tuple[Any, Any, Any, Any, Any],
) -> None:
    _, items, _, pages, (gold, _) = corpus
    by_id = {item.id: item for item in items}
    base: dict[str, Any] = {
        "actual_status": "insufficient_evidence", "failure_stage": None,
        "retrieval_results": [], "qa_evidence_ids": [], "validation_status": None,
        "citations": [], "manual_review_required": False,
    }
    ocr = _question_diagnostic(by_id["s21"], base.copy(), pages)
    assert ocr["first_failure_stage"] == "ocr"
    assert ocr["parsed_text_match"] is False
    retrieval = _question_diagnostic(by_id["s01"], base.copy(), pages)
    assert retrieval["first_failure_stage"] == "retrieval"
    assert retrieval["parsed_text_match"] is True
    question_rows = [
        {"id": "s21", "tags": by_id["s21"].tags, "first_failure_stage": "ocr"},
        {"id": "s01", "tags": by_id["s01"].tags, "first_failure_stage": "retrieval"},
    ]
    pair = next(item for item in gold.conflict_pairs if item.pair_key == "amount_conflict")
    downstream_rows = [{"kind": "conflict_pair", "gold_id": pair.pair_key,
                        "claim_keys": [pair.claim_a, pair.claim_b],
                        "first_failure_stage": "candidate_generation"}]
    distribution = _failure_distribution(question_rows, downstream_rows, items, gold, pages)
    assert distribution["ocr"]["affected_gold_ids"] == ["s21"]
    assert distribution["retrieval"]["affected_gold_ids"] == ["s01"]
    assert distribution["candidate"]["affected_gold_ids"] == ["amount_conflict"]


def test_gold_link_prefers_annotated_event_over_same_value_substring(
    corpus: tuple[Any, Any, Any, Any, Any],
) -> None:
    _, _, _, _, (gold, _) = corpus
    expected = next(item for item in gold.claims if item.claim_key == "c_date_other")
    shortened = ExtractedClaim(
        "short", "answer", "source", "付款记录日期为2026年3月10日",
        "date", (), "evaluation", "fake",
    )
    complete = ExtractedClaim(
        "complete", "answer", "source", "另一笔付款记录日期为2026年3月10日",
        "date", (), "evaluation", "fake",
    )
    assert _select_gold_claim((shortened, complete), expected) == complete


def test_real_smoke_is_explicit_and_pinned(monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
    script = _script()
    selected = script.verify_real_smoke_inputs()
    assert len(selected) == 20
    called: list[str] = []
    monkeypatch.setattr(script, "run_stress_evaluation", lambda *args: (
        called.append("offline") or {"total_questions": 51, "failure_count": 0}
    ))
    monkeypatch.setattr(script, "run_real_smoke", lambda *args: (
        called.append("cloud") or {"total_questions": 20, "failure_count": 0}
    ))
    monkeypatch.setattr(sys, "argv", ["run_stress.py", "--output", str(tmp_path)])
    script.main()
    assert called == ["offline"]


def test_real_smoke_rejects_false_cloud_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script()
    original = script._read_inputs

    def deny(path: Path, questions: Path) -> Any:
        case_set, items, paths = original(path, questions)
        return case_set.model_copy(update={"allow_cloud_model": False}), items, paths

    monkeypatch.setattr(script, "_read_inputs", deny)
    with pytest.raises(EvaluationInputError):
        script.verify_real_smoke_inputs()


def test_full_stress_report_is_offline_and_stage_complete(tmp_path: Path) -> None:
    summary = run_stress_evaluation(
        SET / "case_set.json", SET / "questions.jsonl", SET / "downstream_gold.json",
        SET / "stress_gold.json", tmp_path, embeddings=ConstantEmbedding(),
    )
    assert summary["total_questions"] == 51
    assert summary["stress"]["pages"] == 66
    assert summary["stress"]["ocr"]["token_preservation"]["denominator"] == 12
    assert set(summary["stress"]["stage_failures"]) == {
        "parse", "ocr", "chunk", "retrieval", "evidence", "qa", "citation",
        "claim", "candidate", "contradiction", "timeline",
    }
    assert summary["stress"]["citation"]["semantic_support_mismatch"] is None
    assert summary["stress"]["candidate"]["precision"] is None
    assert summary["stress"]["long_document"]["pages"] == 50
    assert {path.name for path in tmp_path.iterdir()} == {
        "results.jsonl", "summary.json", "downstream_results.jsonl", "downstream_summary.json"
    }
    rows = [json.loads(line) for line in (tmp_path / "results.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert len(rows) == 51
    assert next(row for row in rows if row["id"] == "s21")["first_failure_stage"] == "ocr"
    assert next(row for row in rows if row["id"] == "s16")["hit_at_5"] is None
