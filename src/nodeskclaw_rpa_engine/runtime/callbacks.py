from __future__ import annotations

import logging
from typing import Any

from nodeskclaw_rpa_engine.workers.errors import TaskApiError
from nodeskclaw_rpa_engine.workers.schemas import RunEventRequest
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient

logger = logging.getLogger(__name__)


class TaskRuntimeEventSink:
    def __init__(
        self,
        client: TaskWorkerApiClient,
        *,
        run_id: str,
        worker_id: str,
    ) -> None:
        self._client = client
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
        try:
            await self._client.event(
                self._run_id,
                RunEventRequest(
                    worker_id=self._worker_id,
                    type=event_type,
                    level=level,
                    message=message,
                    payload=payload or {},
                ),
            )
        except TaskApiError:
            logger.warning(
                "Runtime event callback failed",
                extra={"runId": self._run_id, "eventType": event_type},
            )
