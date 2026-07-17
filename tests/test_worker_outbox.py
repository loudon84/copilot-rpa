from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

import nodeskclaw_rpa_engine.workers.outbox as outbox_module
from nodeskclaw_rpa_engine.db.models import RpaCallbackOutbox, RpaExecutionAttempt
from nodeskclaw_rpa_engine.db.session import DatabaseManager
from nodeskclaw_rpa_engine.workers.outbox import (
    CallbackDelivery,
    CallbackOutboxDispatcher,
    CallbackType,
    OutboxStatus,
    SqlAlchemyCallbackOutboxRepository,
)
from nodeskclaw_rpa_engine.workers.schemas import AttemptStatus
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient


class FakeResult:
    def __init__(
        self, scalar: object = None, values: list[object] | None = None
    ) -> None:
        self._scalar = scalar
        self._values = values or []

    def scalar_one(self) -> object:
        return self._scalar

    def scalar_one_or_none(self) -> object:
        return self._scalar

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[object]:
        return self._values


class FakeSession:
    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self.results = list(results or [])
        self.statements: list[object] = []
        self.added: list[object] = []
        self.flushes = 0

    async def execute(self, statement: object, *_args: object) -> FakeResult:
        self.statements.append(statement)
        return self.results.pop(0) if self.results else FakeResult()

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


def attempt() -> RpaExecutionAttempt:
    return RpaExecutionAttempt(
        id=uuid4(),
        lease_id="lease-1",
        run_id="run-1",
        status=AttemptStatus.RUNNING.value,
    )


def message(*, attempts: int, max_attempts: int) -> RpaCallbackOutbox:
    now = datetime.now(UTC)
    return RpaCallbackOutbox(
        id=uuid4(),
        execution_attempt_id=uuid4(),
        destination="NODESKCLAW_TASK",
        callback_type=CallbackType.EVENT.value,
        aggregate_id="run-1",
        sequence_no=1,
        idempotency_key=uuid4().hex,
        endpoint_path="/worker-api/runs/run-1/events",
        payload={},
        status=OutboxStatus.IN_FLIGHT.value,
        attempts=attempts,
        max_attempts=max_attempts,
        next_attempt_at=now,
        locked_by="dispatcher-1",
        locked_at=now,
        created_at=now,
        updated_at=now,
    )


async def test_enqueue_locks_attempt_before_assigning_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked: list[tuple[object, bool]] = []
    target = attempt()

    async def get_by_id(
        _repository: object,
        attempt_id: object,
        *,
        for_update: bool = False,
    ) -> RpaExecutionAttempt:
        locked.append((attempt_id, for_update))
        return target

    monkeypatch.setattr(
        outbox_module.SqlAlchemyAttemptRepository,
        "get_by_id",
        get_by_id,
    )
    session = FakeSession([FakeResult(None), FakeResult(4)])
    repository = SqlAlchemyCallbackOutboxRepository(cast(Any, session))

    created, was_created = await repository.enqueue(
        attempt=target,
        callback_type=CallbackType.EVENT,
        aggregate_id="run-1",
        endpoint_path="/worker-api/runs/run-1/events",
        payload={"type": "RUN_STARTED"},
        idempotency_key="event-key",
    )

    assert locked == [(target.id, True)]
    assert was_created is True
    assert created.sequence_no == 5
    assert created.idempotency_key == "event-key"
    assert session.added == [created]


async def test_enqueue_is_idempotent_after_attempt_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = attempt()
    existing = message(attempts=0, max_attempts=10)
    existing.idempotency_key = "stable-key"
    lock = AsyncMock(return_value=target)
    monkeypatch.setattr(
        outbox_module.SqlAlchemyAttemptRepository,
        "get_by_id",
        lock,
    )
    session = FakeSession([FakeResult(existing)])
    repository = SqlAlchemyCallbackOutboxRepository(cast(Any, session))

    result, created = await repository.enqueue(
        attempt=target,
        callback_type=CallbackType.FINISH,
        aggregate_id="run-1",
        endpoint_path="/worker-api/runs/run-1/finish",
        payload={"status": "SUCCESS"},
        idempotency_key="stable-key",
    )

    assert result is existing
    assert created is False
    lock.assert_awaited_once_with(target.id, for_update=True)
    assert session.added == []


