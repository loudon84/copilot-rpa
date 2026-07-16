from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from nodeskclaw_rpa_engine.api.app import create_app
from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.workers.errors import WorkerError
from nodeskclaw_rpa_engine.workers.schemas import (
    WorkerListResponse,
    WorkerResponse,
    WorkerStatus,
)
from nodeskclaw_rpa_engine.workers.service import WorkerQueryService


def response(status: WorkerStatus = WorkerStatus.ONLINE) -> WorkerResponse:
    now = datetime.now(UTC)
    return WorkerResponse(
        id=uuid4(),
        worker_id="server-worker-phase3-smoke",
        worker_type="SERVER_WORKER",
        device_name="test-device",
        status=status,
        capabilities=["PLAYWRIGHT_CDP", "BROWSER_SESSION_MANAGED"],
        tags=["phase3"],
        app_version="0.3.0",
        agent_version="0.3.0",
        os="Windows",
        max_concurrent_runs=1,
        current_task_count=0,
        browser_count=0,
        registered_at=now,
        last_heartbeat_at=now,
        updated_at=now,
    )


class FakeWorkerService:
    async def list_workers(self, **kwargs) -> WorkerListResponse:
        assert kwargs["capability"] == "PLAYWRIGHT_CDP"
        return WorkerListResponse(items=[response()], total=1, limit=50, offset=0)

    async def get_worker(self, worker_id: str) -> WorkerResponse:
        if worker_id != "server-worker-phase3-smoke":
            raise WorkerError(
                "WORKER_NOT_FOUND",
                "Worker was not found",
                status_code=404,
            )
        return response()


async def test_worker_list_and_detail_are_read_only_and_require_actor() -> None:
    app = create_app(
        Settings(_env_file=None, app_env="test"),
        worker_query_service=cast(WorkerQueryService, FakeWorkerService()),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            missing_header = await client.get("/api/v1/workers")
            listed = await client.get(
                "/api/v1/workers?capability=PLAYWRIGHT_CDP",
                headers={"X-Actor-Id": "tester"},
            )
            detail = await client.get(
                "/api/v1/workers/server-worker-phase3-smoke",
                headers={"X-Actor-Id": "tester"},
            )
            missing = await client.get(
                "/api/v1/workers/unknown",
                headers={"X-Actor-Id": "tester"},
            )

    assert missing_header.status_code == 422
    assert listed.status_code == 200
    assert listed.json()["items"][0]["workerId"] == "server-worker-phase3-smoke"
    assert detail.status_code == 200
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "WORKER_NOT_FOUND"


async def test_worker_api_is_unavailable_without_database() -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.get(
                "/api/v1/workers", headers={"X-Actor-Id": "tester"}
            )
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "WORKER_REGISTRY_UNAVAILABLE"


def test_stale_online_worker_is_reported_offline() -> None:
    old = datetime.now(UTC) - timedelta(seconds=46)
    assert (
        WorkerResponse.effective_status("ONLINE", old, 45)
        is WorkerStatus.OFFLINE
    )
