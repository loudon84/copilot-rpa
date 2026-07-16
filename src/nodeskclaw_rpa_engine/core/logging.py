from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

from nodeskclaw_rpa_engine.core.config import Settings

_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_worker_id: ContextVar[str | None] = ContextVar("worker_id", default=None)
_flow_version_id: ContextVar[str | None] = ContextVar(
    "flow_version_id", default=None
)

_SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|token|authorization|credential|database_url|dsn|"
    r"access_key|private_key",
    re.IGNORECASE,
)
_URL_CREDENTIALS = re.compile(r"(://[^:/@\s]+:)([^@\s]+)(@)")
_BEARER_TOKEN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_SIGNED_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:x-amz-signature|x-amz-credential|x-amz-security-token|"
    r"signature|access_token|token)=)[^&\s]+"
)
_INLINE_SECRET = re.compile(
    r"(?i)\b(password|passwd|secret|token|access_key|credential)="
    r"([^&\s,;]+)"
)

_STANDARD_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


def _redact_string(value: str) -> str:
    value = _URL_CREDENTIALS.sub(r"\1***\3", value)
    value = _BEARER_TOKEN.sub(r"\1***", value)
    value = _SIGNED_QUERY_VALUE.sub(r"\1***", value)
    return _INLINE_SECRET.sub(r"\1=***", value)


def redact_sensitive(value: Any, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "***"
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_sensitive(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_sensitive(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value))


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
            "runId": _run_id.get(),
            "workerId": _worker_id.get(),
            "flowVersionId": _flow_version_id.get(),
        }
        extras = {
            key: redact_sensitive(value, key)
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_")
        }
        if extras:
            payload["fields"] = extras
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(settings: Settings, stream: TextIO | None = None) -> None:
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(StructuredJsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)


@contextmanager
def bind_log_context(
    *,
    run_id: str | None = None,
    worker_id: str | None = None,
    flow_version_id: str | None = None,
) -> Iterator[None]:
    tokens = (
        (_run_id, _run_id.set(run_id)),
        (_worker_id, _worker_id.set(worker_id)),
        (_flow_version_id, _flow_version_id.set(flow_version_id)),
    )
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)
