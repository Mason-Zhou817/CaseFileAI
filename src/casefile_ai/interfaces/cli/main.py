"""Scriptable CLI over the same application runtime used by the GUI and API."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import typer
import uvicorn
from typer._click.exceptions import ClickException
from typer.core import TyperGroup

from casefile_ai.application.bootstrap import (
    ApplicationRuntime,
    build_application_runtime,
    prepare_local_storage,
)
from casefile_ai.application.claim_contracts import ClaimExtractionError
from casefile_ai.application.contradiction_contracts import ContradictionValidationError
from casefile_ai.application.data_contracts import DataManagementError
from casefile_ai.application.data_messages import DATA_MESSAGES
from casefile_ai.application.document_contracts import DocumentNotFoundError, InvalidDocumentError
from casefile_ai.application.material_contracts import IndexInProgressError, IndexOperationError
from casefile_ai.application.qa_contracts import InvalidQuestionError, QAUnavailableError
from casefile_ai.application.retrieval_contracts import InvalidSearchError, RetrievalIndexError
from casefile_ai.application.system_contracts import SystemStatus
from casefile_ai.config import Settings
from casefile_ai.domain.exceptions import CaseNotFoundError, InvalidCaseError
from casefile_ai.infrastructure.llm.errors import MESSAGES, provider_failure
from casefile_ai.infrastructure.safe_logging import SafeLogging, safe_event
from casefile_ai.interfaces.api.main import create_app


class JsonErrorGroup(TyperGroup):
    """Keep parser failures machine-readable when --json was requested."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        supplied = list(sys.argv[1:] if args is None else args)
        options = supplied[:supplied.index("--")] if "--" in supplied else supplied
        if "--json" not in options:
            return super().main(
                args=args, prog_name=prog_name, complete_var=complete_var,
                standalone_mode=standalone_mode, windows_expand_args=windows_expand_args,
                **extra,
            )
        try:
            result = super().main(
                args=args, prog_name=prog_name, complete_var=complete_var,
                standalone_mode=False, windows_expand_args=windows_expand_args,
                **extra,
            )
        except ClickException:
            typer.echo(json.dumps({
                "ok": False,
                "error": {"code": "invalid_arguments", "message": "命令参数无效，请查看 --help。"},
            }, ensure_ascii=False), err=True)
            if standalone_mode:
                raise SystemExit(2) from None
            return 2
        if standalone_mode:
            raise SystemExit(result if isinstance(result, int) else 0)
        return result


app = typer.Typer(cls=JsonErrorGroup, help="本地卷宗分析 CLI",
                  pretty_exceptions_show_locals=False)
case_app = typer.Typer(help="案件管理")
document_app = typer.Typer(help="PDF 材料管理")
index_app = typer.Typer(help="本地索引")
claim_app = typer.Typer(help="已验证回答的事实抽取")
contradiction_app = typer.Typer(help="矛盾判断")
for name, group in (
    ("case", case_app), ("document", document_app), ("index", index_app),
    ("claim", claim_app), ("contradiction", contradiction_app),
):
    app.add_typer(group, name=name)


def _prepare(settings: Settings) -> None:
    prepare_local_storage(settings)


