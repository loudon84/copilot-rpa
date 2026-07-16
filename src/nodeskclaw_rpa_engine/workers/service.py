from __future__ import annotations

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.db.models import RpaWorkerInstance
from nodeskclaw_rpa_engine.db.session import DatabaseManager
from nodeskclaw_rpa_engine.workers.errors import WorkerError
from nodeskclaw_rpa_engine.workers.repository import SqlAlchemyWorkerRepository
from nodeskclaw_rpa_engine.workers.schemas import (
    WorkerListResponse,
    WorkerResponse,
    WorkerStatus,
)


class WorkerQueryService:
    def __init__(self, settings: Settings, database: DatabaseManager) -> None:
        self._settings = settings
        self._database = database

    async def list_workers(
        self,
        *,
        status: WorkerStatus | None,
        capability: str | None,
        limit: int,
        offset: int,
    ) -> WorkerListResponse:
        async with self._database.session() as session:
            workers = await SqlAlchemyWorkerRepository(session).list_workers(
                capability=capability
            )
        items = [self._response(worker) for worker in workers]
        if status is not None:
            items = [item for item in items if item.status is status]
        total = len(items)
        return WorkerListResponse(
            items=items[offset : offset + limit],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_worker(self, worker_id: str) -> WorkerResponse:
        async with self._database.session() as session:
            worker = await SqlAlchemyWorkerRepository(session).get_worker(worker_id)
        if worker is None:
            raise WorkerError(
                "WORKER_NOT_FOUND",
                "Worker was not found",
                status_code=404,
            )
        return self._response(worker)

    def _response(self, worker: RpaWorkerInstance) -> WorkerResponse:
        return WorkerResponse(
            id=worker.id,
            worker_id=worker.worker_id,
            worker_type=worker.worker_type,
            device_name=worker.device_name,
            status=WorkerResponse.effective_status(
                worker.status,
                worker.last_heartbeat_at,
                self._settings.worker_offline_threshold_seconds,
            ),
            capabilities=list(worker.capabilities),
            tags=list(worker.tags),
            app_version=worker.app_version,
            agent_version=worker.agent_version,
            os=worker.os,
            max_concurrent_runs=worker.max_concurrent_runs,
            current_task_count=worker.current_task_count,
            browser_count=worker.browser_count,
            registered_at=worker.registered_at,
            last_heartbeat_at=worker.last_heartbeat_at,
            updated_at=worker.updated_at,
        )
