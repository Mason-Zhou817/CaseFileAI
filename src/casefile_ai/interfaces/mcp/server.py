"""MCP v2 stdio adapter over the shared application service contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NoReturn

from mcp.server import MCPServer

from casefile_ai import __version__
from casefile_ai.application.contracts import (
    CaseService,
    ConflictService,
    IngestionService,
    QAService,
    RetrievalService,
)


@dataclass(frozen=True, slots=True)
class McpServices:
    cases: CaseService
    ingestion: IngestionService
    retrieval: RetrievalService
    qa: QAService
    conflicts: ConflictService


def build_server(services: McpServices) -> MCPServer[None]:
    """Build the six-tool MVP server without duplicating business logic."""

    server: MCPServer[None] = MCPServer(
        name="casefile-ai",
        title="CaseFile AI",
        description="Source-traceable synthetic/de-identified case-file analysis",
        version=__version__,
    )

    @server.tool(name="list_cases", structured_output=True)
    def list_cases() -> dict[str, object]:
        return {"cases": [asdict(case) for case in services.cases.list_cases()]}

    @server.tool(name="import_document", structured_output=True)
    def import_document(case_id: str, path: str) -> dict[str, object]:
        document = services.ingestion.import_document(case_id, Path(path))
        return asdict(document)

    @server.tool(name="search_case_evidence", structured_output=True)
    def search_case_evidence(
        case_id: str, query: str, limit: int = 10
    ) -> dict[str, object]:
        hits = services.retrieval.search_case_evidence(case_id, query, limit)
        return {"hits": [asdict(hit) for hit in hits]}

    @server.tool(name="get_document_page", structured_output=True)
    def get_document_page(document_id: str, page_number: int) -> dict[str, object]:
        page = services.ingestion.get_document_page(document_id, page_number)
        return asdict(page)

    @server.tool(name="detect_conflicts", structured_output=True)
    def detect_conflicts(case_id: str) -> dict[str, object]:
        conflicts = services.conflicts.detect_conflicts(case_id)
        return {"conflicts": [asdict(conflict) for conflict in conflicts]}

    @server.tool(name="ask_case", structured_output=True)
    def ask_case(case_id: str, question: str) -> dict[str, object]:
        answer = services.qa.ask_case(case_id, question)
        return asdict(answer)

    return server


def main() -> NoReturn:
    """The tested development adapter is not wired for the current Windows MVP."""

    raise SystemExit(
        "MCP adapter contracts are available for development; the production entry "
        "is not wired and is not part of the current Windows MVP delivery."
    )
