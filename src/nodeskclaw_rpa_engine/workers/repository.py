from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nodeskclaw_rpa_engine.db.models import (
    RpaExecutionAttempt,
    RpaWorkerInstance,
)
from nodeskclaw_rpa_engine.workers.schemas import AttemptStatus, WorkerStatus


class SqlAlchemyWorkerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_workers(
        self,
        *,
        capability: str | None = None,
    ) -> Sequence[RpaWorkerInstance]:
        statement = select(RpaWorkerInstance)
        if capability is not None:
            statement = statement.where(
                RpaWorkerInstance.capabilities.contains([capability])
            )
        statement = statement.order_by(
            RpaWorkerInstance.updated_at.desc(),
            RpaWorkerInstance.worker_id,
        )
        return (await self._session.execute(statement)).scalars().all()

    async def get_worker(
        self,
        worker_id: str,
        *,
        for_update: bool = False,
    ) -> RpaWorkerInstance | None:
        statement = select(RpaWorkerInstance).where(
            RpaWorkerInstance.worker_id == worker_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def upsert_worker(
        self,
        *,
        worker_id: str,
        worker_type: str,
        device_name: str,
        status: WorkerStatus,
        capabilities: list[str],
        tags: list[str],
        app_version: str,
        agent_version: str | None,
        os: str | None,
        max_concurrent_runs: int,
    ) -> RpaWorkerInstance:
        now = datetime.now(UTC)
        worker = await self.get_worker(worker_id, for_update=True)
        if worker is None:
            worker = RpaWorkerInstance(
                worker_id=worker_id,
                worker_type=worker_type,
                device_name=device_name,
                status=status.value,
                capabilities=capabilities,
                tags=tags,
                app_version=app_version,
                agent_version=agent_version,
                os=os,
                max_concurrent_runs=max_concurrent_runs,
                current_task_count=0,
                browser_count=0,
                metadata_={"heartbeatCount": 0},
                registered_at=now,
                last_heartbeat_at=now,
                updated_at=now,
            )
            self._session.add(worker)
        else:
            worker.worker_type = worker_type
            worker.device_name = device_name
            worker.status = status.value
            worker.capabilities = capabilities
            worker.tags = tags
            worker.app_version = app_version
            worker.agent_version = agent_version
            worker.os = os
            worker.max_concurrent_runs = max_concurrent_runs
            worker.current_task_count = min(
                worker.current_task_count,
                max_concurrent_runs,
            )
            worker.last_heartbeat_at = now
            worker.updated_at = now
        await self._session.flush()
        await self._session.refresh(worker)
        return worker

    async def heartbeat(
        self,
        worker_id: str,
        *,
        current_task_count: int,
    ) -> RpaWorkerInstance:
        worker = await self.get_worker(worker_id, for_update=True)
        if worker is None:
            raise LookupError("Worker is not registered")
        now = datetime.now(UTC)
        metadata = dict(worker.metadata_)
        metadata["heartbeatCount"] = int(metadata.get("heartbeatCount", 0)) + 1
        worker.metadata_ = metadata
        worker.current_task_count = current_task_count
        worker.status = (
            WorkerStatus.BUSY.value
            if current_task_count > 0
            else WorkerStatus.ONLINE.value
        )
        worker.last_heartbeat_at = now
        worker.updated_at = now
        await self._session.flush()
        return worker

    async def set_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        *,
        current_task_count: int | None = None,
    ) -> RpaWorkerInstance:
        worker = await self.get_worker(worker_id, for_update=True)
        if worker is None:
            raise LookupError("Worker is not registered")
        worker.status = status.value
        if current_task_count is not None:
            worker.current_task_count = current_task_count
        worker.updated_at = datetime.now(UTC)
        await self._session.flush()
        return worker


class SqlAlchemyAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_lease_id(self, lease_id: str) -> RpaExecutionAttempt | None:
        statement = select(RpaExecutionAttempt).where(
            RpaExecutionAttempt.lease_id == lease_id
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_id(
        self,
        attempt_id: UUID,
        *,
        for_update: bool = False,
    ) -> RpaExecutionAttempt | None:
        statement = select(RpaExecutionAttempt).where(
            RpaExecutionAttempt.id == attempt_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def create_lease_attempt(
        self,
        *,
        lease_id: str,
        task_id: str,
        run_id: str,
        workflow_binding_id: str | None,
        portal_account_id: str | None,
        worker_instance_id: UUID,
        worker_id: str,
        flow_version_id: UUID,
        rpa_flow_id: str,
        rpa_flow_version: str,
        package_checksum: str,
        input_snapshot: dict[str, object],
        browser_session_snapshot: dict[str, object],
    ) -> tuple[RpaExecutionAttempt, bool]:
        existing = await self.get_by_lease_id(lease_id)
        if existing is not None:
            return existing, False

        # Serialize attempt numbering for a run without adding a new table/sequence.
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:run_id, 0))"),
            {"run_id": run_id},
        )
        existing = await self.get_by_lease_id(lease_id)
        if existing is not None:
            return existing, False
        max_statement = select(func.max(RpaExecutionAttempt.attempt_no)).where(
            RpaExecutionAttempt.run_id == run_id
        )
        max_attempt = (await self._session.execute(max_statement)).scalar_one()
        attempt = RpaExecutionAttempt(
            dispatch_mode="LEASE",
            command_id=None,
            lease_id=lease_id,
            task_id=task_id,
            run_id=run_id,
            workflow_binding_id=workflow_binding_id,
            portal_account_id=portal_account_id,
            worker_instance_id=worker_instance_id,
            worker_id=worker_id,
            flow_version_id=flow_version_id,
            rpa_flow_id_snapshot=rpa_flow_id,
            rpa_flow_version_snapshot=rpa_flow_version,
            package_checksum_snapshot=package_checksum,
            attempt_no=int(max_attempt or 0) + 1,
            status=AttemptStatus.LEASED.value,
            input_snapshot=input_snapshot,
            browser_session_snapshot=browser_session_snapshot,
            error_details={},
        )
        self._session.add(attempt)
        await self._session.flush()
        await self._session.refresh(attempt)
        return attempt, True

    async def transition(
        self,
        attempt: RpaExecutionAttempt,
        status: AttemptStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        error_details: dict[str, object] | None = None,
    ) -> None:
        attempt.status = status.value
        attempt.error_code = error_code
        attempt.error_message = error_message
        attempt.error_details = error_details or {}
        attempt.updated_at = datetime.now(UTC)
        await self._session.flush()
