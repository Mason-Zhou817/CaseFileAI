"""Real stdio MCP client checks against a spawned server process."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_cli_workflow import write_pdf

TOOLS = {
    "list_cases",
    "create_case",
    "list_documents",
    "import_document",
    "build_index",
    "get_index_status",
    "search_evidence",
    "ask_case",
    "submit_answer_feedback",
    "get_feedback_summary",
    "extract_claims",
    "list_claims",
    "run_contradiction_analysis",
    "list_contradictions",
    "get_timeline",
    "export_case_analysis",
    "get_system_status",
}
SERVER = Path(__file__).with_name("mcp_fake_server.py")


def server_parameters(home: Path, roots: Path, failure: str = "") -> StdioServerParameters:
    env = os.environ.copy()
    env.update(
        {
            "CASEFILE_APP_HOME": str(home),
            "CASEFILE_ENV_FILE": str(home / "absent.env"),
            "CASEFILE_MCP_IMPORT_ROOTS": str(roots),
            "CASEFILE_LLM_BASE_URL": "https://model.invalid/v1",
            "CASEFILE_LLM_API_KEY": "fixture-secret",
            "CASEFILE_LLM_MODEL": "fake",
            "CASEFILE_LLM_ENABLED": "true",
            "CASEFILE_MCP_FAKE_FAILURE": failure,
        }
    )
    return StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)


async def call(session: ClientSession, tool_name: str, **arguments: Any) -> dict[str, Any]:
    result = await session.call_tool(tool_name, arguments)
    assert not result.is_error, result
    data = result.structured_content
    assert isinstance(data, dict), result
    return data


@pytest.mark.asyncio
async def test_installed_entrypoint_stdio_start_and_exit(tmp_path: Path) -> None:
    command = shutil.which("casefile-ai-mcp")
    assert command is not None
    env = {key: value for key, value in os.environ.items() if not key.startswith("CASEFILE_")}
    env["CASEFILE_APP_HOME"] = str(tmp_path / "home")
    env["CASEFILE_ENV_FILE"] = str(tmp_path / "absent.env")
    params = StdioServerParameters(command=command, env=env)
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "casefile-ai"
            assert len((await session.list_tools()).tools) == 17
            assert (await call(session, "list_cases"))["data"] == []
            assert (await call(session, "get_system_status"))["ok"]


@pytest.mark.asyncio
async def test_mcp_stdio_workflow_and_security(tmp_path: Path) -> None:
    imports = tmp_path / "imports"
    imports.mkdir()
    pdf = imports / "synthetic.pdf"
    write_pdf(pdf, "交易金额为450000元。付款日期为2026年3月1日。")
    next_pdf = imports / "next.pdf"
    write_pdf(next_pdf, "第二笔交易金额为500000元。付款日期为2026年3月2日。")
    second_pdf = imports / "second.pdf"
    write_pdf(second_pdf, "乙案交易金额为600000元。")
    outside = tmp_path / "outside.pdf"
    write_pdf(outside, "不属于允许目录。")
    (imports / "secret.env").write_text("API_KEY=fixture-secret")
    async with stdio_client(server_parameters(tmp_path / "home", imports)) as streams:
        async with ClientSession(*streams) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "casefile-ai"
            listed = await session.list_tools()
            assert {item.name for item in listed.tools} == TOOLS
            assert "get_document_page" not in TOOLS
            for item in listed.tools:
                assert item.input_schema["type"] == "object"
                assert item.input_schema.get("additionalProperties") is False
                if item.name in {"ask_case", "extract_claims", "run_contradiction_analysis"}:
                    assert "May invoke configured cloud model" in (item.description or "")
                if item.name in {"submit_answer_feedback", "get_feedback_summary"}:
                    assert "locally" in (item.description or "")
                    assert "never automatically used for model training" in (item.description or "")
            search_schema = next(
                item.input_schema for item in listed.tools if item.name == "search_evidence"
            )
            assert search_schema["properties"]["limit"]["minimum"] == 1
            assert search_schema["properties"]["limit"]["maximum"] == 50

            status = await call(session, "get_system_status")
            assert status["ok"] and status["data"]["version"], status
            assert str(tmp_path) not in str(status)
            assert "fixture-secret" not in str(status)
            assert (await call(session, "list_cases"))["data"] == []
            first = (await call(session, "create_case", name="MCP 甲案"))["data"]["case_id"]
            second = (await call(session, "create_case", name="MCP 乙案"))["data"]["case_id"]
            assert (await call(session, "list_documents", case_id=first))["data"] == []
            assert (await call(session, "get_index_status", case_id=first))["data"][
                "status"
            ] == "not_indexed"
            assert (await call(session, "search_evidence", case_id=first, query="450000"))["error"][
                "code"
            ] == "index_not_ready"
            assert (await call(session, "list_documents", case_id="missing"))["error"][
                "code"
            ] == "not_found"

            for denied in (outside, imports / ".." / "outside.pdf", imports / "secret.env"):
                result = await call(session, "import_document", case_id=first, path=str(denied))
                assert not result["ok"], denied
            malformed = await session.call_tool(
                "search_evidence", {"case_id": first, "limit": "bad"}
            )
            assert malformed.is_error
            traversed_export = await session.call_tool(
                "export_case_analysis",
                {"case_id": first, "format": "json", "output": "../../escaped.json"},
            )
            assert traversed_export.is_error
            assert not (tmp_path / "escaped.json").exists()
            imported = await call(session, "import_document", case_id=first, path=str(pdf))
            assert imported["ok"], imported
            document_id = imported["data"]["document_id"]
            assert (await call(session, "import_document", case_id=first, path=str(next_pdf)))["ok"]
            assert (await call(session, "list_documents", case_id=second))["data"] == []
            indexed = await call(session, "build_index", case_id=first)
            assert indexed["ok"], indexed
            hits = await call(session, "search_evidence", case_id=first, query="450000")
            assert hits["ok"] and hits["data"][0]["document_id"] == document_id
            assert hits["data"][0]["page"] == 1
            second_import = await call(
                session, "import_document", case_id=second, path=str(second_pdf)
            )
            assert second_import["ok"]
            assert (await call(session, "build_index", case_id=second))["ok"]
            second_hits = await call(session, "search_evidence", case_id=second, query="450000")
            assert second_hits["ok"]
            assert all(
                hit["document_id"] == second_import["data"]["document_id"]
                for hit in second_hits["data"]
            )

            answer = await call(session, "ask_case", case_id=first, question="交易金额是多少？")
            assert answer["ok"] and answer["data"]["status"] == "answered", answer
            answer_id = answer["data"]["answer_id"]
            feedback = await call(
                session,
                "submit_answer_feedback",
                case_id=first,
                answer_id=answer_id,
                rating="incorrect",
                category="citation",
                note="合成页码错误",
            )
            assert feedback["ok"] and feedback["data"]["review_status"] == "pending"
            summary = await call(session, "get_feedback_summary", case_id=first)
            assert summary["data"]["category"]["citation"] == 1
            cross_feedback = await call(
                session,
                "submit_answer_feedback",
                case_id=second,
                answer_id=answer_id,
                rating="incorrect",
                category="citation",
            )
            assert (
                not cross_feedback["ok"] and cross_feedback["error"]["code"] == "cross_case_answer"
            )
            assert answer["data"]["citations"][0]["document_id"] == document_id
            cross = await call(session, "extract_claims", case_id=second, answer_id=answer_id)
            assert not cross["ok"] and cross["error"]["code"] == "not_found"
            claims = await call(session, "extract_claims", case_id=first, answer_id=answer_id)
            assert claims["ok"] and claims["data"], claims
            second_answer = await call(
                session, "ask_case", case_id=first, question="第二笔交易金额是多少？"
            )
            assert second_answer["ok"], second_answer
            assert (
                await call(
                    session,
                    "extract_claims",
                    case_id=first,
                    answer_id=second_answer["data"]["answer_id"],
                )
            )["ok"]
            date_answer = await call(
                session, "ask_case", case_id=first, question="付款日期是什么？"
            )
            assert date_answer["ok"], date_answer
            assert (
                await call(
                    session,
                    "extract_claims",
                    case_id=first,
                    answer_id=date_answer["data"]["answer_id"],
                )
            )["ok"]
            assert (await call(session, "list_claims", case_id=first))["data"]
            conflicts = await call(session, "run_contradiction_analysis", case_id=first)
            assert conflicts["ok"] and conflicts["data"], conflicts
            assert (await call(session, "list_contradictions", case_id=first))["data"]
            events = await call(session, "get_timeline", case_id=first)
            assert events["ok"] and any(
                item["date_value"] == "2026-03-01" for item in events["data"]
            )
            report = await call(session, "export_case_analysis", case_id=first, format="json")
            assert report["ok"] and report["data"]["case"]["case_id"] == first
            assert str(tmp_path) not in str(report)


@pytest.mark.asyncio
async def test_mcp_import_disabled_and_model_unconfigured(tmp_path: Path) -> None:
    roots = tmp_path / "nonexistent"
    params = server_parameters(tmp_path / "home", roots)
    assert params.env is not None
    params.env["CASEFILE_LLM_API_KEY"] = ""
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            case_id = (await call(session, "create_case", name="配置边界"))["data"]["case_id"]
            denied = await call(session, "import_document", case_id=case_id, path=str(tmp_path))
            assert not denied["ok"] and denied["error"]["code"] == "file_access_denied"
            answer = await call(session, "ask_case", case_id=case_id, question="金额是多少？")
            assert answer["error"]["code"] == "model_not_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rate_limited", "timeout"])
async def test_mcp_provider_failure_is_redacted(tmp_path: Path, failure: str) -> None:
    imports = tmp_path / "imports"
    imports.mkdir()
    pdf = imports / "synthetic.pdf"
    write_pdf(pdf, "交易金额为450000元。")
    async with stdio_client(server_parameters(tmp_path / "home", imports, failure)) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            case_id = (await call(session, "create_case", name="故障测试"))["data"]["case_id"]
            assert (await call(session, "import_document", case_id=case_id, path=str(pdf)))["ok"]
            assert (await call(session, "build_index", case_id=case_id))["ok"]
            result = await call(session, "ask_case", case_id=case_id, question="交易金额是多少？")
            assert result["error"]["code"] == failure
            assert "fixture-secret" not in str(result)
            assert "Traceback" not in str(result)
