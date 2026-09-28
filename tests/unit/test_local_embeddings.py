from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from casefile_ai.infrastructure.llm.local_embeddings import (
    EmbeddingOutputError,
    LocalSentenceTransformerEmbeddingProvider,
)


class FakeEncoder:
    def __init__(self, output: NDArray[np.float32]) -> None:
        self.output = output
        self.calls: list[list[str]] = []

    def encode(
        self,
        sentences: list[str],
        *,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> NDArray[np.float32]:
        assert normalize_embeddings is True
        assert convert_to_numpy is True
        assert show_progress_bar is False
        self.calls.append(sentences)
        return self.output


def test_documents_are_embedded_without_query_instruction() -> None:
    encoder = FakeEncoder(np.array([[0.6, 0.8]], dtype=np.float32))
    provider = LocalSentenceTransformerEmbeddingProvider(
        model="local-model",
        revision="abc123",
        query_instruction="检索：",
        encoder=encoder,
    )

    vectors = provider.embed_documents(["证据原文"])

    assert encoder.calls == [["证据原文"]]
    assert vectors[0] == pytest.approx([0.6, 0.8])
    assert provider.model_version == "local-model@abc123"


def test_query_instruction_is_applied_only_to_query() -> None:
    encoder = FakeEncoder(np.array([[1.0, 0.0]], dtype=np.float32))
    provider = LocalSentenceTransformerEmbeddingProvider(
        model="local-model",
        query_instruction="检索：",
        encoder=encoder,
    )

    provider.embed_query("付款时间")

    assert encoder.calls == [["检索：付款时间"]]


def test_empty_batch_does_not_load_or_call_encoder() -> None:
    encoder = FakeEncoder(np.empty((0, 2), dtype=np.float32))
    provider = LocalSentenceTransformerEmbeddingProvider(model="local-model", encoder=encoder)

    assert provider.embed_documents([]) == []
    assert encoder.calls == []


def test_invalid_embedding_shape_is_rejected() -> None:
    encoder = FakeEncoder(np.array([1.0, 0.0], dtype=np.float32))
    provider = LocalSentenceTransformerEmbeddingProvider(model="local-model", encoder=encoder)

    with pytest.raises(EmbeddingOutputError, match="shape"):
        provider.embed_documents(["第一段"])
