"""A non-network provider preserving existing model service contracts."""

from casefile_ai.infrastructure.llm.errors import ProviderError
from casefile_ai.infrastructure.llm.protocol import ModelResponse


class DisabledLLMProvider:
    def __init__(self, *, configured: bool, model: str | None) -> None:
        self._configured = configured
        self._model = model or "unconfigured"

    @property
    def model_version(self) -> str:
        return self._model

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ModelResponse],
    ) -> ModelResponse:
        raise ProviderError("model_disabled" if self._configured else "model_not_configured")

    def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        raise ProviderError("model_disabled" if self._configured else "model_not_configured")

    def close(self) -> None:
        pass
