from __future__ import annotations

from typing import Protocol

from nodeskclaw_rpa_engine.workers.schemas import (
    LeaseRenewal,
    LeaseRunCommand,
    WorkerLeaseRenewRequest,
    WorkerLeaseRequest,
)
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient


class RunCommandSource(Protocol):
    async def receive(self, available_slots: int) -> list[LeaseRunCommand]: ...

    async def renew(self, command: LeaseRunCommand) -> LeaseRenewal: ...


class LeaseRunCommandSource:
    def __init__(
        self,
        client: TaskWorkerApiClient,
        *,
        worker_id: str,
        capabilities: list[str],
    ) -> None:
        self._client = client
        self._worker_id = worker_id
        self._capabilities = capabilities

    async def receive(self, available_slots: int) -> list[LeaseRunCommand]:
        if available_slots <= 0:
            return []
        return await self._client.lease(
            WorkerLeaseRequest(
                worker_id=self._worker_id,
                capabilities=self._capabilities,
                limit=available_slots,
            )
        )

    async def renew(self, command: LeaseRunCommand) -> LeaseRenewal:
        return await self._client.renew(
            command.task_id,
            WorkerLeaseRenewRequest(
                worker_id=self._worker_id,
                lease_id=command.lease_id,
            ),
        )
