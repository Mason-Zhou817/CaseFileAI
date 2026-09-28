"""Process logging with an allowlist, bounded files and a safe stderr fallback."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from casefile_ai.application.data_messages import DATA_MESSAGES

OPERATIONS = {
    "startup",
    "shutdown",
    "api",
    "provider",
    "diagnostics",
    "logging",
    "runtime",
    "data_management",
    "index",
}
CATEGORIES = {
    "ok",
    "failed",
    "degraded",
    "unhandled_error",
    "file_logging_unavailable",
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
    "third_party_event",
}
_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CATEGORIES |= set(DATA_MESSAGES)


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        operation = getattr(record, "operation", "runtime")
        category = getattr(record, "category", "third_party_event")
        fields: dict[str, str | int | float] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": logging.getLevelName(record.levelno),
            "operation": operation
            if isinstance(operation, str) and operation in OPERATIONS
            else "runtime",
            "category": category
            if isinstance(category, str) and category in CATEGORIES
            else "third_party_event",
        }
        for name in ("diagnostic_id", "case_id", "document_id"):
            value = getattr(record, name, None)
            if isinstance(value, str) and _ID.fullmatch(value):
                fields[name] = value
        duration = getattr(record, "duration_ms", None)
        if isinstance(duration, (int, float)) and 0 <= duration < float("inf"):
            fields["duration_ms"] = duration
        # Never format message, args, exception/stack text or arbitrary extra fields.
        return json.dumps(fields, ensure_ascii=False, allow_nan=False)


class SafeRotatingHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        sys.stderr.write('{"operation":"logging","category":"file_logging_unavailable"}\n')


def safe_event(
    operation: str,
    category: str = "ok",
    *,
    diagnostic_id: str | None = None,
    duration_ms: float | None = None,
    level: int = logging.INFO,
) -> None:
    logging.getLogger("casefile_ai.safe").log(
        level,
        "event",
        extra={
            "operation": operation,
            "category": category,
            "diagnostic_id": diagnostic_id,
            "duration_ms": duration_ms,
        },
    )


class SafeLogging:
    """Own and restore process-wide handlers and record factory for a runtime."""

    def __init__(self, directory: Path, level: str, *, console: bool = True) -> None:
        self.directory = directory
        self.file_logging_available = False
        self._closed = False
        self._root = logging.getLogger()
        self._old_handlers = self._root.handlers[:]
        self._old_level = self._root.level
        self._old_factory = logging.getLogRecordFactory()
        logging.getLogger("casefile_ai.safe")
        self._loggers: list[tuple[logging.Logger, list[logging.Handler], bool, bool, int]] = []
        for item in logging.Logger.manager.loggerDict.values():
            if isinstance(item, logging.Logger):
                self._loggers.append(
                    (
                        item,
                        item.handlers[:],
                        item.propagate,
                        item.disabled,
                        item.level,
                    )
                )
                item.handlers = []
                item.propagate = True
                item.disabled = False
        logging.getLogger("casefile_ai.safe").setLevel(level)

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = self._old_factory(*args, **kwargs)
            # Sanitize before records reach ANY handler, including dependency handlers.
            record.msg = "runtime_event"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            return record

        logging.setLogRecordFactory(factory)
        self._handlers: list[logging.Handler] = (
            [logging.StreamHandler(sys.stderr)] if console else []
        )
        console_count = len(self._handlers)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for filename, minimum in (("app.log", logging.DEBUG), ("error.log", logging.ERROR)):
                file_handler = SafeRotatingHandler(
                    directory / filename,
                    maxBytes=2 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
                file_handler.setLevel(minimum)
                self._handlers.append(file_handler)
            self.file_logging_available = True
        except OSError:
            for handler in self._handlers[console_count:]:
                handler.close()
            self._handlers = [logging.StreamHandler(sys.stderr)]
        for handler in self._handlers:
            handler.setFormatter(SafeFormatter())
        self._root.handlers = self._handlers
        self._root.setLevel(level)
        if not self.file_logging_available:
            safe_event("logging", "file_logging_unavailable", level=logging.WARNING)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handler in self._handlers:
            try:
                handler.close()
            except OSError:
                pass
        logging.setLogRecordFactory(self._old_factory)
        self._root.handlers = self._old_handlers
        self._root.setLevel(self._old_level)
        for logger, handlers, propagate, disabled, old_level in self._loggers:
            logger.handlers = handlers
            logger.propagate = propagate
            logger.disabled = disabled
            logger.setLevel(old_level)