async def test_claim_order_treats_dead_as_terminal_predecessor() -> None:
    session = FakeSession([FakeResult(values=[])])
    repository = SqlAlchemyCallbackOutboxRepository(cast(Any, session))

    assert (
        await repository.claim_ready(
            locked_by="dispatcher-1",
            limit=20,
            now=datetime.now(UTC),
        )
        == []
    )

    statement = session.statements[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "NOT IN" in sql
    assert "SENT" in sql
    assert "DEAD" in sql
    assert "NOT (EXISTS" in sql


async def test_failure_retries_then_marks_message_dead() -> None:
    session = FakeSession()
    repository = SqlAlchemyCallbackOutboxRepository(cast(Any, session))
    target = message(attempts=1, max_attempts=2)

    async def get_claimed(*_args: object, **_kwargs: object) -> RpaCallbackOutbox:
        return target

    repository._get_claimed = get_claimed  # type: ignore[method-assign]
    await repository.mark_failed(
        target.id,
        locked_by="dispatcher-1",
        error="temporary",
        now=datetime.now(UTC),
    )
    assert target.status == OutboxStatus.RETRY.value

    target.status = OutboxStatus.IN_FLIGHT.value
    target.attempts = 2
    await repository.mark_failed(
        target.id,
        locked_by="dispatcher-1",
        error="permanent",
        now=datetime.now(UTC),
    )
    assert target.status == OutboxStatus.DEAD.value
    assert target.locked_by is None


async def test_stale_recovery_handles_missing_lock_and_exhausted_attempts() -> None:
    session = FakeSession()
    repository = SqlAlchemyCallbackOutboxRepository(cast(Any, session))
    now = datetime.now(UTC)

    await repository.recover_stale(stale_before=now, now=now)

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "locked_at IS NULL" in sql
    assert "CASE WHEN" in sql
    assert "DEAD" in sql
    assert "STALE_DISPATCH_LOCK_RECOVERED" in sql


class Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class DispatcherSession:
    def begin(self) -> Transaction:
        return Transaction()


class FakeDatabase:
    def __init__(self) -> None:
        self.value = DispatcherSession()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[DispatcherSession]:
        yield self.value


async def test_dispatcher_recovers_stale_on_every_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Repository:
        def __init__(self, _session: object) -> None:
            pass

        async def recover_stale(self, **_kwargs: object) -> None:
            calls.append("recover")

        async def claim_ready(self, **_kwargs: object) -> list[CallbackDelivery]:
            calls.append("claim")
            return []

    monkeypatch.setattr(outbox_module, "SqlAlchemyCallbackOutboxRepository", Repository)
    dispatcher = CallbackOutboxDispatcher(
        cast(DatabaseManager, FakeDatabase()),
        cast(TaskWorkerApiClient, object()),
        worker_id="worker-1",
    )

    assert await dispatcher.dispatch_once() == 0
    assert await dispatcher.dispatch_once() == 0
    assert calls == ["recover", "claim", "recover", "claim"]


async def test_dispatcher_delivers_with_stored_idempotency_key() -> None:
    client = AsyncMock(spec=TaskWorkerApiClient)
    dispatcher = CallbackOutboxDispatcher(
        cast(DatabaseManager, object()),
        client,
        worker_id="worker-1",
    )
    event = CallbackDelivery(
        id=uuid4(),
        callback_type=CallbackType.EVENT,
        aggregate_id="run-1",
        sequence_no=1,
        idempotency_key="event-key",
        endpoint_path="/worker-api/runs/run-1/events",
        payload={"type": "RUN_STARTED", "message": "started"},
    )
    finish = CallbackDelivery(
        id=uuid4(),
        callback_type=CallbackType.FINISH,
        aggregate_id="run-1",
        sequence_no=2,
        idempotency_key="finish-key",
        endpoint_path="/worker-api/runs/run-1/finish",
        payload={"status": "SUCCESS"},
    )

    await dispatcher._deliver(event)  # noqa: SLF001
    await dispatcher._deliver(finish)  # noqa: SLF001

    assert client.event.await_args.kwargs["idempotency_key"] == "event-key"
    assert client.finish.await_args.kwargs["idempotency_key"] == "finish-key"
