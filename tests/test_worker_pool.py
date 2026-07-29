from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import nodeskclaw_rpa_engine.workers.pool as pool_module
from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.db.models import RpaExecutionAttempt
from nodeskclaw_rpa_engine.db.session import DatabaseManager
from nodeskclaw_rpa_engine.workers.errors import (
    RunCommandRejected,
    TaskApiError,
    WorkerConfigurationError,
)
from nodeskclaw_rpa_engine.workers.outbox import CallbackOutboxService
from nodeskclaw_rpa_engine.workers.pool import RunCommandHandler, WorkerPool
from nodeskclaw_rpa_engine.workers.schemas import (
    AttemptStatus,
    LeaseRenewal,
    LeaseRunCommand,
    ResolvedFlowVersion,
    RunCommand,
    RunFinishRequest,
    RunResult,
    WorkerStatus,
)
from nodeskclaw_rpa_engine.workers.source import RunCommandSource
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient


def enabled_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "database_enabled": True,
        "database_url": "postgresql+asyncpg://user:secret@db/nodeskclaw_task",
        "worker_enabled": True,
        "worker_lease_enabled": True,
        "worker_lease_renew_interval_seconds": 0.01,
        "worker_capabilities": [
            "PLAYWRIGHT_CDP",
            "BROWSER_SESSION_MANAGED",
            "download",
        ],
    }
    values.update(updates)
    return Settings(**values)


def lease(lease_id: str = "lease-1", *, expires_in: float = 60) -> LeaseRunCommand:
    return LeaseRunCommand.model_validate(
        {
            "taskId": "task-1",
            "runId": "run-1",
            "leaseId": lease_id,
            "workflowBindingId": "binding-1",
            "portalAccountId": "portal-1",
            "rpaFlowId": "flow-1",
            "input": {},
            "tenantId": "tenant-1",
            "workflowTemplateId": "template-1",
            "workflowCode": "fetch_po",
            "rpaEngineType": "PLAYWRIGHT_CDP",
            "rpaFlowVersion": "1.0.0",
            "credentialRef": None,
            "config": {
                "portalUrl": "http://mock.test",
                "browserSession": {
                    "mode": "MANAGED",
                    "headless": True,
                    "channel": "chromium",
                    "profileRef": None,
                    "cdpEndpointRef": None,
                    "closePolicy": "ALWAYS",
                },
            },
            "leaseExpiresAt": (
                datetime.now(UTC) + timedelta(seconds=expires_in)
            ).isoformat(),
        }
    )


def run_command(command_lease: LeaseRunCommand) -> RunCommand:
    return RunCommand(
        lease=command_lease,
        flow=ResolvedFlowVersion(
            flow_version_id=uuid4(),
            rpa_flow_id="flow-1",
            version="1.0.0",
            engine_type="PLAYWRIGHT_CDP",
            package_uri="http://engine/package",
            package_checksum="a" * 64,
            supported_workflow_codes=["fetch_po"],
            capabilities=["download"],
        ),
    )


class SleepingHandler:
    async def handle(self, _command: RunCommand) -> RunResult:
        await asyncio.sleep(60)
        return RunResult(status=AttemptStatus.SUCCESS)


class FailingRenewSource:
    async def receive(self, _available_slots: int) -> list[LeaseRunCommand]:
        return []

    async def renew(self, _command: LeaseRunCommand) -> LeaseRenewal:
        raise TaskApiError("RENEW_FAILED", "renew failed")


class TrackingHandler:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def handle(self, _command: RunCommand) -> RunResult:
        self.started.set()
        try:
            await asyncio.sleep(60)
        finally:
            self.cancelled.set()
        return RunResult(status=AttemptStatus.SUCCESS)


class DelayedCancellationHandler:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.completed = asyncio.Event()

    async def handle(self, _command: RunCommand) -> RunResult:
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)
        finally:
            self.completed.set()
        return RunResult(status=AttemptStatus.SUCCESS)


class UnexpectedRenewSource:
    async def receive(self, _available_slots: int) -> list[LeaseRunCommand]:
        return []

    async def renew(self, _command: LeaseRunCommand) -> LeaseRenewal:
        raise RuntimeError("unexpected renewal failure")


