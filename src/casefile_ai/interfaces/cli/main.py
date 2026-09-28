"""Small CLI over the shared application layer."""

from __future__ import annotations

import logging

import typer
import uvicorn

from casefile_ai.application.bootstrap import build_case_application, prepare_local_storage
from casefile_ai.application.system_contracts import SystemStatus
from casefile_ai.config import Settings
from casefile_ai.infrastructure.safe_logging import SafeLogging, safe_event
from casefile_ai.interfaces.api.main import create_app

app = typer.Typer(help="Local source-traceable case analysis", pretty_exceptions_show_locals=False)


def _prepare(settings: Settings) -> None:
    prepare_local_storage(settings)


def _startup_report(item: SystemStatus, host: str, port: int) -> None:
    typer.echo("CaseFile AI")
    typer.echo(f"本地服务：http://{host}:{port}")
    typer.echo(f"数据目录：{item.data_directory.path}")
    typer.echo(f"数据库：{item.database.path}")
    ready = item.embedding.dependency_available and item.embedding.available
    typer.echo(f"本地检索：{'已准备（首次使用时加载）' if ready else '未就绪，请查看系统状态'}")
    ocr_ready = item.ocr.dependency_available and item.ocr.model_available
    typer.echo(f"OCR：{'已准备' if ocr_ready else '未就绪'}")
    provider = item.qa_provider
    label = (
        "未配置"
        if not provider.configured
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


@app.command("case-create")
def case_create(name: str, description: str | None = None) -> None:
    """Create a case through the same service used by the GUI."""
    settings = Settings()
    _prepare(settings)
    application = build_case_application(settings.database_url)
    try:
        item = application.service.create_case(name, description)
        typer.echo(f"{item.case_id}\t{item.name}")
    finally:
        application.engine.dispose()


@app.command("case-list")
def case_list() -> None:
    """List local cases through the shared service."""
    settings = Settings()
    _prepare(settings)
    application = build_case_application(settings.database_url)
    try:
        for item in application.service.list_cases():
            typer.echo(f"{item.case_id}\t{item.name}")
    finally:
        application.engine.dispose()
