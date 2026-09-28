"""Explicit local model preparation; no case access and no cloud model calls."""

import os
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf

from casefile_ai.application.services.system_status import embedding_cached, ocr_models_available
from casefile_ai.config import Settings
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.infrastructure.parsers.pdf import RapidOcrPageReader


def prepare_local_models(settings: Settings, progress: Callable[[str], None]) -> None:
    progress("正在准备本地向量模型（首次可能联网下载，请等待）……")
    with open(os.devnull, "w") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            provider = LocalSentenceTransformerEmbeddingProvider(
                model=settings.embedding_model,
                revision=settings.embedding_revision,
                device=settings.embedding_device,
            )
            try:
                provider.embed_query("合成本地模型准备检查")
            finally:
                provider.close()
    if not embedding_cached(settings):
        raise RuntimeError("embedding_not_prepared")
    progress("正在检查 OCR 模型（首次可能联网下载，请等待）……")
    with TemporaryDirectory() as directory:
        path = Path(directory) / "synthetic-model-check.pdf"
        with pymupdf.open() as document:  # type: ignore[no-untyped-call]
            page = document.new_page(width=240, height=100)
            page.insert_text((15, 40), "LOCAL MODEL CHECK 123", fontsize=12)
            document.save(path)
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
                RapidOcrPageReader().read(document[0])
    if not ocr_models_available():
        raise RuntimeError("ocr_not_prepared")
    progress("本地向量与 OCR 模型已准备并完成合成输入检查。")
