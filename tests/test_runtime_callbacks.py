from __future__ import annotations

from unittest.mock import AsyncMock

from nodeskclaw_rpa_engine.runtime.callbacks import TaskRuntimeEventSink
from nodeskclaw_rpa_engine.workers.outbox import CallbackOutboxService


async def test_runtime_event_sink_persists_to_outbox() -> None:
    outbox = AsyncMock(spec=CallbackOutboxService)
    sink = TaskRuntimeEventSink(
        outbox,
        lease_id="lease-1",
        run_id="run-1",
        worker_id="worker-1",
    )

    await sink.emit(
        "RUNTIME_STARTED",
        level="INFO",
        message="started",
        payload={"step": 1},
    )

    outbox.enqueue_event_for_lease.assert_awaited_once()
    call = outbox.enqueue_event_for_lease.await_args
    assert call.kwargs["lease_id"] == "lease-1"
    assert call.kwargs["run_id"] == "run-1"
    assert call.kwargs["request"].worker_id == "worker-1"
    assert call.kwargs["request"].type == "RUNTIME_STARTED"


async def test_runtime_event_sink_rejects_missing_outbox() -> None:
    sink = TaskRuntimeEventSink(
        None,
        lease_id="lease-1",
        run_id="run-1",
        worker_id="worker-1",
    )

    try:
        await sink.emit("EVENT", level="INFO", message="message")
    except RuntimeError as exc:
        assert "outbox" in str(exc)
    else:
        raise AssertionError("missing outbox must not fall back to direct HTTP")
