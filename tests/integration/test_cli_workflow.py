"""CLI contract and synthetic end-to-end checks without paid model calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from typer.testing import CliRunner

from casefile_ai.application.claim_contracts import ModelClaimExtraction, ModelClaimValidation
from casefile_ai.application.qa_contracts import (
    ModelAnswerAlignment,
    ModelGroundedAnswer,
    ModelSupportDecision,
)
from casefile_ai.infrastructure.llm.errors import ProviderError
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider
from casefile_ai.interfaces.cli.main import app

runner = CliRunner()


class Encoder:
    def encode(self, sentences: list[str], **_options: Any) -> Any:
        return np.asarray([[1.0, float("450000" in item), 0.1] for item in sentences],
                          dtype=np.float32)


class FakeLLM(OpenAICompatibleLLMProvider):
    model_version = "cli-fake-v1"
    failure: str | None = None
    abstain = False

    def __init__(self, **_values: Any) -> None:
        pass

    def close(self) -> None:
        pass

    def generate_structured(
        self, *, system_prompt: str, user_prompt: str, response_model: type[Any]
    ) -> Any:
        if self.failure:
            raise ProviderError(self.failure)  # type: ignore[arg-type]
        payload = json.loads(user_prompt)
        if response_model is ModelAnswerAlignment:
            return response_model.model_validate({
                "requested_fact": payload["question"], "requested_scope": "fixture",
                "answers_requested_fact": "matches", "field_matches": "matches",
                "source_scope_matches": "matches", "reason": "synthetic alignment",
            })
        if response_model is ModelSupportDecision:
            return response_model.model_validate({"verdict": "supported", "reason": "fixture"})
        if response_model is ModelClaimValidation:
            return response_model.model_validate({
                "verdict": "supported", "reason": "fixture", "atomicity": "matches",
                "scope_preservation": "matches", "category_matches": "matches",
            })
        if issubclass(response_model, ModelGroundedAnswer):
            if self.abstain:
                return response_model.model_validate({
                    "status": "insufficient_evidence", "answer": "", "claims": [],
                })
            question = payload["question"]
            if "日期" in question:
                phrase, fact = "2026年3月1日", "付款日期为2026年3月1日。"
            elif "乙案" in question:
                phrase, fact = "600000", "交易金额为600000元。"
            elif "第二" in question:
                phrase, fact = "500000", "交易金额为500000元。"
            else:
                phrase, fact = "450000", "交易金额为450000元。"
            evidence = next(item for item in payload["evidence"] if phrase in item["quote"])
            return response_model.model_validate({
                "status": "answered", "answer": fact,
                "claims": [{"text": fact, "citation_ids": [evidence["citation_id"]],
                            "confidence": 0.9}],
            })
        if issubclass(response_model, ModelClaimExtraction):
            return response_model.model_validate({
                "claims": [{"text": payload["answer_claim"],
                            "kind": "date" if "日期" in payload["answer_claim"] else "amount",
                            "citation_ids": ["E1"]}],
            })
        raise AssertionError("unexpected schema")


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, type[FakeLLM]]:
    monkeypatch.setenv("CASEFILE_APP_HOME", str(tmp_path))
    monkeypatch.setenv("CASEFILE_ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.setenv("CASEFILE_LLM_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("CASEFILE_LLM_API_KEY", "fixture-secret")
    monkeypatch.setenv("CASEFILE_LLM_MODEL", "fake")
    monkeypatch.setenv("CASEFILE_LLM_ENABLED", "true")
    monkeypatch.setattr(
        "casefile_ai.application.bootstrap.LocalSentenceTransformerEmbeddingProvider",
        lambda **values: LocalSentenceTransformerEmbeddingProvider(
            **values, encoder=Encoder()
        ),
    )
    FakeLLM.failure = None
    FakeLLM.abstain = False
    monkeypatch.setattr(
        "casefile_ai.application.bootstrap.OpenAICompatibleLLMProvider",
        FakeLLM,
    )
    return tmp_path, FakeLLM


@pytest.fixture
def pdf(cli_env: tuple[Path, type[FakeLLM]]) -> Path:
    path = cli_env[0] / "synthetic-450000.pdf"
    write_pdf(path, "交易金额为450000元。付款日期为2026年3月1日。")
    return path


def write_pdf(path: Path, text: str) -> None:
    pdfmetrics.registerFont(TTFont("CliSimHei", r"C:\Windows\Fonts\simhei.ttf"))
    document = canvas.Canvas(str(path))
    document.setFont("CliSimHei", 14)
    document.drawString(60, 750, text)
    document.save()


def invoke(*args: str) -> tuple[int, Any, str]:
    result = runner.invoke(app, [*args, "--json"])
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    payload = json.loads(result.stdout if result.exit_code == 0 else result.stderr)
    return result.exit_code, payload, result.output


def test_case_aliases_help_and_json(cli_env: tuple[Path, type[FakeLLM]]) -> None:
    for args in ((), ("case",), ("document",), ("index",), ("search",),
                 ("ask",), ("claim",), ("contradiction",), ("timeline",),
                 ("export",)):
        assert runner.invoke(app, [*args, "--help"]).exit_code == 0
    code, case, _ = invoke("case", "create", "合成 CLI 案件")
    assert code == 0 and case["ok"] and case["data"]["name"] == "合成 CLI 案件"
    code, cases, _ = invoke("case-list")
    assert code == 0 and len(cases["data"]) == 1
    code, legacy, _ = invoke("case-create", "旧命令")
    assert code == 0 and legacy["data"]["name"] == "旧命令"
    for args in (("search", "query"), ("search", "--case", "any", "query", "--limit", "0")):
        code, error, output = invoke(*args)
        assert code == 2 and error["error"]["code"] == "invalid_arguments"
        assert "Traceback" not in output


def test_local_workflow_json_isolation_and_export(
    cli_env: tuple[Path, type[FakeLLM]], pdf: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = invoke("case", "create", "甲案")[1]["data"]["case_id"]
    other = invoke("case", "create", "乙案")[1]["data"]["case_id"]
    assert invoke("document", "list", "--case", other)[1]["data"] == []
    assert invoke("index", "status", "--case", first)[1]["data"]["status"] == "not_indexed"
    code, error, _ = invoke("search", "--case", first, "450000")
    assert code == 4 and error["error"]["code"] == "index_not_ready"
    imported = invoke("document", "import", "--case", first, str(pdf))[1]["data"]
    assert imported["page_count"] == 1
    duplicate = invoke("document", "import", "--case", first, str(pdf))[1]["data"]
    assert duplicate["duplicate"]
    assert duplicate["document_version_id"] == imported["document_version_id"]
    second_pdf = cli_env[0] / "synthetic-500000.pdf"
    write_pdf(second_pdf, "交易金额为500000元。付款日期为2026年3月2日。")
    second_doc = invoke("document", "import", "--case", first, str(second_pdf))[1]["data"]
    assert len(invoke("document", "list", "--case", first)[1]["data"]) == 2
    assert invoke("document", "list", "--case", other)[1]["data"] == []
    assert invoke("index", "build", "--case", first)[0] == 0
    assert invoke("index", "status", "--case", first)[1]["data"]["status"] == "ready"
    hits = invoke("search", "--case", first, "450000")[1]["data"]
    assert hits and hits[0]["document_id"] == imported["document_id"]
    assert hits[0]["page_number"] and hits[0]["score"] > 0
    assert invoke("search", "--case", other, "450000")[0] == 4
    other_pdf = cli_env[0] / "other-600000.pdf"
    write_pdf(other_pdf, "乙案交易金额为600000元。")
    other_doc = invoke("document", "import", "--case", other, str(other_pdf))[1]["data"]
    assert invoke("document", "import", "--case", other, str(other_pdf),
                  "--document", imported["document_id"])[0] == 3
    assert invoke("index", "build", "--case", other)[0] == 0
    other_hits = invoke("search", "--case", other, "450000")[1]["data"]
    assert all(hit["document_id"] == other_doc["document_id"] for hit in other_hits)
    other_answer = invoke("ask", "--case", other, "乙案交易金额是多少？")[1]["data"]
    assert other_answer["status"] == "answered"
    assert all(cite["document_id"] == other_doc["document_id"]
               for cite in other_answer["citations"])
    assert invoke("claim", "extract", "--answer", other_answer["answer_id"])[0] == 0
    ask_code, ask_payload, _ = invoke("ask", "--case", first, "交易金额是多少？")
    assert ask_code == 0, ask_payload
    answer = ask_payload["data"]
    assert answer["status"] == "answered"
    assert answer["metadata"]["validation_status"] == "validated"
    assert answer["citations"] and answer["citations"][0]["document_id"] == imported["document_id"]
    claims = invoke("claim", "extract", "--answer", answer["answer_id"])[1]["data"]
    assert claims and claims[0]["evidence_ids"]
    second_answer = invoke("ask", "--case", first, "第二笔交易金额是多少？")[1]["data"]
    assert second_answer["citations"][0]["document_id"] == second_doc["document_id"]
    assert invoke("claim", "extract", "--answer", second_answer["answer_id"])[1]["data"]
    date_answer = invoke("ask", "--case", first, "付款日期是什么？")[1]["data"]
    assert invoke("claim", "extract", "--answer", date_answer["answer_id"])[1]["data"]
    assert all(claim["answer_id"] != other_answer["answer_id"]
               for claim in invoke("claim", "list", "--case", first)[1]["data"])
    assert invoke("contradiction", "list", "--case", other)[1]["data"] == []
    decisions = invoke("contradiction", "run", "--case", first)[1]["data"]
    assert decisions and decisions[0]["verdict"] in {"contradiction", "uncertain"}
    saved = invoke("contradiction", "list", "--case", first)[1]["data"]
    assert [item["result_id"] for item in saved] == [item["result_id"] for item in decisions]
    events = invoke("timeline", "--case", first)[1]["data"]
    assert any(item["date_value"] == "2026-03-01" and item["evidence"] for item in events)
    report = invoke("export", "--case", first, "--format", "json")[1]["data"]["content"]
    assert report["case"]["case_id"] == first and len(report["answers"]) == 3
    assert all(item["answer_id"] != other_answer["answer_id"] for item in report["answers"])
    markdown = invoke("export", "--case", first, "--format", "markdown")[1]["data"]["content"]
    assert "甲案" in markdown and "交易金额" in markdown
    target = cli_env[0] / "analysis.md"
    export_args = ("export", "--format", "markdown", "--output", str(target))
    assert invoke(*export_args, "--case", first)[0] == 0
    assert invoke(*export_args, "--case", other)[1]["error"]["code"] == "output_exists"
    assert "甲案" in target.read_text(encoding="utf-8")
    assert "乙案" not in target.read_text(encoding="utf-8")
    monkeypatch.delenv("CASEFILE_LLM_API_KEY")
    assert len(invoke("claim", "list", "--case", first)[1]["data"]) == 3
    assert invoke("contradiction", "list", "--case", first)[1]["data"]
    assert any(item["date_value"] == "2026-03-01"
               for item in invoke("timeline", "--case", first)[1]["data"])


def test_provider_abstain_errors_and_missing_case(
    cli_env: tuple[Path, type[FakeLLM]], pdf: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = invoke("case", "create", "异常案")[1]["data"]["case_id"]
    assert invoke("document", "list", "--case", "missing")[0] == 3
    assert invoke("document", "import", "--case", case, str(pdf) + ".missing")[0] == 3
    assert invoke("document", "import", "--case", case, str(pdf))[0] == 0
    assert invoke("index", "build", "--case", case)[0] == 0
    cli_env[1].abstain = True
    result = invoke("ask", "--case", case, "未知情况？")[1]["data"]
    assert result["status"] == "insufficient_evidence" and not result["citations"]
    cli_env[1].abstain = False
    for category in ("rate_limited", "timeout"):
        cli_env[1].failure = category
        code, payload, output = invoke("ask", "--case", case, f"{category} 金额？")
        assert code == 5 and payload["error"]["code"] == category
        assert "fixture-secret" not in output and "Traceback" not in output
    cli_env[1].failure = None
    monkeypatch.delenv("CASEFILE_LLM_API_KEY")
    code, payload, _ = invoke("ask", "--case", case, "新问题？")
    assert code == 5 and payload["error"]["code"] == "model_not_configured"
    assert invoke("search", "--case", case, "450000")[0] == 0
    assert invoke("timeline", "--case", case)[0] == 0


def test_stale_version_and_index_exit_code(
    cli_env: tuple[Path, type[FakeLLM]], pdf: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = invoke("case", "create", "版本案")[1]["data"]["case_id"]
    document = invoke("document", "import", "--case", case, str(pdf))[1]["data"]
    assert invoke("index", "build", "--case", case)[0] == 0
    monkeypatch.setenv("CASEFILE_EMBEDDING_MODEL", "fixture-alternate")
    monkeypatch.setenv("CASEFILE_EMBEDDING_REVISION", "rev2")
    assert invoke("index", "status", "--case", case)[1]["data"]["status"] == "stale"
    assert invoke("search", "--case", case, "450000")[0] == 4
    assert invoke("ask", "--case", case, "金额？")[0] == 4
    monkeypatch.delenv("CASEFILE_EMBEDDING_MODEL")
    monkeypatch.delenv("CASEFILE_EMBEDDING_REVISION")
    updated = cli_env[0] / "updated.pdf"
    write_pdf(updated, "交易金额为500000元。付款日期为2026年3月2日。")
    version = invoke("document", "import", "--case", case, str(updated),
                     "--document", document["document_id"])[1]["data"]
    assert version["version_number"] == 2
    old_again = invoke("document", "import", "--case", case, str(pdf),
                       "--document", document["document_id"])[1]["data"]
    assert old_again["duplicate"] and old_again["version_number"] == 1
    assert invoke("document", "list", "--case", case)[1]["data"][0]["version_number"] == 2
    assert invoke("index", "status", "--case", case)[1]["data"]["status"] == "not_indexed"
    assert invoke("search", "--case", case, "450000")[0] == 4
    assert invoke("ask", "--case", case, "金额？")[0] == 4
    assert invoke("index", "build", "--case", case)[0] == 0
    assert invoke("index", "status", "--case", case)[1]["data"]["status"] == "ready"
