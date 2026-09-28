"""OpenAI-compatible chat adapter with validated structured output."""

from __future__ import annotations

import json
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from casefile_ai.infrastructure.llm.errors import ProviderCategory, ProviderError

ModelResponse = TypeVar("ModelResponse", bound=BaseModel)
StructuredOutputMode = Literal["json_schema", "json_object"]


class ModelProtocolError(ProviderError):
    """Raised when a configured model endpoint violates the expected contract."""

    def __init__(self, message: str) -> None:
        super().__init__("invalid_response", message=message)


class OpenAICompatibleLLMProvider:
    """LLM-only adapter; embeddings deliberately use a separate provider."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        structured_output_mode: StructuredOutputMode = "json_schema",
        max_validation_attempts: int = 3,
        timeout_seconds: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        if max_validation_attempts < 1:
            raise ValueError("max_validation_attempts must be at least 1")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._structured_output_mode = structured_output_mode
        self._max_validation_attempts = max_validation_attempts
        self.connection_verified = False
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @property
    def model_version(self) -> str:
        return self._model

    def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        response = self._post_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        result = self._extract_content(response)
        self.connection_verified = True
        return result

    def close(self) -> None:
        self._client.close()

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ModelResponse],
    ) -> ModelResponse:
        schema = response_model.model_json_schema()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._structured_system_prompt(system_prompt, schema)},
            {"role": "user", "content": user_prompt},
        ]
        for attempt in range(1, self._max_validation_attempts + 1):
            response = self._post_chat(
                messages=messages,
                response_format=self._response_format(response_model, schema),
            )
            try:
                content = self._extract_content(response)
                parsed = json.loads(content)
                result = response_model.model_validate(parsed)
                self.connection_verified = True
                return result
            except (json.JSONDecodeError, ValidationError, ModelProtocolError):
                if attempt < self._max_validation_attempts:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The previous response did not match the required JSON structure. "
                                "Return one corrected JSON object only."
                            ),
                        }
                    )

        raise ModelProtocolError(
            f"structured response failed validation after {self._max_validation_attempts} attempts"
        ) from None

    def _post_chat(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> httpx.Response:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        error: ProviderError | None = None
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
            )
        except httpx.TimeoutException:
            error = ProviderError("timeout", retryable=True)
        except httpx.RequestError:
            error = ProviderError("connection_error", retryable=True)
        except Exception:
            error = ProviderError("unknown_provider_error")
        if error is not None:
            # Raise outside the except block: no raw network exception is retained.
            raise error from None
        if not 200 <= response.status_code < 300:
            status = response.status_code
            categories: dict[int, ProviderCategory] = {
                401: "authentication_error",
                403: "permission_error",
                429: "rate_limited",
            }
            category = categories.get(
                status,
                "unavailable" if 500 <= status <= 599 else "unknown_provider_error",
            )
            raise ProviderError(
                category,
                http_status=status,
                retryable=status == 429 or 500 <= status <= 599,
            ) from None
        return response

    @staticmethod
    def _extract_content(response: httpx.Response) -> str:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        else:
            if isinstance(content, str) and content.strip():
                return content
        raise ModelProtocolError("invalid chat response envelope") from None

    def _structured_system_prompt(self, prompt: str, schema: dict[str, Any]) -> str:
        if self._structured_output_mode == "json_schema":
            return prompt
        serialized_schema = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        return f"{prompt}\nReturn one JSON object matching this JSON Schema: {serialized_schema}"

    def _response_format(
        self,
        response_model: type[BaseModel],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        if self._structured_output_mode == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__.lower(),
                "strict": True,
                "schema": schema,
            },
        }
