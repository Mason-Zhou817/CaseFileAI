"""Provider classifications must survive without retaining sensitive responses."""

import json
from pathlib import Path

import httpx
import pytest

from casefile_ai.infrastructure.llm.errors import ProviderError
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider
from casefile_ai.infrastructure.safe_logging import SafeLogging, safe_event

PRIVATE = "synthetic-key-and-evidence-do-not-expose-7812"


def provider(transport: httpx.MockTransport) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        base_url="https://mock.invalid/v1",
        api_key=PRIVATE,
        model="fixture",
        client=httpx.Client(transport=transport),
    )


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, "authentication_error", False),
        (403, "permission_error", False),
        (429, "rate_limited", True),
        (500, "unavailable", True),
        (502, "unavailable", True),
        (503, "unavailable", True),
        (400, "unknown_provider_error", False),
    ],
)
def test_http_mapping_has_only_safe_fields(status: int, category: str, retryable: bool) -> None:
    adapter = provider(httpx.MockTransport(lambda _: httpx.Response(status, text=PRIVATE)))
    try:
        with pytest.raises(ProviderError) as captured:
            adapter.generate_text(system_prompt=PRIVATE, user_prompt=PRIVATE)
        failure = captured.value
        assert failure.category == category and failure.http_status == status
        assert failure.retryable is retryable
        assert set(failure.safe_fields()) == {
            "category",
            "provider",
            "http_status",
            "retryable",
            "diagnostic_id",
        }
        assert PRIVATE not in str(failure)
        assert PRIVATE not in json.dumps(failure.safe_fields())
        assert failure.__cause__ is None and failure.__context__ is None
        assert not adapter.connection_verified
    finally:
        adapter.close()


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("timeout", "timeout"),
        ("connection", "connection_error"),
        ("unknown", "unknown_provider_error"),
    ],
)
def test_transport_exception_content_is_discarded(kind: str, category: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if kind == "timeout":
            raise httpx.ReadTimeout(PRIVATE, request=request)
        if kind == "connection":
            raise httpx.ConnectError(PRIVATE, request=request)
        raise RuntimeError(PRIVATE)

    adapter = provider(httpx.MockTransport(handler))
    try:
        with pytest.raises(ProviderError) as captured:
            adapter.generate_text(system_prompt=PRIVATE, user_prompt=PRIVATE)
        assert captured.value.category == category
        assert captured.value.__cause__ is None and captured.value.__context__ is None
        assert PRIVATE not in repr(captured.value)
    finally:
        adapter.close()


def test_invalid_envelope_is_safe_and_success_sets_verified() -> None:
    responses = iter(
        [
            httpx.Response(200, text=PRIVATE),
            httpx.Response(200, json={"choices": [{"message": {"content": "fixture"}}]}),
        ]
    )
    adapter = provider(httpx.MockTransport(lambda _: next(responses)))
    try:
        with pytest.raises(ProviderError) as captured:
            adapter.generate_text(system_prompt=PRIVATE, user_prompt=PRIVATE)
        assert captured.value.category == "invalid_response"
        assert PRIVATE not in str(captured.value)
        assert not adapter.connection_verified
        assert adapter.generate_text(system_prompt="fixture", user_prompt="fixture") == "fixture"
        assert adapter.connection_verified
    finally:
        adapter.close()


def test_provider_failure_logged_without_remote_body(tmp_path: Path) -> None:
    manager = SafeLogging(tmp_path, "INFO")
    adapter = provider(httpx.MockTransport(lambda _: httpx.Response(401, text=PRIVATE)))
    try:
        with pytest.raises(ProviderError) as captured:
            adapter.generate_text(system_prompt=PRIVATE, user_prompt=PRIVATE)
        safe_event("provider", captured.value.category, diagnostic_id=captured.value.diagnostic_id)
    finally:
        adapter.close()
        manager.close()
    text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "authentication_error" in text
    assert PRIVATE not in text
