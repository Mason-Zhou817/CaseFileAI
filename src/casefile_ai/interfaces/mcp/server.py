"""Case-scoped stdio MCP adapter over the shared application runtime."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from casefile_ai import __version__
from casefile_ai.application.bootstrap import (
    ApplicationRuntime,
    build_application_runtime,
    prepare_local_storage,
)
from casefile_ai.application.document_contracts import InvalidDocumentError
from casefile_ai.application.services.analysis_export import render_markdown
from casefile_ai.application.services.documents import MAX_PDF_BYTES
from casefile_ai.config import Settings
from casefile_ai.infrastructure.safe_logging import SafeLogging
from casefile_ai.interfaces.cli.main import _failure, _IndexNotReady


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _result(operation: Callable[[], Any], *, case_id: str | None = None) -> dict[str, Any]:
    try:
        result: dict[str, Any] = {"ok": True, "data": _plain(operation())}
        if case_id is not None:
            result["case_id"] = case_id
        return result
    except Exception as exc:
        _, code, message = _failure(exc)
        return {"ok": False, "error": {"code": code, "message": message}}


def _local_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if ".." in candidate.parts or not candidate.is_absolute() or path.startswith(("\\\\", "//")):
        raise PermissionError("an absolute local path is required")
    # Resolve existing symlink/junction components before checking containment.
    # strict=False avoids probing existence outside the allowlist.
    resolved = candidate.resolve(strict=False)
    if str(resolved).startswith(("\\\\", "//")):
        raise PermissionError("network paths are not allowed")
    return resolved


def import_roots_from_environment() -> tuple[Path, ...]:
    """An unset or invalid allowlist disables MCP import."""
    roots: list[Path] = []
    for entry in os.environ.get("CASEFILE_MCP_IMPORT_ROOTS", "").split(os.pathsep):
        if not entry.strip():
            continue
        try:
            root = _local_path(entry.strip())
            if root.is_dir() and root != Path(root.anchor):
                roots.append(root)
        except (OSError, ValueError, PermissionError):
            continue
    return tuple(roots)


def allowed_pdf(path: str, roots: tuple[Path, ...]) -> Path:
    if not roots:
        raise PermissionError("MCP import is disabled; configure CASEFILE_MCP_IMPORT_ROOTS")
    candidate = _local_path(path)
    if not any(candidate.is_relative_to(root) for root in roots):
        raise PermissionError("file is outside the MCP import allowlist")
    if not candidate.is_file():
        raise InvalidDocumentError("a regular PDF file is required")
    if candidate.suffix.lower() != ".pdf" or candidate.stat().st_size > MAX_PDF_BYTES:
        raise InvalidDocumentError("PDF type or size is unsupported")
    return candidate


def build_server(
    runtime: ApplicationRuntime, *, import_roots: tuple[Path, ...] | None = None
) -> MCPServer[None]:
    """Expose service operations with bounded paths and case-scoped IDs."""
    roots = import_roots_from_environment() if import_roots is None else import_roots
    server: MCPServer[None] = MCPServer(
        name="casefile-ai",
        title="CaseFile AI",
        description="Case-scoped source-traceable analysis over local stdio",
        version=__version__,
    )

    def case(case_id: str) -> None:
        runtime.cases.service.get_case(case_id)

    def ready(case_id: str) -> None:
        case(case_id)
        status = runtime.materials.snapshot(case_id)
        if status.status != "ready":
            raise _IndexNotReady(status.status)

    @server.tool(name="list_cases", structured_output=True)
    def list_cases() -> dict[str, Any]:
        """List locally stored cases."""
        return _result(lambda: runtime.cases.service.list_cases())

    @server.tool(name="create_case", structured_output=True)
    def create_case(name: str, description: str | None = None) -> dict[str, Any]:
        """Create a local case."""
        return _result(lambda: runtime.cases.service.create_case(name, description))

    @server.tool(name="list_documents", structured_output=True)
    def list_documents(case_id: str) -> dict[str, Any]:
        """List document versions belonging to a case."""

        def run() -> Any:
            case(case_id)
            return runtime.ingestion.list_documents(case_id)

        return _result(run, case_id=case_id)

    @server.tool(name="import_document", structured_output=True)
    def import_document(case_id: str, path: str) -> dict[str, Any]:
        """Import a PDF from CASEFILE_MCP_IMPORT_ROOTS into a case."""

        def run() -> Any:
            case(case_id)
            safe_path = allowed_pdf(path, roots)
            with safe_path.open("rb") as source:
                return runtime.ingestion.upload_document(case_id, source, safe_path.name)

        return _result(run, case_id=case_id)

    @server.tool(name="build_index", structured_output=True)
    def build_index(case_id: str) -> dict[str, Any]:
        """Build or update the local evidence index for a case."""

        def run() -> Any:
            case(case_id)
            return runtime.retrieval.index_case(case_id)

        return _result(run, case_id=case_id)

    @server.tool(name="get_index_status", structured_output=True)
    def get_index_status(case_id: str) -> dict[str, Any]:
        """Read local indexing status for a case."""

        def run() -> Any:
            case(case_id)
            return runtime.materials.snapshot(case_id)

        return _result(run, case_id=case_id)

    @server.tool(name="search_evidence", structured_output=True)
    def search_evidence(
        case_id: str, query: str, limit: Annotated[int, Field(ge=1, le=50)] = 10
    ) -> dict[str, Any]:
        """Search indexed evidence, including document, version, page and citation ID."""

        def run() -> Any:
            ready(case_id)
            hits = runtime.retrieval.search_case_evidence(case_id, query, limit)
            return [
                {
                    "evidence_id": hit.evidence_id,
                    "document_id": hit.document_id,
                    "document_version_id": hit.document_version_id,
                    "document_display_name": hit.filename,
                    "page": hit.page_number,
                    "snippet": hit.quote,
                    "citation": f"{hit.filename} 第 {hit.page_number} 页",
                    "score": hit.score,
                    "rank": hit.rank,
                }
                for hit in hits
            ]

        return _result(run, case_id=case_id)

    @server.tool(name="ask_case", structured_output=True)
    def ask_case(case_id: str, question: str) -> dict[str, Any]:
        """Answer with validated case citations. May invoke configured cloud model."""

        def run() -> Any:
            case(case_id)
            runtime.require_model()
            ready(case_id)
            answer = runtime.qa.ask_case(case_id, question)
            data = asdict(answer)
            data["status"] = "insufficient_evidence" if answer.abstained else "answered"
            return data

        return _result(run, case_id=case_id)

    @server.tool(name="submit_answer_feedback", structured_output=True)
    def submit_answer_feedback(
        case_id: str, answer_id: str,
        rating: Literal["correct", "partially_correct", "incorrect", "insufficient_evidence"],
        category: Literal["retrieval", "answer", "citation", "claim", "other"],
        note: str = "",
    ) -> dict[str, Any]:
        """Store feedback locally; it is never automatically used for model training."""
        return _result(
            lambda: runtime.feedback.submit_feedback(case_id, answer_id, rating, category, note),
            case_id=case_id,
        )

    @server.tool(name="get_feedback_summary", structured_output=True)
    def get_feedback_summary(case_id: str) -> dict[str, Any]:
        """Read feedback counts locally; feedback is never automatically used for model training."""
        return _result(
            lambda: runtime.feedback.get_feedback_summary(case_id), case_id=case_id
        )

    @server.tool(name="extract_claims", structured_output=True)
    def extract_claims(case_id: str, answer_id: str) -> dict[str, Any]:
        """Extract validated case claims. May invoke configured cloud model."""

        def run() -> Any:
            case(case_id)
            runtime.require_model()
            return runtime.workspace.extract(case_id, answer_id)

        return _result(run, case_id=case_id)

    @server.tool(name="list_claims", structured_output=True)
    def list_claims(case_id: str) -> dict[str, Any]:
        """List saved claims for a case without model calls."""
        return _result(
            lambda: runtime.data_management.read_analysis(case_id).claims, case_id=case_id
        )

    @server.tool(name="run_contradiction_analysis", structured_output=True)
    def run_contradiction_analysis(case_id: str) -> dict[str, Any]:
        """Analyze case claims. May invoke configured cloud model for semantic comparisons."""

        def run() -> Any:
            case(case_id)
            runtime.require_semantic_model(case_id)
            return runtime.workspace.detect(case_id)

        return _result(run, case_id=case_id)

    @server.tool(name="list_contradictions", structured_output=True)
    def list_contradictions(case_id: str) -> dict[str, Any]:
        """List saved contradiction results for a case without model calls."""
        return _result(
            lambda: runtime.data_management.read_analysis(case_id).contradictions, case_id=case_id
        )

    @server.tool(name="get_timeline", structured_output=True)
    def get_timeline(case_id: str) -> dict[str, Any]:
        """Build a local timeline from saved validated claims."""
        return _result(
            lambda: runtime.data_management.read_analysis(case_id).timeline, case_id=case_id
        )

    @server.tool(name="export_case_analysis", structured_output=True)
    def export_case_analysis(
        case_id: str, format: Literal["json", "markdown"] = "json"
    ) -> dict[str, Any]:
        """Return saved case analysis in JSON or Markdown; writes no caller-selected path."""

        def run() -> Any:
            report = runtime.data_management.read_analysis(case_id)
            return report.model_dump(mode="json") if format == "json" else render_markdown(report)

        return _result(run, case_id=case_id)

    @server.tool(name="get_system_status", structured_output=True)
    def get_system_status(case_id: str | None = None) -> dict[str, Any]:
        """Read redacted health and optional case index status without local paths or secrets."""

        def run() -> Any:
            status = runtime.diagnostics.snapshot()
            result: dict[str, Any] = {
                "version": status.application.version,
                "overall": status.status,
                "database_healthy": status.database.reachable
                and status.database.schema_status == "current",
                "embedding_available": status.embedding.available,
                "ocr_available": status.ocr.dependency_available and status.ocr.model_available,
                "ai_configured": status.qa_provider.configured,
                "ai_enabled": status.qa_provider.enabled,
                "log_available": status.logs.file_logging_available,
            }
            if case_id is not None:
                case(case_id)
                result["index_status"] = runtime.materials.snapshot(case_id).status
            return result

        return _result(run, case_id=case_id)

    # MCP SDK 2.2 builds permissive argument models by default. The advertised
    # schema and server-side validation must both reject unexpected keys.
    for tool in server._tool_manager.list_tools():
        model = tool.fn_metadata.arg_model
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)
        tool.parameters = model.model_json_schema(by_alias=True)

    return server


def main() -> None:
    """Start the MCP protocol loop; stdout is reserved for JSON-RPC."""
    logs: SafeLogging | None = None
    runtime: ApplicationRuntime | None = None
    try:
        settings = Settings()
        logs = SafeLogging(settings.log_dir, settings.log_level, console=False)
        prepare_local_storage(settings)
        runtime = build_application_runtime(settings, logs)
        build_server(runtime).run("stdio")
    except KeyboardInterrupt:
        pass
    except Exception:
        print("CaseFile AI MCP startup failed; check the safe log.", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if runtime is not None:
            runtime.close()
        if logs is not None:
            logs.close()


if __name__ == "__main__":
    main()