class ExitErrorContext:
    def __init__(self, value: object, error: BaseException | None = None) -> None:
        self.value = value
        self.error = error

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *_args: object) -> None:
        if self.error is not None:
            raise self.error


class ExitErrorSession:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def begin(self) -> ExitErrorContext:
        return ExitErrorContext(None, self.error)


class ExitErrorDatabase:
    def __init__(self, error: BaseException) -> None:
        self.value = ExitErrorSession(error)

    def session(self) -> ExitErrorContext:
        return ExitErrorContext(self.value)


def pool(
    *,
    settings: Settings | None = None,
    source: RunCommandSource | None = None,
    handler: RunCommandHandler | None = None,
) -> WorkerPool:
    return WorkerPool(
        settings or enabled_settings(),
        cast(DatabaseManager, object()),
        cast(TaskWorkerApiClient, object()),
        command_source=source or FailingRenewSource(),
        command_handler=handler or SleepingHandler(),
    )


def test_lease_enabled_without_runtime_handler_is_rejected() -> None:
    with pytest.raises(WorkerConfigurationError, match="RunCommandHandler"):
        WorkerPool(
            enabled_settings(),
            cast(DatabaseManager, object()),
            cast(TaskWorkerApiClient, object()),
            command_source=FailingRenewSource(),
            command_handler=None,
        )


async def test_duplicate_lease_and_concurrency_slot_are_not_dispatched_twice() -> None:
    worker_pool = pool()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_execute(_lease: LeaseRunCommand) -> None:
        started.set()
        await release.wait()

    worker_pool._execute = fake_execute  # type: ignore[method-assign]
    first = lease()
    assert await worker_pool.dispatch(first) is True
    await started.wait()
    assert await worker_pool.dispatch(first) is False
    assert await worker_pool.dispatch(lease("lease-2")) is False
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert worker_pool.active_count == 0


async def test_failed_renewal_cancels_handler_at_lease_expiry() -> None:
    worker_pool = pool()
    result = await worker_pool._handle_with_renewal(  # noqa: SLF001
        run_command(lease(expires_in=0.04))
    )
    assert result.status is AttemptStatus.ABANDONED
    assert result.error_code == "LEASE_EXPIRED"


async def test_outer_cancellation_cancels_and_awaits_runtime_child() -> None:
    handler = TrackingHandler()
    worker_pool = pool(handler=handler)
    execution = asyncio.create_task(
        worker_pool._handle_with_renewal(run_command(lease()))  # noqa: SLF001
    )
    await handler.started.wait()

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    assert handler.cancelled.is_set()


async def test_outer_cancellation_is_bounded_when_runtime_child_ignores_cancel() -> (
    None
):
    handler = DelayedCancellationHandler()
    worker_pool = pool(
        settings=enabled_settings(worker_shutdown_grace_seconds=0),
        handler=handler,
    )
    execution = asyncio.create_task(
        worker_pool._handle_with_renewal(run_command(lease()))  # noqa: SLF001
    )
    await handler.started.wait()
    started = asyncio.get_running_loop().time()

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    assert asyncio.get_running_loop().time() - started < 0.5
    await asyncio.wait_for(handler.completed.wait(), timeout=1)


async def test_unexpected_renew_error_cancels_and_awaits_runtime_child() -> None:
    handler = TrackingHandler()
    worker_pool = pool(source=UnexpectedRenewSource(), handler=handler)

    with pytest.raises(RuntimeError, match="unexpected renewal failure"):
        await worker_pool._handle_with_renewal(  # noqa: SLF001
            run_command(lease())
        )

    assert handler.cancelled.is_set()


