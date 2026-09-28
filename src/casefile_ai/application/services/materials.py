"""User-facing readiness and safe indexing lifecycle shared by all adapters."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from uuid import uuid4

from casefile_ai.application.material_contracts import (
    IndexInProgressError,
    IndexOperationError,
    IndexStatus,
    MaterialRepository,
    MaterialsStatus,
    MaterialStatus,
)
from casefile_ai.infrastructure.safe_logging import safe_event

LABELS: dict[IndexStatus, str] = {
    "not_indexed": "尚未建立索引",
    "ready": "索引可用",
    "stale": "索引需要更新",
    "processing": "正在建立索引",
    "failed": "上次索引失败",
}
MESSAGES: dict[IndexStatus, str] = {
    "not_indexed": "材料已导入并完成解析 / OCR，请建立索引后再检索或提问。",
    "ready": "当前材料版本的分块、向量与关键词索引均可用。",
    "stale": "已有索引不完整或与当前分块、向量或关键词版本不一致，请更新索引。",
    "processing": "正在建立 / 更新索引，请等待当前操作完成。",
    "failed": "上次建立 / 更新索引失败；已导入材料仍保留，请重试并查看系统状态。",
}


class MaterialsService:
    def __init__(self, repository: MaterialRepository, embedding_version: str) -> None:
        self._repository = repository
        self._embedding_version = embedding_version
        self._active: set[str] = set()
        self._lock = Lock()

    def snapshot(self, case_id: str) -> MaterialsStatus:
        facts = self._repository.facts(case_id, self._embedding_version)
        with self._lock:
            processing = case_id in self._active
        documents: list[MaterialStatus] = []
        for item in facts:
            state: IndexStatus
            if processing:
                state = "processing"
            elif item.attempt == "failed":
                state = "failed"
            elif item.complete:
                state = "ready"
            elif item.has_index:
                state = "stale"
            else:
                state = "not_indexed"
            documents.append(
                MaterialStatus(
                    item.version, state, LABELS[state], MESSAGES[state], item.diagnostic_id
                )
            )
        priorities: list[IndexStatus] = ["failed", "not_indexed", "stale", "processing"]
        reasons = [state for state in priorities if any(item.status == state for item in documents)]
        overall: IndexStatus = reasons[0] if reasons else ("ready" if documents else "not_indexed")
        message = " ".join(MESSAGES[state] for state in reasons) if reasons else MESSAGES[overall]
        if not documents:
            message = "本案尚无材料，请先导入 PDF。"
        return MaterialsStatus(
            case_id,
            documents,
            overall,
            LABELS[overall],
            message,
            "建立索引" if overall == "not_indexed" else "更新索引",
            reasons,
        )

    @contextmanager
    def monitor(self, case_id: str) -> Iterator[None]:
        with self._lock:
            if case_id in self._active:
                raise IndexInProgressError("index_processing")
            self._active.add(case_id)
        try:
            facts = self._repository.facts(case_id, self._embedding_version)
            try:
                yield
            except Exception:
                diagnostic_id = str(uuid4())
                self._repository.record_attempt(facts, "failed", diagnostic_id)
                safe_event("index", "failed", diagnostic_id=diagnostic_id, level=40)
                raise IndexOperationError(diagnostic_id) from None
            else:
                self._repository.record_attempt(facts, "ready", None)
        finally:
            with self._lock:
                self._active.discard(case_id)
