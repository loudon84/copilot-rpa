from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.db.session import DatabaseManager
from nodeskclaw_rpa_engine.workers.errors import (
    RunCommandRejected,
    TaskApiError,
    WorkerConfigurationError,
)
from nodeskclaw_rpa_engine.workers.pool import RunCommandHandler, WorkerPool
from nodeskclaw_rpa_engine.workers.schemas import (
    AttemptStatus,
    LeaseRenewal,
    LeaseRunCommand,
    ResolvedFlowVersion,
    RunCommand,
    RunResult,
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
                "browserSession": {
                    "mode": "MANAGED",
                    "headless": True,
                    "channel": "chromium",
                    "profileRef": None,
                    "cdpEndpointRef": None,
                    "closePolicy": "ALWAYS",
                }
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


async def test_rejected_leased_attempt_transitions_through_running() -> None:
    worker_pool = pool()
    statuses: list[AttemptStatus] = []

    async def record_transition(
        _attempt_id,
        status: AttemptStatus,
        **_kwargs,
    ) -> None:
        statuses.append(status)

    worker_pool._transition = record_transition  # type: ignore[method-assign]
    await worker_pool._fail_attempt(  # noqa: SLF001
        uuid4(),
        attempt_running=False,
        error_code="UNSUPPORTED",
        error_message="unsupported",
    )

    assert statuses == [AttemptStatus.RUNNING, AttemptStatus.FAILED]


def test_capability_mismatch_is_explicit() -> None:
    worker_pool = pool(settings=enabled_settings(worker_capabilities=[
        "PLAYWRIGHT_CDP",
        "BROWSER_SESSION_MANAGED",
    ]))
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