async def test_unresolved_flow_uses_idempotent_direct_finish_fallback() -> None:
    class RejectingResolver:
        async def resolve(self, _lease: LeaseRunCommand) -> ResolvedFlowVersion:
            raise RunCommandRejected("FLOW_VERSION_NOT_FOUND", "not found")

    client = AsyncMock(spec=TaskWorkerApiClient)
    worker_pool = WorkerPool(
        enabled_settings(),
        cast(DatabaseManager, object()),
        client,
        command_source=FailingRenewSource(),
        command_handler=SleepingHandler(),
        resolver=cast(object, RejectingResolver()),  # type: ignore[arg-type]
    )
    worker_pool._lease_already_recorded = AsyncMock(  # type: ignore[method-assign]
        return_value=False
    )
    worker_pool._set_worker_status = AsyncMock()  # type: ignore[method-assign]

    await worker_pool._execute(lease())  # noqa: SLF001

    client.finish.assert_awaited_once()
    assert client.finish.await_args.args[0] == "run-1"
    assert client.finish.await_args.kwargs["idempotency_key"].startswith(
        "rpa:unrecorded-finish:"
    )


async def test_cancellation_before_attempt_uses_shielded_finish_fallback() -> None:
    class BlockingResolver:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def resolve(self, _lease: LeaseRunCommand) -> ResolvedFlowVersion:
            self.started.set()
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

    resolver = BlockingResolver()
    client = AsyncMock(spec=TaskWorkerApiClient)
    worker_pool = WorkerPool(
        enabled_settings(),
        cast(DatabaseManager, object()),
        client,
        command_source=FailingRenewSource(),
        command_handler=SleepingHandler(),
        resolver=cast(object, resolver),  # type: ignore[arg-type]
    )
    worker_pool._lease_already_recorded = AsyncMock(  # type: ignore[method-assign]
        return_value=False
    )
    worker_pool._set_worker_status = AsyncMock()  # type: ignore[method-assign]
    execution = asyncio.create_task(worker_pool._execute(lease()))  # noqa: SLF001
    await resolver.started.wait()

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    client.finish.assert_awaited_once()
    request = client.finish.await_args.args[1]
    assert request.error_code == "WORKER_SHUTDOWN"


