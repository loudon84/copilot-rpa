from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.db.session import DatabaseManager
from nodeskclaw_rpa_engine.workers.errors import (
    RunCommandRejected,
    TaskApiError,
    WorkerConfigurationError,
)
from nodeskclaw_rpa_engine.workers.repository import (
    SqlAlchemyAttemptRepository,
    SqlAlchemyWorkerRepository,
)
from nodeskclaw_rpa_engine.workers.resolver import FlowVersionResolver
from nodeskclaw_rpa_engine.workers.schemas import (
    AttemptStatus,
    LeaseRunCommand,
    ResolvedFlowVersion,
    RunCommand,
    RunEventRequest,
    RunFinishRequest,
    RunResult,
    WorkerRegisterRequest,
    WorkerStatus,
)
from nodeskclaw_rpa_engine.workers.source import RunCommandSource
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient

logger = logging.getLogger(__name__)


class RunCommandHandler(Protocol):
    async def handle(self, command: RunCommand) -> RunResult: ...


class WorkerPool:
    def __init__(
        self,
        settings: Settings,
        database: DatabaseManager,
        task_client: TaskWorkerApiClient,
        *,
        command_source: RunCommandSource | None = None,
        command_handler: RunCommandHandler | None = None,
        resolver: FlowVersionResolver | None = None,
    ) -> None:
        if settings.worker_lease_enabled and command_handler is None:
            raise WorkerConfigurationError(
                "WORKER_LEASE_ENABLED=true requires a Runtime RunCommandHandler"
            )
        if settings.worker_lease_enabled and command_source is None:
            raise WorkerConfigurationError(
                "WORKER_LEASE_ENABLED=true requires a RunCommandSource"
            )
        self._settings = settings
        self._database = database
        self._task_client = task_client
        self._source = command_source
        self._handler = command_handler
        self._resolver = resolver or FlowVersionResolver(settings, database)
        self._worker_instance_id: UUID | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._stopping = False

    @property
    def active_count(self) -> int:
        return len(self._active_tasks)

    async def start(self) -> None:
        if not self._settings.worker_enabled:
            return
        await self._task_client.register(
            WorkerRegisterRequest(
                worker_id=self._settings.worker_id,
                worker_type=self._settings.worker_type.value,
                device_name=self._settings.worker_device_name,
                capabilities=self._settings.worker_capabilities,
                app_version=self._settings.app_version,
                agent_version=self._settings.worker_agent_version,
                os=self._settings.worker_os,
            )
        )
        async with self._database.session() as session, session.begin():
            worker = await SqlAlchemyWorkerRepository(session).upsert_worker(
                worker_id=self._settings.worker_id,
                worker_type=self._settings.worker_type.value,
                device_name=self._settings.worker_device_name,
                status=WorkerStatus.ONLINE,
                capabilities=self._settings.worker_capabilities,
                tags=self._settings.worker_tags,
                app_version=self._settings.app_version,
                agent_version=self._settings.worker_agent_version,
                os=self._settings.worker_os,
                max_concurrent_runs=self._settings.worker_max_concurrent_runs,
            )
            self._worker_instance_id = worker.id
        self._stopping = False
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"worker-heartbeat:{self._settings.worker_id}",
        )
        if self._settings.worker_lease_enabled:
            self._poll_task = asyncio.create_task(
                self._poll_loop(),
                name=f"worker-lease:{self._settings.worker_id}",
            )

    async def stop(self) -> None:
        if not self._settings.worker_enabled or self._worker_instance_id is None:
            return
        self._stopping = True
        await self._set_worker_status(WorkerStatus.DRAINING)
        for background in (self._poll_task, self._heartbeat_task):
            if background is not None:
                background.cancel()
        for background in (self._poll_task, self._heartbeat_task):
            if background is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await background
        if self._active_tasks:
            _, pending = await asyncio.wait(
                set(self._active_tasks.values()),
                timeout=self._settings.worker_shutdown_grace_seconds,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        await self._set_worker_status(WorkerStatus.OFFLINE, current_task_count=0)

    async def dispatch(self, lease: LeaseRunCommand) -> bool:
        """Dispatch a leased command. Exposed for deterministic Phase 3 tests."""
        if self._handler is None:
            raise WorkerConfigurationError("Runtime RunCommandHandler is unavailable")
        if lease.lease_id in self._active_tasks:
            return False
        if self.active_count >= self._settings.worker_max_concurrent_runs:
            return False
        task = asyncio.create_task(
            self._execute(lease),
            name=f"worker-run:{lease.run_id}:{lease.lease_id}",
        )
        self._active_tasks[lease.lease_id] = task
        task.add_done_callback(
            lambda _: self._active_tasks.pop(lease.lease_id, None)
        )
        return True

    async def _heartbeat_loop(self) -> None:
        while not self._stopping:
            try:
                await self._task_client.heartbeat(self._settings.worker_id)
                async with self._database.session() as session, session.begin():
                    await SqlAlchemyWorkerRepository(session).heartbeat(
                        self._settings.worker_id,
                        current_task_count=self.active_count,
                    )
            except Exception:
                logger.exception(
                    "Worker heartbeat failed",
                    extra={"workerId": self._settings.worker_id},
                )
            await asyncio.sleep(
                self._settings.worker_heartbeat_interval_seconds
            )

    async def _poll_loop(self) -> None:
        assert self._source is not None
        while not self._stopping:
            available = self._settings.worker_max_concurrent_runs - self.active_count
            if available > 0:
                try:
                    commands = await self._source.receive(available)
                    for command in commands[:available]:
                        await self.dispatch(command)
                except Exception:
                    logger.exception(
                        "Worker lease poll failed",
                        extra={"workerId": self._settings.worker_id},
                    )
            await asyncio.sleep(self._settings.worker_poll_interval_seconds)

    async def _execute(self, lease: LeaseRunCommand) -> None:
        attempt_id: UUID | None = None
        attempt_running = False
        try:
            if await self._lease_already_recorded(lease.lease_id):
                return
            flow = await self._resolver.resolve(lease)
            assert self._worker_instance_id is not None
            async with self._database.session() as session, session.begin():
                attempt, created = (
                    await SqlAlchemyAttemptRepository(session).create_lease_attempt(
                        lease_id=lease.lease_id,
                        task_id=lease.task_id,
                        run_id=lease.run_id,
                        workflow_binding_id=lease.workflow_binding_id,
                        portal_account_id=lease.portal_account_id,
                        worker_instance_id=self._worker_instance_id,
                        worker_id=self._settings.worker_id,
                        flow_version_id=flow.flow_version_id,
                        rpa_flow_id=flow.rpa_flow_id,
                        rpa_flow_version=flow.version,
                        package_checksum=flow.package_checksum,
                        input_snapshot=lease.input,
                        browser_session_snapshot=lease.config.browser_session.model_dump(
                            mode="json", by_alias=True
                        ),
                    )
                )
                attempt_id = attempt.id
            if not created:
                return
            self._validate_lease(lease)
            self._validate_flow_contract(lease, flow)
            self._validate_capabilities(flow.capabilities)
            await self._transition(attempt_id, AttemptStatus.RUNNING)
            attempt_running = True
            await self._set_worker_status(
                WorkerStatus.BUSY,
                current_task_count=self.active_count,
            )
            await self._safe_event(
                lease.run_id,
                RunEventRequest(
                    worker_id=self._settings.worker_id,
                    type="RUN_STARTED",
                    message="Run started",
                    payload={"leaseId": lease.lease_id},
                ),
            )
            result = await self._handle_with_renewal(RunCommand(lease=lease, flow=flow))
            await self._transition(
                attempt_id,
                result.status,
                error_code=result.error_code,
                error_message=result.error_message,
            )
            try:
                await self._task_client.finish(
                    lease.run_id,
                    RunFinishRequest(
                        status=(
                            AttemptStatus.FAILED
                            if result.status is AttemptStatus.ABANDONED
                            else result.status
                        ),
                        error_code=result.error_code,
                        error_message=result.error_message,
                    ),
                )
            except TaskApiError:
                logger.warning(
                    "Run finish callback failed",
                    extra={"runId": lease.run_id},
                )
        except RunCommandRejected as exc:
            if attempt_id is not None:
                await self._fail_attempt(
                    attempt_id,
                    attempt_running=attempt_running,
                    error_code=exc.code,
                    error_message=exc.message,
                )
            await self._safe_finish_rejected(lease, exc.code, exc.message)
        except Exception as exc:
            logger.exception(
                "Worker command failed",
                extra={"runId": lease.run_id, "workerId": self._settings.worker_id},
            )
            if attempt_id is not None:
                with contextlib.suppress(Exception):
                    await self._fail_attempt(
                        attempt_id,
                        attempt_running=attempt_running,
                        error_code="ENGINE_WORKER_ERROR",
                        error_message=type(exc).__name__,
                    )
            await self._safe_finish_rejected(
                lease,
                "ENGINE_WORKER_ERROR",
                "Engine Worker failed to process the command",
            )
        finally:
            with contextlib.suppress(Exception):
                next_status = (
                    WorkerStatus.DRAINING if self._stopping else WorkerStatus.ONLINE
                )
                await self._set_worker_status(
                    next_status,
                    current_task_count=max(0, self.active_count - 1),
                )

    async def _handle_with_renewal(self, command: RunCommand) -> RunResult:
        assert self._handler is not None
        execution = asyncio.create_task(self._handler.handle(command))
        expires_at = command.lease.lease_expires_at
        while True:
            now = datetime.now(UTC)
            seconds_to_expiry = max(0.0, (expires_at - now).total_seconds())
            timeout = min(
                self._settings.worker_lease_renew_interval_seconds,
                seconds_to_expiry,
            )
            done, _ = await asyncio.wait({execution}, timeout=timeout)
            if execution in done:
                return await execution
            if datetime.now(UTC) >= expires_at:
                execution.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await execution
                return RunResult(
                    status=AttemptStatus.ABANDONED,
                    error_code="LEASE_EXPIRED",
                    error_message="Lease expired before the run completed",
                )
            assert self._source is not None
            try:
                renewal = await self._source.renew(command.lease)
                expires_at = renewal.lease_expires_at
            except TaskApiError:
                logger.warning(
                    "Lease renewal failed; execution will stop at lease expiry",
                    extra={"runId": command.lease.run_id},
                )

    async def _transition(
        self,
        attempt_id: UUID,
        status: AttemptStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        async with self._database.session() as session, session.begin():
            repository = SqlAlchemyAttemptRepository(session)
            attempt = await repository.get_by_id(attempt_id, for_update=True)
            if attempt is None:
                raise LookupError("Execution attempt was not found")
            await repository.transition(
                attempt,
                status,
                error_code=error_code,
                error_message=error_message,
            )

    async def _fail_attempt(
        self,
        attempt_id: UUID,
        *,
        attempt_running: bool,
        error_code: str,
        error_message: str,
    ) -> None:
        if not attempt_running:
            await self._transition(attempt_id, AttemptStatus.RUNNING)
        await self._transition(
            attempt_id,
            AttemptStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )

    async def _lease_already_recorded(self, lease_id: str) -> bool:
        async with self._database.session() as session:
            attempt = await SqlAlchemyAttemptRepository(session).get_by_lease_id(
                lease_id
            )
        return attempt is not None

    async def _set_worker_status(
        self,
        status: WorkerStatus,
        *,
        current_task_count: int | None = None,
    ) -> None:
        async with self._database.session() as session, session.begin():
            await SqlAlchemyWorkerRepository(session).set_status(
                self._settings.worker_id,
                status,
                current_task_count=current_task_count,
            )

    def _validate_lease(self, lease: LeaseRunCommand) -> None:
        if lease.rpa_engine_type != "PLAYWRIGHT_CDP":
            raise RunCommandRejected(
                "RPA_ENGINE_TYPE_UNSUPPORTED",
                "Only PLAYWRIGHT_CDP commands are supported",
            )
        if lease.config.browser_session.mode != "MANAGED":
            raise RunCommandRejected(
                "BROWSER_SESSION_MODE_UNSUPPORTED",
                "Only MANAGED browser sessions are supported",
            )
        if lease.lease_expires_at <= datetime.now(UTC):
            raise RunCommandRejected(
                "LEASE_EXPIRED",
                "The lease has already expired",
            )

    def _validate_capabilities(self, flow_capabilities: list[str]) -> None:
        available = set(self._settings.worker_capabilities)
        required = {
            "PLAYWRIGHT_CDP",
            "BROWSER_SESSION_MANAGED",
            *flow_capabilities,
        }
        missing = sorted(required.difference(available))
        if missing:
            raise RunCommandRejected(
                "WORKER_CAPABILITY_MISMATCH",
                "Worker is missing required capabilities: " + ", ".join(missing),
            )

    @staticmethod
    def _validate_flow_contract(
        lease: LeaseRunCommand,
        flow: ResolvedFlowVersion,
    ) -> None:
        if flow.engine_type != lease.rpa_engine_type:
            raise RunCommandRejected(
                "FLOW_ENGINE_TYPE_MISMATCH",
                "The lease engine type does not match the Flow version",
            )
        if lease.workflow_code not in flow.supported_workflow_codes:
            raise RunCommandRejected(
                "FLOW_WORKFLOW_CODE_UNSUPPORTED",
                "The exact Flow version does not support the workflow code",
            )

    async def _safe_event(self, run_id: str, request: RunEventRequest) -> None:
        try:
            await self._task_client.event(run_id, request)
        except TaskApiError:
            logger.warning("Run event callback failed", extra={"runId": run_id})

    async def _safe_finish_rejected(
        self,
        lease: LeaseRunCommand,
        code: str,
        message: str,
    ) -> None:
        with contextlib.suppress(TaskApiError):
            await self._task_client.finish(
                lease.run_id,
                RunFinishRequest(
                    status=AttemptStatus.FAILED,
                    error_code=code,
                    error_message=message,
                ),
            )
