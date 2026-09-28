from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "synthetic_case"


def test_text_pdf_has_extractable_chinese_text() -> None:
    document = pymupdf.open(FIXTURE_DIR / "01_询问笔录_王海.pdf")

    assert len(document) == 2
    assert "2021年6月1日" in document[0].get_text()
    assert "付款人是王海本人" in document[1].get_text()


def test_scanned_pdf_has_no_text_layer() -> None:
    document = pymupdf.open(FIXTURE_DIR / "02_银行凭证_扫描件.pdf")

    assert len(document) == 2
    assert all(not page.get_text().strip() for page in document)


def test_manifest_hashes_match_fixture_files() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["synthetic"] is True
    assert len(manifest["documents"]) == 2
    assert {item["has_text_layer"] for item in manifest["documents"]} == {True, False}
    for item in manifest["documents"]:
        content = (FIXTURE_DIR / item["filename"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == item["sha256"]


def test_evaluation_citations_resolve_to_existing_pages() -> None:
    datasets = [ROOT / "evals" / "qa_dataset.jsonl", ROOT / "evals" / "conflicts_dataset.jsonl"]
    page_counts = {
        path.name: len(pymupdf.open(path)) for path in FIXTURE_DIR.glob("*.pdf")
    }

    for dataset in datasets:
        for raw_line in dataset.read_text(encoding="utf-8").splitlines():
            record = json.loads(raw_line)
            citations = record.get("citations", [])
            citations.extend(
                value for key, value in record.items() if key in {"left", "right"}
            )
            for citation in citations:
                assert citation["filename"] in page_counts
                assert 1 <= citation["page"] <= page_counts[citation["filename"]]