@pytest.mark.parametrize(
    ('exit_error', 'expected_error_code'),
    [
        (OSError('commit failed'), 'ENGINE_WORKER_ERROR'),
        (asyncio.CancelledError(), 'WORKER_SHUTDOWN'),
    ],
)
async def test_attempt_is_unrecorded_until_transaction_exit_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    exit_error: BaseException,
    expected_error_code: str,
) -> None:
    command = lease()
    target = RpaExecutionAttempt(id=uuid4())
    database = ExitErrorDatabase(exit_error)
    repository = AsyncMock()
    repository.create_lease_attempt.return_value = (target, True)
    monkeypatch.setattr(
        pool_module,
        'SqlAlchemyAttemptRepository',
        lambda _session: repository,
    )
    resolver = AsyncMock()
    resolver.resolve.return_value = run_command(command).flow
    client = AsyncMock(spec=TaskWorkerApiClient)
    worker_pool = WorkerPool(
        enabled_settings(),
        cast(DatabaseManager, database),
        client,
        command_source=FailingRenewSource(),
        command_handler=SleepingHandler(),
        resolver=resolver,
    )
    worker_pool._worker_instance_id = uuid4()  # noqa: SLF001
    worker_pool._lease_already_recorded = AsyncMock(  # type: ignore[method-assign]
        return_value=False
    )
    worker_pool._set_worker_status = AsyncMock()  # type: ignore[method-assign]

    if isinstance(exit_error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await worker_pool._execute(command)  # noqa: SLF001
    else:
        await worker_pool._execute(command)  # noqa: SLF001

    client.finish.assert_awaited_once()
    request = client.finish.await_args.args[1]
    assert request.error_code == expected_error_code


async def test_shutdown_abandons_attempt_and_enqueues_finish_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = RpaExecutionAttempt(
        id=uuid4(),
        status=AttemptStatus.RUNNING.value,
        error_details={},
    )
    transitions: list[AttemptStatus] = []

    class Context:
        def __init__(self, value: object) -> None:
            self.value = value

        async def __aenter__(self) -> object:
            return self.value

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Session:
        def begin(self) -> Context:
            return Context(None)

    session = Session()

    class Database:
        def session(self) -> Context:
            return Context(session)

    class AttemptRepository:
        def __init__(self, used_session: object) -> None:
            assert used_session is session

        async def get_by_id(
            self,
            _attempt_id: object,
            *,
            for_update: bool = False,
        ) -> RpaExecutionAttempt:
            assert for_update is True
            return target

        async def transition(
            self,
            attempt: RpaExecutionAttempt,
            status: AttemptStatus,
            **kwargs: object,
        ) -> None:
            transitions.append(status)
            attempt.status = status.value
            attempt.error_code = cast(str | None, kwargs.get("error_code"))
            attempt.error_message = cast(str | None, kwargs.get("error_message"))

    class Outbox:
        def __init__(self) -> None:
            self.calls: list[tuple[object, RunFinishRequest]] = []

        async def enqueue_finish(
            self,
            used_session: object,
            **kwargs: object,
        ) -> None:
            self.calls.append((used_session, cast(RunFinishRequest, kwargs["request"])))

    monkeypatch.setattr(pool_module, "SqlAlchemyAttemptRepository", AttemptRepository)
    outbox = Outbox()
    worker_pool = WorkerPool(
        enabled_settings(),
        cast(DatabaseManager, Database()),
        cast(TaskWorkerApiClient, object()),
        command_source=FailingRenewSource(),
        command_handler=SleepingHandler(),
        callback_outbox=cast(CallbackOutboxService, outbox),
    )

    await worker_pool._complete_attempt(  # noqa: SLF001
        target.id,
        run_id="run-1",
        status=AttemptStatus.ABANDONED,
        error_code="WORKER_SHUTDOWN",
        error_message="worker stopped",
    )

    assert transitions == [AttemptStatus.ABANDONED]
    assert target.status == AttemptStatus.ABANDONED.value
    assert outbox.calls[0][0] is session
    assert outbox.calls[0][1].status is AttemptStatus.FAILED
    assert outbox.calls[0][1].error_code == "WORKER_SHUTDOWN"

    target.status = AttemptStatus.LEASED.value
    transitions.clear()
    await worker_pool._complete_attempt(  # noqa: SLF001
        target.id,
        run_id="run-1",
        status=AttemptStatus.FAILED,
        error_code="UNSUPPORTED",
        error_message="unsupported",
    )
    assert transitions == [AttemptStatus.RUNNING, AttemptStatus.FAILED]

    target.status = AttemptStatus.RUNNING.value
    transitions.clear()
    await worker_pool._complete_attempt(  # noqa: SLF001
        target.id,
        run_id="run-1",
        status=AttemptStatus.SUCCESS,
        error_code=None,
        error_message=None,
        output={"schemaVersion": "ORDER_DOWNLOAD_PUSH_OUTPUT_V1"},
    )
    assert transitions == [AttemptStatus.SUCCESS]
    assert outbox.calls[-1][1].output == {
        "schemaVersion": "ORDER_DOWNLOAD_PUSH_OUTPUT_V1"
    }


async def test_stop_cancels_active_tasks_when_draining_write_fails() -> None:
    worker_pool = pool(settings=enabled_settings(worker_shutdown_grace_seconds=0))
    worker_pool._worker_instance_id = uuid4()  # noqa: SLF001
    cancelled = asyncio.Event()
    statuses: list[WorkerStatus] = []

    async def active_run() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    async def set_status(status: WorkerStatus, **_kwargs: object) -> None:
        statuses.append(status)
        if status is WorkerStatus.DRAINING:
            raise OSError("database unavailable")

    active = asyncio.create_task(active_run())
    worker_pool._active_tasks["lease-1"] = active  # noqa: SLF001
    worker_pool._set_worker_status = set_status  # type: ignore[method-assign]
    await asyncio.sleep(0)

    await worker_pool.stop()

    assert cancelled.is_set()
    assert active.done()
    assert statuses == [WorkerStatus.DRAINING, WorkerStatus.OFFLINE]


async def test_worker_start_recovery_abandons_active_attempts_with_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leased = RpaExecutionAttempt(
        id=uuid4(),
        run_id="run-leased",
        status=AttemptStatus.LEASED.value,
        error_details={},
    )
    running = RpaExecutionAttempt(
        id=uuid4(),
        run_id="run-running",
        status=AttemptStatus.RUNNING.value,
        error_details={},
    )

    class Context:
        def __init__(self, value: object) -> None:
            self.value = value

        async def __aenter__(self) -> object:
            return self.value

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Session:
        def begin(self) -> Context:
            return Context(None)

    session = Session()

    class Database:
        def session(self) -> Context:
            return Context(session)

    repository = AsyncMock()
    repository.list_active_for_worker.return_value = [leased, running]

    async def transition(
        target: RpaExecutionAttempt,
        status: AttemptStatus,
        **kwargs: object,
    ) -> None:
        target.status = status.value
        target.error_code = cast(str | None, kwargs.get("error_code"))

    repository.transition.side_effect = transition
    monkeypatch.setattr(
        pool_module,
        "SqlAlchemyAttemptRepository",
        lambda used_session: repository if used_session is session else None,
    )
    outbox = AsyncMock(spec=CallbackOutboxService)
    worker_pool = WorkerPool(
        enabled_settings(),
        cast(DatabaseManager, Database()),
        cast(TaskWorkerApiClient, object()),
        command_source=FailingRenewSource(),
        command_handler=SleepingHandler(),
        callback_outbox=outbox,
    )

    await worker_pool._recover_interrupted_attempts()  # noqa: SLF001

    statuses = [call.args[1] for call in repository.transition.await_args_list]
    assert statuses == [
        AttemptStatus.RUNNING,
        AttemptStatus.ABANDONED,
        AttemptStatus.ABANDONED,
    ]
    assert leased.status == AttemptStatus.ABANDONED.value
    assert running.status == AttemptStatus.ABANDONED.value
    assert leased.error_code == "WORKER_RESTART_RECOVERY"
    assert outbox.enqueue_finish.await_count == 2
    for call in outbox.enqueue_finish.await_args_list:
        assert call.args[0] is session
        assert call.kwargs["request"].status is AttemptStatus.FAILED
        assert call.kwargs["request"].error_code == "WORKER_RESTART_RECOVERY"


async def test_stop_does_not_wait_forever_for_cancellation_resistant_task() -> None:
    worker_pool = pool(settings=enabled_settings(worker_shutdown_grace_seconds=0))
    worker_pool._worker_instance_id = uuid4()  # noqa: SLF001
    release = asyncio.Event()
    cancellation_seen = asyncio.Event()

    async def resistant_run() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancellation_seen.set()
            await release.wait()

    active = asyncio.create_task(resistant_run())
    worker_pool._active_tasks["lease-1"] = active  # noqa: SLF001
    worker_pool._set_worker_status = AsyncMock()  # type: ignore[method-assign]
    await asyncio.sleep(0)
    started = asyncio.get_running_loop().time()

    await worker_pool.stop()

    assert cancellation_seen.is_set()
    assert asyncio.get_running_loop().time() - started < 1
    assert not active.done()
    release.set()
    await active


def test_capability_mismatch_is_explicit() -> None:
    worker_pool = pool(
        settings=enabled_settings(
            worker_capabilities=[
                "PLAYWRIGHT_CDP",
                "BROWSER_SESSION_MANAGED",
            ]
        )
    )
    with pytest.raises(RunCommandRejected) as captured:
        worker_pool._validate_capabilities(["download"])  # noqa: SLF001
    assert captured.value.code == "WORKER_CAPABILITY_MISMATCH"


def test_non_managed_command_is_rejected() -> None:
    worker_pool = pool()
    command = lease()
    command.config.browser_session.mode = "CDP_ATTACH"
    with pytest.raises(RunCommandRejected) as captured:
        worker_pool._validate_lease(command)  # noqa: SLF001
    assert captured.value.code == "BROWSER_SESSION_MODE_UNSUPPORTED"


def test_flow_workflow_code_mismatch_is_rejected() -> None:
    worker_pool = pool()
    command = lease()
    resolved = run_command(command).flow
    command.workflow_code = "other_workflow"
    with pytest.raises(RunCommandRejected) as captured:
        worker_pool._validate_flow_contract(command, resolved)  # noqa: SLF001
    assert captured.value.code == "FLOW_WORKFLOW_CODE_UNSUPPORTED"
