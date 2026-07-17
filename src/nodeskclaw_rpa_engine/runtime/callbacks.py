from __future__ import annotations

from typing import Any

from nodeskclaw_rpa_engine.workers.outbox import CallbackOutboxService
from nodeskclaw_rpa_engine.workers.schemas import RunEventRequest


class TaskRuntimeEventSink:
    def __init__(
        self,
        outbox: CallbackOutboxService | None,
        *,
        lease_id: str,
        run_id: str,
        worker_id: str,
    ) -> None:
        self._outbox = outbox
        self._lease_id = lease_id
        self._run_id = run_id
        self._worker_id = worker_id

    async def emit(
        self,
        event_type: str,
        *,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._outbox is None:
            raise RuntimeError("Runtime callback outbox is unavailable")
        await self._outbox.enqueue_event_for_lease(
            lease_id=self._lease_id,
            run_id=self._run_id,
            request=RunEventRequest(
                worker_id=self._worker_id,
                type=event_type,
                level=level,
                message=message,
                payload=payload or {},
            ),
        )
