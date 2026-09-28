"""Narrow model protocols consumed by application services."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

ModelResponse = TypeVar("ModelResponse", bound=BaseModel)


class LLMProvider(Protocol):
    """Provider-neutral text and validated structured generation contract."""

    @property
    def model_version(self) -> str: ...

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str: ...

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ModelResponse],
    ) -> ModelResponse: ...


class EmbeddingProvider(Protocol):
    """Provider-neutral document/query embedding contract."""

    @property
    def model_version(self) -> str: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
