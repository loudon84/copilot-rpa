from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from nodeskclaw_rpa_engine.core.logging import redact_sensitive
from nodeskclaw_rpa_engine.runtime.errors import RpaFatalError


class RuntimeEventSink(Protocol):
    async def emit(
        self,
        event_type: str,
        *,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None: ...


class ArtifactApi(Protocol):
    async def screenshot(
        self,
        name: str,
        *,
        full_page: bool = True,
        step_id: str | None = None,
    ) -> Any: ...

    async def save_download(
        self,
        download: Any,
        name: str | None = None,
        *,
        step_id: str | None = None,
    ) -> Any: ...


class CredentialResolver(Protocol):
    async def resolve(
        self,
        credential_ref: str | None,
        *,
        tenant_id: str | None,
        portal_account_id: str | None,
    ) -> Mapping[str, Any]: ...


class DisabledCredentialResolver:
    async def resolve(
        self,
        credential_ref: str | None,
        *,
        tenant_id: str | None,
        portal_account_id: str | None,
    ) -> Mapping[str, Any]:
        del tenant_id, portal_account_id
        if credential_ref is not None:
            raise RpaFatalError(
                "CREDENTIAL_RESOLVER_UNAVAILABLE",
                "Credential resolution is not configured",
            )
        return MappingProxyType({})


class RunLogger:
    def __init__(self, sink: RuntimeEventSink) -> None:
        self._sink = sink

    async def info(
        self,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._sink.emit(
            "FLOW_LOG",
            level="INFO",
            message=str(redact_sensitive(message)),
            payload=self._safe_payload(payload),
        )

    async def warning(
        self,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._sink.emit(
            "FLOW_LOG",
            level="WARNING",
            message=str(redact_sensitive(message)),
            payload=self._safe_payload(payload),
        )

    async def error(
        self,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._sink.emit(
            "FLOW_LOG",
            level="ERROR",
            message=str(redact_sensitive(message)),
            payload=self._safe_payload(payload),
        )

    @staticmethod
    def _safe_payload(
        payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if payload is None:
            return None
        sanitized = redact_sensitive(payload)
        return sanitized if isinstance(sanitized, dict) else {}


class RunEvents:
    def __init__(self, sink: RuntimeEventSink) -> None:
        self._sink = sink

    async def emit(
        self,
        event_type: str,
        *,
        level: str = "INFO",
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._sink.emit(
            event_type,
            level=level,
            message=str(redact_sensitive(message or event_type)),
            payload=RunLogger._safe_payload(payload),
        )


@dataclass(frozen=True, slots=True)
class RunContext:
    input: Mapping[str, Any]
    credentials: Mapping[str, Any]
    page: Any
    portal_url: str
    selectors: Mapping[str, Any]
    artifacts: ArtifactApi
    log: RunLogger
    events: RunEvents
    config: Mapping[str, Any]

    @classmethod
    def create(
        cls,
        *,
        input_data: Mapping[str, Any],
        credentials: Mapping[str, Any],
        page: Any,
        portal_url: str,
        selectors: Mapping[str, Any],
        artifacts: ArtifactApi,
        event_sink: RuntimeEventSink,
        safe_config: Mapping[str, Any],
    ) -> RunContext:
        return cls(
            input=MappingProxyType(copy.deepcopy(dict(input_data))),
            credentials=MappingProxyType(copy.deepcopy(dict(credentials))),
            page=page,
            portal_url=portal_url,
            selectors=MappingProxyType(copy.deepcopy(dict(selectors))),
            artifacts=artifacts,
            log=RunLogger(event_sink),
            events=RunEvents(event_sink),
            config=MappingProxyType(copy.deepcopy(dict(safe_config))),
        )
