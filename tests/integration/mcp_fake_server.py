"""Subprocess-only deterministic MCP composition for protocol tests."""

import os

from test_cli_workflow import Encoder, FakeLLM

from casefile_ai.application import bootstrap
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.interfaces.mcp.server import main


def embeddings(**values: object) -> LocalSentenceTransformerEmbeddingProvider:
    return LocalSentenceTransformerEmbeddingProvider(**values, encoder=Encoder())  # type: ignore[arg-type]


bootstrap.LocalSentenceTransformerEmbeddingProvider = embeddings  # type: ignore[misc, assignment]
bootstrap.OpenAICompatibleLLMProvider = FakeLLM  # type: ignore[misc, assignment]
FakeLLM.failure = os.environ.get("CASEFILE_MCP_FAKE_FAILURE")
FakeLLM.connection_verified = False  # type: ignore[attr-defined]
main()
