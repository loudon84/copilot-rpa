from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from sqlalchemy import case, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from nodeskclaw_rpa_engine.db.models import (
    RpaCallbackOutbox,
    RpaExecutionAttempt,
)
from nodeskclaw_rpa_engine.db.session import DatabaseManager
from nodeskclaw_rpa_engine.workers.errors import TaskApiError
from nodeskclaw_rpa_engine.workers.repository import SqlAlchemyAttemptRepository
from nodeskclaw_rpa_engine.workers.schemas import (
    RunEventRequest,
    RunFinishRequest,
)
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient

logger = logging.getLogger(__name__)


class CallbackType(StrEnum):
    EVENT = "EVENT"
    FINISH = "FINISH"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    RETRY = "RETRY"
    SENT = "SENT"
    DEAD = "DEAD"


@dataclass(frozen=True, slots=True)
class CallbackDelivery:
    id: UUID
    callback_type: CallbackType
    aggregate_id: str
    sequence_no: int
    idempotency_key: str
    endpoint_path: str
    payload: dict[str, Any]

    @classmethod
    def from_model(cls, model: RpaCallbackOutbox) -> CallbackDelivery:
        return cls(
            id=model.id,
            callback_type=CallbackType(model.callback_type),
            aggregate_id=model.aggregate_id,
            sequence_no=model.sequence_no,
            idempotency_key=model.idempotency_key,
            endpoint_path=model.endpoint_path,
            payload=dict(model.payload),
        )


class SqlAlchemyCallbackOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> RpaCallbackOutbox | None:
        statement = select(RpaCallbackOutbox).where(
            RpaCallbackOutbox.idempotency_key == idempotency_key
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def enqueue(
        self,
        *,
        attempt: RpaExecutionAttempt,
        callback_type: CallbackType,
        aggregate_id: str,
        endpoint_path: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[RpaCallbackOutbox, bool]:
        locked_attempt = await SqlAlchemyAttemptRepository(self._session).get_by_id(
            attempt.id,
            for_update=True,
        )
        if locked_attempt is None:
            raise LookupError("Execution attempt for callback was not found")

        resolved_key = idempotency_key
        if resolved_key is not None:
            existing = await self.get_by_idempotency_key(resolved_key)
            if existing is not None:
                return existing, False

        max_statement = select(func.max(RpaCallbackOutbox.sequence_no)).where(
            RpaCallbackOutbox.execution_attempt_id == attempt.id
        )
        max_sequence = (await self._session.execute(max_statement)).scalar_one()
        sequence_no = int(max_sequence or 0) + 1
        if resolved_key is None:
            resolved_key = (
                f"rpa:{attempt.id}:{callback_type.value.lower()}:"
                f"{sequence_no}:{uuid4().hex}"
            )

        now = datetime.now(UTC)
        message = RpaCallbackOutbox(
            execution_attempt_id=attempt.id,
            destination="NODESKCLAW_TASK",
            callback_type=callback_type.value,
            aggregate_id=aggregate_id,
            sequence_no=sequence_no,
            idempotency_key=resolved_key,
            endpoint_path=endpoint_path,
            payload=payload,
            status=OutboxStatus.PENDING.value,
            attempts=0,
            max_attempts=10,
            next_attempt_at=now,
            locked_by=None,
            locked_at=None,
            last_error=None,
            response_status=None,
            sent_at=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(message)
        await self._session.flush()
        return message, True

    async def claim_ready(
        self,
        *,
        locked_by: str,
        limit: int,
        now: datetime,
    ) -> list[CallbackDelivery]:
        earlier = aliased(RpaCallbackOutbox)
        earlier_unsent = exists(
            select(earlier.id).where(
                earlier.execution_attempt_id == RpaCallbackOutbox.execution_attempt_id,
                earlier.sequence_no < RpaCallbackOutbox.sequence_no,
                earlier.status.notin_(
                    [OutboxStatus.SENT.value, OutboxStatus.DEAD.value]
                ),
            )
        )
        statement = (
            select(RpaCallbackOutbox)
            .where(
                RpaCallbackOutbox.status.in_(
                    [OutboxStatus.PENDING.value, OutboxStatus.RETRY.value]
                ),
                RpaCallbackOutbox.next_attempt_at <= now,
                RpaCallbackOutbox.attempts < RpaCallbackOutbox.max_attempts,
                ~earlier_unsent,
            )
            .order_by(
                RpaCallbackOutbox.next_attempt_at,
                RpaCallbackOutbox.created_at,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        messages = list((await self._session.execute(statement)).scalars().all())
        for message in messages:
            message.status = OutboxStatus.IN_FLIGHT.value
            message.attempts += 1
            message.locked_by = locked_by
            message.locked_at = now
            message.updated_at = now
        if messages:
            await self._session.flush()
        return [CallbackDelivery.from_model(message) for message in messages]

    async def mark_sent(
        self,
        message_id: UUID,
        *,
        locked_by: str,
        now: datetime,
    ) -> None:
        message = await self._get_claimed(message_id, locked_by=locked_by)
        message.status = OutboxStatus.SENT.value
        message.locked_by = None
        message.locked_at = None
        message.last_error = None
        message.sent_at = now
        message.updated_at = now
        await self._session.flush()

    async def mark_failed(
        self,
        message_id: UUID,
        *,
        locked_by: str,
        error: str,
        now: datetime,
    ) -> None:
        message = await self._get_claimed(message_id, locked_by=locked_by)
        message.status = (
            OutboxStatus.DEAD.value
            if message.attempts >= message.max_attempts
            else OutboxStatus.RETRY.value
        )
        backoff_seconds = min(300, 2 ** max(0, message.attempts - 1))
        message.next_attempt_at = now + timedelta(seconds=backoff_seconds)
        message.locked_by = None
        message.locked_at = None
        message.last_error = error[:2000]
        message.updated_at = now
        await self._session.flush()

    async def recover_stale(
        self,
        *,
        stale_before: datetime,
        now: datetime,
    ) -> None:
        statement = (
            update(RpaCallbackOutbox)
            .where(
                RpaCallbackOutbox.status == OutboxStatus.IN_FLIGHT.value,
                or_(
                    RpaCallbackOutbox.locked_at.is_(None),
                    RpaCallbackOutbox.locked_at < stale_before,
                ),
            )
            .values(
                status=case(
                    (
                        RpaCallbackOutbox.attempts >= RpaCallbackOutbox.max_attempts,
                        OutboxStatus.DEAD.value,
                    ),
                    else_=OutboxStatus.RETRY.value,
                ),
                next_attempt_at=now,
                locked_by=None,
                locked_at=None,
                last_error="STALE_DISPATCH_LOCK_RECOVERED",
                updated_at=now,
            )
        )
        await self._session.execute(statement)

    async def _get_claimed(
        self,
        message_id: UUID,
        *,
        locked_by: str,
    ) -> RpaCallbackOutbox:
        statement = (
            select(RpaCallbackOutbox)
            .where(
                RpaCallbackOutbox.id == message_id,
                RpaCallbackOutbox.status == OutboxStatus.IN_FLIGHT.value,
                RpaCallbackOutbox.locked_by == locked_by,
            )
            .with_for_update()
        )
        message = (await self._session.execute(statement)).scalar_one_or_none()
        if message is None:
            raise LookupError("Callback outbox claim was not found")
        return message


class CallbackOutboxService:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    async def enqueue_event_for_lease(
        self,
        *,
        lease_id: str,
        run_id: str,
        request: RunEventRequest,
        idempotency_key: str | None = None,
    ) -> None:
        async with self._database.session() as session, session.begin():
            attempt = await SqlAlchemyAttemptRepository(session).get_by_lease_id(
                lease_id
            )
            if attempt is None:
                raise LookupError("Execution attempt for callback was not found")
            await self.enqueue_event(
                session,
                attempt=attempt,
                run_id=run_id,
                request=request,
                idempotency_key=idempotency_key,
            )

    async def enqueue_event(
        self,
        session: AsyncSession,
        *,
        attempt: RpaExecutionAttempt,
        run_id: str,
        request: RunEventRequest,
        idempotency_key: str | None = None,
    ) -> None:
        await SqlAlchemyCallbackOutboxRepository(session).enqueue(
            attempt=attempt,
            callback_type=CallbackType.EVENT,
            aggregate_id=run_id,
            endpoint_path=(f"/worker-api/runs/{quote(run_id, safe='')}/events"),
            payload=request.model_dump(
                mode="json",
                by_alias=False,
                exclude_none=True,
            ),
            idempotency_key=idempotency_key,
        )

    async def enqueue_finish(
        self,
        session: AsyncSession,
        *,
        attempt: RpaExecutionAttempt,
        run_id: str,
        request: RunFinishRequest,
    ) -> None:
        await SqlAlchemyCallbackOutboxRepository(session).enqueue(
            attempt=attempt,
            callback_type=CallbackType.FINISH,
            aggregate_id=run_id,
            endpoint_path=(f"/worker-api/runs/{quote(run_id, safe='')}/finish"),
            payload=request.model_dump(
                mode="json",
                by_alias=False,
                exclude_none=True,
            ),
            idempotency_key=f"rpa:{attempt.id}:finish",
        )


class CallbackOutboxDispatcher:
    def __init__(
        self,
        database: DatabaseManager,
        task_client: TaskWorkerApiClient,
        *,
        worker_id: str,
        poll_interval_seconds: float = 1.0,
        batch_size: int = 20,
        stale_lock_seconds: float = 60.0,
    ) -> None:
        self._database = database
        self._task_client = task_client
        self._locked_by = f"{worker_id}:outbox:{uuid4().hex}"
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self._stale_lock_seconds = stale_lock_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        now = datetime.now(UTC)
        async with self._database.session() as session, session.begin():
            await SqlAlchemyCallbackOutboxRepository(session).recover_stale(
                stale_before=now - timedelta(seconds=self._stale_lock_seconds),
                now=now,
            )
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run(),
            name=f"callback-outbox:{self._locked_by}",
        )

    async def stop(self, *, drain: bool = True) -> None:
        self._stop_event.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        if drain:
            while await self.dispatch_once() > 0:
                pass

    async def dispatch_once(self) -> int:
        now = datetime.now(UTC)
        async with self._database.session() as session, session.begin():
            repository = SqlAlchemyCallbackOutboxRepository(session)
            await repository.recover_stale(
                stale_before=now - timedelta(seconds=self._stale_lock_seconds),
                now=now,
            )
            deliveries = await repository.claim_ready(
                locked_by=self._locked_by,
                limit=self._batch_size,
                now=now,
            )
        for delivery in deliveries:
            try:
                await self._deliver(delivery)
            except Exception as exc:
                logger.warning(
                    "Callback outbox delivery failed",
                    extra={
                        "callbackId": str(delivery.id),
                        "callbackType": delivery.callback_type.value,
                        "sequenceNo": delivery.sequence_no,
                    },
                )
                await self._mark_failed(delivery.id, exc)
            else:
                await self._mark_sent(delivery.id)
        return len(deliveries)

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.dispatch_once()
            except Exception:
                logger.exception("Callback outbox dispatcher poll failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue

    async def _deliver(self, delivery: CallbackDelivery) -> None:
        if delivery.callback_type is CallbackType.EVENT:
            await self._task_client.event(
                delivery.aggregate_id,
                RunEventRequest.model_validate(delivery.payload),
                idempotency_key=delivery.idempotency_key,
            )
            return
        if delivery.callback_type is CallbackType.FINISH:
            await self._task_client.finish(
                delivery.aggregate_id,
                RunFinishRequest.model_validate(delivery.payload),
                idempotency_key=delivery.idempotency_key,
            )
            return
        raise ValueError("Unsupported callback outbox type")

    async def _mark_sent(self, message_id: UUID) -> None:
        now = datetime.now(UTC)
        async with self._database.session() as session, session.begin():
            await SqlAlchemyCallbackOutboxRepository(session).mark_sent(
                message_id,
                locked_by=self._locked_by,
                now=now,
            )

    async def _mark_failed(self, message_id: UUID, error: Exception) -> None:
        error_code = (
            error.code if isinstance(error, TaskApiError) else type(error).__name__
        )
        now = datetime.now(UTC)
        async with self._database.session() as session, session.begin():
            await SqlAlchemyCallbackOutboxRepository(session).mark_failed(
                message_id,
                locked_by=self._locked_by,
                error=str(error_code),
                now=now,
            )
