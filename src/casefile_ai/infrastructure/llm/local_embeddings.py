"""Local sentence-transformer embedding adapter."""

from __future__ import annotations

from typing import Protocol, cast

import numpy as np
from numpy.typing import NDArray


class EmbeddingOutputError(RuntimeError):
    """Raised when a local encoder violates the embedding contract."""


class SentenceEncoder(Protocol):
    def encode(
        self,
        sentences: list[str],
        *,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> NDArray[np.float32]: ...


class LocalSentenceTransformerEmbeddingProvider:
    """Lazy local embedding provider suitable for BGE sentence-transformer models."""

    def __init__(
        self,
        *,
        model: str,
        revision: str | None = None,
        device: str = "cpu",
        query_instruction: str = "",
        encoder: SentenceEncoder | None = None,
    ) -> None:
        self._model = model
        self._revision = revision
        self._device = device
        self._query_instruction = query_instruction
        self._encoder = encoder

    @property
    def loaded(self) -> bool:
        return self._encoder is not None

    def close(self) -> None:
        """Release this application's encoder reference; never evict shared caches."""
        self._encoder = None

    @property
    def model_version(self) -> str:
        revision = self._revision or "unversioned"
        return f"{self._model}@{revision}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        query = f"{self._query_instruction}{text}"
        vectors = self._encode([query])
        return vectors[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        encoded = self._get_encoder().encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        vectors = np.asarray(encoded, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(texts) or vectors.shape[1] == 0:
            raise EmbeddingOutputError("embedding shape does not match the input batch")
        if not np.isfinite(vectors).all():
            raise EmbeddingOutputError("embedding output contains non-finite values")
        return cast(list[list[float]], vectors.tolist())

    def _get_encoder(self) -> SentenceEncoder:
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer

            loaded = SentenceTransformer(
                self._model,
                revision=self._revision,
                device=self._device,
            )
            self._encoder = cast(SentenceEncoder, loaded)
        return self._encoder