def _startup_report(item: SystemStatus, host: str, port: int) -> None:
    typer.echo("CaseFile AI")
    typer.echo(f"本地服务：http://{host}:{port}")
    typer.echo(f"数据目录：{item.data_directory.path}")
    ready = item.embedding.dependency_available and item.embedding.available
    typer.echo(f"本地检索：{'已准备（首次使用时加载）' if ready else '未就绪，请查看系统状态'}")
    ocr_ready = item.ocr.dependency_available and item.ocr.model_available
    typer.echo(f"OCR：{'已准备' if ocr_ready else '未就绪'}")
    provider = item.qa_provider
    label = (
        "未配置" if not provider.configured
        else ("已配置，尚未验证连接" if provider.enabled else "已配置，外发未启用")
    )
    typer.echo(f"模型服务：{label}")
    typer.echo(f"日志目录：{item.logs.directory}")
    typer.echo("系统状态：/api/system/status")
    for notice in item.notices:
        typer.echo(notice)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Migrate the local database and start the browser GUI."""
    try:
        settings = Settings()
    except Exception:
        typer.echo("配置无效，请检查本地配置文件；不会输出配置内容。", err=True)
        raise typer.Exit(1) from None
    logs = SafeLogging(settings.log_dir, settings.log_level)
    try:
        try:
            _prepare(settings)
        except Exception:
            safe_event("startup", "failed", level=logging.ERROR)
            typer.echo("启动检查失败：请检查数据目录权限、数据库或迁移资源。", err=True)
            raise typer.Exit(1) from None
        uvicorn.run(
            create_app(
                settings=settings,
                log_manager=logs,
                startup_report=lambda item: _startup_report(item, host, port),
            ),
            host=host,
            port=port,
            log_level=settings.log_level.lower(),
            access_log=False,
            log_config=None,
        )
    finally:
        logs.close()


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _emit(value: Any, json_mode: bool, human: Callable[[Any], str] | None = None) -> None:
    data = _plain(value)
    if json_mode:
        typer.echo(json.dumps({"ok": True, "data": data}, ensure_ascii=False, allow_nan=False))
    elif human is not None:
        typer.echo(human(data))
    else:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))


def _failure(exc: Exception) -> tuple[int, str, str]:
    provider = provider_failure(exc)
    if provider is not None:
        return 5, provider.category, MESSAGES[provider.category]
    if isinstance(exc, CaseNotFoundError | DocumentNotFoundError):
        return 3, "not_found", "未找到指定案件、材料或回答。"
    if isinstance(exc, FileNotFoundError):
        return 3, "file_not_found", "未找到指定文件。"
    if isinstance(exc, FileExistsError):
        return 4, "output_exists", "输出文件已存在；请选择新文件名。"
    if isinstance(exc, PermissionError):
        return 4, "file_access_denied", "无法访问指定文件或目录。"
    if isinstance(exc, InvalidCaseError):
        return 2, "invalid_case", "案件名称或说明无效，请检查长度和内容。"
    if isinstance(exc, InvalidDocumentError):
        return 2, "invalid_document", "PDF 无效、加密或超出导入限制，请检查文件。"
    if isinstance(exc, InvalidQuestionError):
        return 2, "invalid_question", "问题须包含 1–1000 个字符。"
    if isinstance(exc, InvalidSearchError):
        return 2, "invalid_search", "检索词不能为空，limit 须为 1–50。"
    if isinstance(exc, IndexInProgressError):
        return 4, "index_processing", "当前案件正在建立索引，请稍后重试。"
    if isinstance(exc, _IndexNotReady):
        return 4, "index_not_ready", "当前案件索引未就绪，请先建立或更新索引。"
    if isinstance(exc, IndexOperationError | RetrievalIndexError):
        return 4, "index_failed", "索引处理失败，请查看系统状态后重试。"
    if isinstance(exc, ClaimExtractionError | ContradictionValidationError):
        return 6, "validation_failed", "证据或模型结果未通过验证。"
    if isinstance(exc, DataManagementError):
        code = 3 if exc.category == "artifact_not_found" else 4
        return code, exc.category, DATA_MESSAGES.get(exc.category, "导出未完成。")
    if isinstance(exc, QAUnavailableError):
        return 5, "model_unavailable", "模型服务暂不可用。"
    diagnostic_id = str(uuid4())
    safe_event("runtime", "unhandled_error", diagnostic_id=diagnostic_id, level=logging.ERROR)
    return 1, "internal_error", f"处理失败，请使用诊断编号 {diagnostic_id} 查看安全日志。"


def _execute(
    operation: Callable[[ApplicationRuntime], Any],
    json_mode: bool,
    human: Callable[[Any], str] | None = None,
) -> None:
    logs: SafeLogging | None = None
    runtime: ApplicationRuntime | None = None
    try:
        settings = Settings()
        logs = SafeLogging(settings.log_dir, settings.log_level, console=False)
        prepare_local_storage(settings)
        runtime = build_application_runtime(settings, logs)
        _emit(operation(runtime), json_mode, human)
    except Exception as exc:
        code, category, message = _failure(exc)
        if json_mode:
            typer.echo(json.dumps({"ok": False, "error": {"code": category, "message": message}},
                                  ensure_ascii=False), err=True)
        else:
            typer.echo(f"错误：{message} [{category}]", err=True)
        raise typer.Exit(code) from None
    finally:
        if runtime is not None:
            runtime.close()
        if logs is not None:
            logs.close()


def _case_create(name: str, description: str | None, json_mode: bool) -> None:
    _execute(lambda runtime: runtime.cases.service.create_case(name, description), json_mode,
             lambda data: f"{data['case_id']}\t{data['name']}")


def _case_list(json_mode: bool) -> None:
    _execute(lambda runtime: runtime.cases.service.list_cases(), json_mode,
             lambda rows: "\n".join(f"{row['case_id']}\t{row['name']}" for row in rows))


@case_app.command("create")
def case_create(name: str, description: str | None = None, json: bool = False) -> None:
    """创建案件。"""
    _case_create(name, description, json)


@case_app.command("list")
def case_list(json: bool = False) -> None:
    """列出案件。"""
    _case_list(json)


@app.command("case-create", hidden=True)
def case_create_legacy(name: str, description: str | None = None, json: bool = False) -> None:
    """Legacy alias for case create."""
    _case_create(name, description, json)


@app.command("case-list", hidden=True)
def case_list_legacy(json: bool = False) -> None:
    """Legacy alias for case list."""
    _case_list(json)


@document_app.command("list")
def document_list(case: str = typer.Option(..., "--case"), json: bool = False) -> None:
    """列出案件材料及当前版本。"""
    def run(runtime: ApplicationRuntime) -> Any:
        runtime.cases.service.get_case(case)
        return runtime.ingestion.list_documents(case)
    _execute(run, json)


@document_app.command("import")
def document_import(
    path: Path, case: str = typer.Option(..., "--case"),
    document: str | None = None, json: bool = False,
) -> None:
    """导入 PDF；可用 --document 为已有材料添加版本。"""
    def run(runtime: ApplicationRuntime) -> Any:
        runtime.cases.service.get_case(case)
        with path.open("rb") as stream:
            return runtime.ingestion.upload_document(case, stream, path.name, document)
    _execute(run, json)


@index_app.command("build")
def index_build(case: str = typer.Option(..., "--case"), json: bool = False) -> None:
    """建立或更新本地索引。"""
    def run(runtime: ApplicationRuntime) -> Any:
        runtime.cases.service.get_case(case)
        return runtime.retrieval.index_case(case)
    _execute(run, json)


@index_app.command("status")
def index_status(case: str = typer.Option(..., "--case"), json: bool = False) -> None:
    """查看与 GUI 相同的材料索引状态。"""
    def run(runtime: ApplicationRuntime) -> Any:
        runtime.cases.service.get_case(case)
        return runtime.materials.snapshot(case)
    _execute(run, json)


def _ready(runtime: ApplicationRuntime, case: str) -> None:
    runtime.cases.service.get_case(case)
    state = runtime.materials.snapshot(case)
    if state.status != "ready":
        raise _IndexNotReady(state.status)


class _IndexNotReady(RuntimeError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(status)


@app.command()
def search(
    query: str, case: str = typer.Option(..., "--case"),
    limit: int = typer.Option(10, min=1, max=50), json: bool = False,
) -> None:
    """在当前案件的已索引证据中进行混合检索。"""
    def run(runtime: ApplicationRuntime) -> Any:
        _ready(runtime, case)
        return runtime.retrieval.search(case, query, limit=limit)
    _execute(run, json, lambda rows: "\n\n".join(
        f"#{item['rank']} {item['filename']} · version {item['document_version_id']} "
        f"· 第 {item['page_number']} 页 · score {item['score']:.4f}\n{item['text']}"
        for item in rows
    ))


@app.command()
def ask(question: str, case: str = typer.Option(..., "--case"), json: bool = False) -> None:
    """完整证据约束问答，包括引用和对齐校验。"""
    def run(runtime: ApplicationRuntime) -> Any:
        runtime.cases.service.get_case(case)
        runtime.require_model()
        _ready(runtime, case)
        answer = runtime.qa.ask_case(case, question)
        data = asdict(answer)
        data["status"] = "insufficient_evidence" if answer.abstained else "answered"
        return data
    _execute(run, json, lambda data: (
        f"状态：{data['status']}\n回答：{data['answer']}\n"
        f"Answer ID：{data['answer_id']}\n"
        f"原因：{data['metadata']['refusal_reason'] or '无'}\n"
        + "\n".join(
            f"引用：{item['filename']} 第 {item['page_number']} 页 · {item['quote']}"
            for item in data["citations"]
        )
    ))


@claim_app.command("extract")
def claim_extract(answer: str = typer.Option(..., "--answer"), json: bool = False) -> None:
    """从指定已验证 Answer 提取 Claim。"""
    def run(runtime: ApplicationRuntime) -> Any:
        runtime.require_model()
        return runtime.workspace.extract_by_answer(answer)
    _execute(run, json)


@claim_app.command("list")
def claim_list(case: str = typer.Option(..., "--case"), json: bool = False) -> None:
    """列出案件已保存的各模型版本 Claim。"""
    _execute(lambda runtime: runtime.data_management.read_analysis(case).claims, json)


@contradiction_app.command("run")
def contradiction_run(case: str = typer.Option(..., "--case"), json: bool = False) -> None:
    """运行矛盾判断；不确定结果保留 uncertain。"""
    def run(runtime: ApplicationRuntime) -> Any:
        runtime.require_semantic_model(case)
        return runtime.workspace.detect(case)
    _execute(run, json)


@contradiction_app.command("list")
def contradiction_list(case: str = typer.Option(..., "--case"), json: bool = False) -> None:
    """列出已保存的矛盾判断。"""
    _execute(lambda runtime: runtime.data_management.read_analysis(case).contradictions, json)


@app.command()
def timeline(case: str = typer.Option(..., "--case"), json: bool = False) -> None:
    """按已验证 Claim 构建案件时间线。"""
    _execute(lambda runtime: runtime.data_management.read_analysis(case).timeline, json)


@app.command()
def export(
    case: str = typer.Option(..., "--case"),
    format: Literal["json", "markdown"] = typer.Option(..., "--format"),
    output: Path | None = None,
    json: bool = False,
) -> None:
    """导出案件分析。--output 只创建新文件，不覆盖已有文件。"""
    def run(runtime: ApplicationRuntime) -> Any:
        runtime.cases.service.get_case(case)
        artifact = runtime.data_management.export_analysis(case, format)
        path, _ = runtime.data_management.download(artifact.artifact_id)
        content = path.read_text(encoding="utf-8")
        if output is not None:
            with output.open("x", encoding="utf-8") as destination:
                destination.write(content)
            return {"format": format, "written": True, "bytes": len(content.encode("utf-8"))}
        return {"format": format, "content": json_loads(content) if format == "json" else content}
    _execute(run, json, lambda data: (
        f"已写入新文件（{data['bytes']} bytes）。" if data.get("written") else
        (json_dumps(data["content"]) if format == "json" else str(data["content"]))
    ))


def json_loads(value: str) -> Any:
    return json.loads(value)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
