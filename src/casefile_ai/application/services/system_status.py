"""Local diagnostics without model initialization, downloads or paid requests."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
import tempfile
from pathlib import Path

from sqlalchemy import Engine

from casefile_ai import __version__
from casefile_ai.application.system_contracts import (
    ApplicationStatus,
    DatabaseStatus,
    DirectoryStatus,
    EmbeddingStatus,
    LogsStatus,
    OcrStatus,
    ProviderStatus,
    SystemStatus,
)
from casefile_ai.config import SOURCE_ROOT, Settings, configuration_file
from casefile_ai.infrastructure.db.migration import expected_revision
from casefile_ai.infrastructure.db.models import Base
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider
from casefile_ai.infrastructure.safe_logging import SafeLogging


def dependencies_available(*modules: str) -> bool:
    try:
        return all(importlib.util.find_spec(module) is not None for module in modules)
    except (ImportError, ValueError):
        return False


def directory_status(path: Path) -> DirectoryStatus:
    try:
        exists = path.is_dir()
    except OSError:
        exists = False
    writable = False
    if exists:
        try:
            # A real, empty write probe checks ACLs; os.access alone is insufficient.
            with tempfile.TemporaryFile(dir=path):
                writable = True
        except OSError:
            pass
    return DirectoryStatus(path=str(path), exists=exists, writable=writable)


def embedding_cached(settings: Settings) -> bool:
    try:
        path = Path(settings.embedding_model)
        if not path.is_dir():
            home = Path(
                os.environ.get(
                    "HF_HOME",
                    str(
                        Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
                        / "huggingface"
                    ),
                )
            )
            cache = Path(
                os.environ.get(
                    "SENTENCE_TRANSFORMERS_HOME",
                    os.environ.get(
                        "HF_HUB_CACHE",
                        str(home / "hub"),
                    ),
                )
            )
            repository = cache / ("models--" + settings.embedding_model.replace("/", "--"))
            revision = settings.embedding_revision
            if not revision or not (repository / "snapshots" / revision).is_dir():
                ref = repository / "refs" / (revision or "main")
                revision = ref.read_text(encoding="utf-8").strip()
            path = repository / "snapshots" / revision
        required = ("config.json", "modules.json", "tokenizer_config.json")
        return all((path / name).is_file() for name in required) and any(
            (path / name).is_file() and (path / name).stat().st_size > 0
            for name in ("model.safetensors", "pytorch_model.bin")
        )
    except (OSError, ValueError):
        return False


def ocr_models_available() -> bool:
    """Read installed RapidOCR manifests, without importing/constructing RapidOCR."""
    try:
        import yaml  # type: ignore[import-untyped]

        package = Path(str(importlib.metadata.distribution("rapidocr").locate_file("rapidocr")))
        config = yaml.safe_load((package / "config.yaml").read_text(encoding="utf-8"))
        models = yaml.safe_load((package / "default_models.yaml").read_text(encoding="utf-8"))
        root = Path(config["Global"].get("model_root_dir") or package / "models")
        for section in ("Det", "Cls", "Rec"):
            cfg = config[section]
            if cfg.get("model_path"):
                path = Path(cfg["model_path"])
            else:
                entries = models[cfg["engine_type"]][cfg["ocr_version"]][cfg["task_type"]]
                lang = "multi" if cfg["ocr_version"] == "PP-OCRv6" else cfg["lang_type"]
                entry = next(
                    value
                    for key, value in entries.items()
                    if key.startswith(lang) and cfg["model_type"] in key
                )
                path = root / str(entry["model_dir"]).rsplit("/", 1)[-1]
            if not path.is_file() or path.stat().st_size == 0:
                return False
        return True
    except Exception:
        return False


class SystemStatusService:
    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        *,
        embeddings: LocalSentenceTransformerEmbeddingProvider | None = None,
        provider: OpenAICompatibleLLMProvider | None = None,
        logs: SafeLogging | None = None,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._embeddings = embeddings
        self._provider = provider
        self._logs = logs

    def _database(self) -> DatabaseStatus:
        path = self._settings.database_path
        result = DatabaseStatus(
            configured=True,
            reachable=False,
            schema_status="unavailable",
            path=str(path) if path is not None else None,
            revision=None,
            expected_revision=None,
        )
        try:
            result.expected_revision = expected_revision(self._settings)
        except Exception:
            pass
        try:
            if path is not None and not path.is_file():
                return result
            with self._engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
                result.reachable = True
                table = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                ).first()
                if table is None:
                    result.schema_status = "missing"
                else:
                    revisions = list(
                        connection.exec_driver_sql("SELECT version_num FROM alembic_version")
                    )
                    value = str(revisions[0][0]) if len(revisions) == 1 else ""
                    result.revision = value if re.fullmatch(r"\d{8}_\d{4}", value) else None
                    result.schema_status = (
                        "unknown"
                        if result.expected_revision is None
                        else "current"
                        if value == result.expected_revision
                        else "outdated"
                    )
                    if result.schema_status == "current":
                        tables = {
                            str(row[0])
                            for row in connection.exec_driver_sql(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )
                        }
                        if not (set(Base.metadata.tables) | {"chunk_fts"}).issubset(tables):
                            result.schema_status = "missing"
        except Exception:
            result.reachable = False
            result.schema_status = "unavailable"
        return result

    def snapshot(self) -> SystemStatus:
        settings = self._settings
        database = self._database()
        directory = directory_status(settings.data_dir)
        loaded = self._embeddings.loaded if self._embeddings is not None else False
        cached = embedding_cached(settings)
        embedding = EmbeddingStatus(
            configured=bool(settings.embedding_model),
            model=settings.embedding_model,
            revision=settings.embedding_revision,
            dependency_available=dependencies_available("sentence_transformers", "torch"),
            model_cached=cached,
            available=cached or loaded,
            loaded=loaded,
        )
        ocr = OcrStatus(
            dependency_available=dependencies_available("rapidocr", "onnxruntime"),
            model_available=ocr_models_available(),
        )
        local_ready = (
            database.reachable
            and database.schema_status == "current"
            and directory.writable
            and embedding.dependency_available
            and embedding.available
            and ocr.dependency_available
            and ocr.model_available
        )
        notices: list[str] = []
        old_data = SOURCE_ROOT / "data"
        try:
            if (old_data / "casefile-ai.db").is_file() and old_data.resolve() != settings.data_dir:
                notices.append("检测到项目旧数据目录；未自动迁移。请明确配置旧路径或另行安排迁移。")
        except OSError:
            pass
        if not embedding.available:
            notices.append("本地检索模型尚未准备；首次建立索引或向量检索可能下载模型。")
        if not ocr.model_available:
            notices.append("OCR 模型尚未准备；首次扫描件导入可能下载模型。")
        if not settings.llm_configured:
            notices.append("模型服务未配置；本地材料处理和检索不受云配置限制。")
        elif not settings.llm_enabled:
            notices.append("模型外发未启用；系统不会自动脱敏，请仅使用合成或人工脱敏材料。")
        return SystemStatus(
            status="ready" if local_ready else "degraded",
            configuration_file=str(configuration_file()),
            application=ApplicationStatus(version=__version__),
            database=database,
            data_directory=directory,
            embedding=embedding,
            ocr=ocr,
            qa_provider=ProviderStatus(
                configured=settings.llm_configured,
                enabled=settings.llm_enabled,
                connection_verified=self._provider.connection_verified if self._provider else False,
            ),
            logs=LogsStatus(
                directory=str(settings.log_dir),
                file_logging_available=(
                    self._logs.file_logging_available if self._logs is not None else False
                ),
            ),
            notices=notices,
        )
