"""Safe provider failures: never retain requests, responses or exception text."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

ProviderCategory = Literal[
    "authentication_error",
    "permission_error",
    "rate_limited",
    "timeout",
    "unavailable",
    "connection_error",
    "invalid_response",
    "unknown_provider_error",
    "model_not_configured",
    "model_disabled",
]

MESSAGES: dict[ProviderCategory, str] = {
    "authentication_error": "模型凭据无效，请检查 API key。",
    "permission_error": "没有访问该模型的权限，请检查模型配置。",
    "rate_limited": "模型服务请求受限，请稍后重试并检查配额。",
    "timeout": "模型服务响应超时，请稍后重试。",
    "unavailable": "模型服务暂不可用，请稍后重试。",
    "connection_error": "无法连接模型服务，请检查网络和服务地址。",
    "invalid_response": "模型返回格式无效，本次未生成有效结果。",
    "unknown_provider_error": "模型服务发生未知错误，请使用诊断编号排查。",
    "model_not_configured": "模型服务未配置；本地材料处理和检索仍可使用。",
    "model_disabled": "模型调用未启用；仅合成或人工脱敏材料可启用外发。",
}


class ProviderError(RuntimeError):
    def __init__(
        self,
        category: ProviderCategory,
        *,
        http_status: int | None = None,
        retryable: bool = False,
        message: str | None = None,
    ) -> None:
        self.category = category
        self.provider = "openai_compatible"
        self.http_status = http_status
        self.retryable = retryable
        self.diagnostic_id = str(uuid4())
        super().__init__(message or category)

    def safe_fields(self) -> dict[str, str | int | bool | None]:
        return {
            "category": self.category,
            "provider": self.provider,
            "http_status": self.http_status,
            "retryable": self.retryable,
            "diagnostic_id": self.diagnostic_id,
        }


def provider_failure(exception: Exception) -> ProviderError | None:
    """Recover a typed cause through legacy wrappers, without string matching."""
    item: BaseException | None = exception
    seen: set[int] = set()
    while item is not None and id(item) not in seen:
        seen.add(id(item))
        if isinstance(item, ProviderError):
            return item
        item = item.__cause__
    return None
