from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from nodeskclaw_rpa_engine.db.models import (
    RpaExecutionAttempt,
    RpaWorkerInstance,
)
from nodeskclaw_rpa_engine.workers.repository import (
    SqlAlchemyAttemptRepository,
    SqlAlchemyWorkerRepository,
)
from nodeskclaw_rpa_engine.workers.schemas import AttemptStatus, WorkerStatus


class FakeResult:
    def __init__(self, scalar=None, values=None) -> None:
        self.scalar = scalar
        self.values = list(values or [])

    def scalar_one(self):
        return self.scalar

    def scalars(self):
        return self

    def all(self):
        return self.values


class FakeSession:
    def __init__(self, results=None) -> None:
        self.results = list(results or [])
        self.added: list[object] = []
        self.statements: list[object] = []
        self.flushes = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def execute(self, statement, *_args, **_kwargs):
        self.statements.append(statement)
        return self.results.pop(0)

    async def flush(self) -> None:
        self.flushes += 1

    async def refresh(self, value: object) -> None:
        if getattr(value, "id", None) is None:
            value.id = uuid4()


def worker() -> RpaWorkerInstance:
    now = datetime.now(UTC)
    return RpaWorkerInstance(
        id=uuid4(),
        worker_id="worker-1",
        worker_type="SERVER_WORKER",
        device_name="old",
        status="OFFLINE",
        capabilities=["old"],
        tags=[],
        app_version="0.2.0",
        agent_version=None,
        os=None,
        max_concurrent_runs=1,
        current_task_count=0,
        browser_count=0,
        metadata_={"heartbeatCount": 2},
        registered_at=now,
        last_heartbeat_at=now,
        updated_at=now,
    )


async def test_worker_upsert_and_heartbeat_update_existing_record() -> None:
    session = FakeSession()
    existing = worker()
    repository = SqlAlchemyWorkerRepository(session)  # type: ignore[arg-type]

    async def get_existing(*_args, **_kwargs):
        return existing

    repository.get_worker = get_existing  # type: ignore[method-assign]
    result = await repository.upsert_worker(
        worker_id="worker-1",
        worker_type="SERVER_WORKER",
        device_name="new",
        status=WorkerStatus.ONLINE,
        capabilities=["PLAYWRIGHT_CDP", "BROWSER_SESSION_MANAGED"],
        tags=["phase3"],
        app_version="0.3.0",
        agent_version="0.3.0",
        os="Windows",
        max_concurrent_runs=1,
    )
    await repository.heartbeat("worker-1", current_task_count=1)

    assert result is existing
    assert existing.device_name == "new"
    assert existing.status == "BUSY"
    assert existing.current_task_count == 1
    assert existing.metadata_["heartbeatCount"] == 3
    assert session.flushes == 2


async def test_attempt_repository_is_idempotent_by_lease_id() -> None:
    session = FakeSession()
    existing = RpaExecutionAttempt(id=uuid4(), lease_id="lease-1")
    repository = SqlAlchemyAttemptRepository(session)  # type: ignore[arg-type]

    async def get_existing(_lease_id: str):
        return existing

    repository.get_by_lease_id = get_existing  # type: ignore[method-assign]
    result, created = await repository.create_lease_attempt(
        lease_id="lease-1",
        task_id="task-1",
        run_id="run-1",
        workflow_binding_id=None,
        portal_account_id=None,
        worker_instance_id=uuid4(),
        worker_id="worker-1",
        flow_version_id=uuid4(),
        rpa_flow_id="flow-1",
        rpa_flow_version="1.0.0",
        package_checksum="a" * 64,
        input_snapshot={},
        browser_session_snapshot={},
    )

    assert result is existing
    assert created is False
    assert session.added == []


async def test_attempt_number_increments_and_state_transition_flushes() -> None:
    session = FakeSession([FakeResult(None), FakeResult(2)])
    repository = SqlAlchemyAttemptRepository(session)  # type: ignore[arg-type]

    async def no_existing(_lease_id: str):
        return None

    repository.get_by_lease_id = no_existing  # type: ignore[method-assign]
    attempt, created = await repository.create_lease_attempt(
        lease_id="lease-2",
        task_id="task-1",
        run_id="run-1",
        workflow_binding_id="binding-1",
        portal_account_id="portal-1",
        worker_instance_id=uuid4(),
        worker_id="worker-1",
        flow_version_id=uuid4(),
        rpa_flow_id="flow-1",
        rpa_flow_version="1.0.0",
        package_checksum="b" * 64,
        input_snapshot={},
        browser_session_snapshot={"mode": "MANAGED"},
    )
    await repository.transition(attempt, AttemptStatus.RUNNING)
    await repository.transition(
        attempt,
        AttemptStatus.SUCCESS,
    )

    assert created is True
    assert attempt.attempt_no == 3
    assert attempt.status == "SUCCESS"
    assert attempt.started_at is not None
    assert attempt.ended_at is not None
    assert attempt.ended_at >= attempt.started_at
    assert session.added == [attempt]
    assert session.flushes == 3


async def test_active_attempt_query_is_scoped_and_locked_for_recovery() -> None:
    active = [
        RpaExecutionAttempt(id=uuid4(), status=AttemptStatus.LEASED.value),
        RpaExecutionAttempt(id=uuid4(), status=AttemptStatus.RUNNING.value),
    ]
    session = FakeSession([FakeResult(values=active)])
    repository = SqlAlchemyAttemptRepository(session)  # type: ignore[arg-type]

    result = await repository.list_active_for_worker(
        "worker-1",
        for_update=True,
    )

    assert list(result) == active
    sql = str(session.statements[0])
    assert "worker_id" in sql
    assert "status IN" in sql
    assert "FOR UPDATE" in sql
