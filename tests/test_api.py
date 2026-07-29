from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch

import nodeskclaw_rpa_engine.api.app as app_module
from nodeskclaw_rpa_engine.api.app import create_app
from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.core.health import ReadinessService
from nodeskclaw_rpa_engine.flows.schemas import (
    ActorContext,
    BindingValidationRequest,
    BindingValidationResponse,
    FlowVersionResponse,
    FlowVersionStatus,
)
from nodeskclaw_rpa_engine.flows.service import FlowRegistryService
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient


class FailingProbe:
    async def check(self) -> None:
        raise TimeoutError("private dependency detail")


class HealthyClosableProbe:
    async def check(self) -> None:
        return None

    async def close(self) -> None:
        return None


@asynccontextmanager
async def api_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client


async def test_live_and_ready_endpoints_with_offline_defaults() -> None:
    settings = Settings(_env_file=None, app_env="test")
    async with api_client(create_app(settings)) as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {
        "service": "nodeskclaw-rpa-engine",
        "version": "0.6.0",
        "environment": "test",
        "status": "alive",
    }
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["dependencies"]["database"]["state"] == "disabled"
    assert (
        ready.json()["dependencies"]["objectStorage"]["state"] == "disabled"
    )


async def test_ready_returns_503_for_failed_required_dependency() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_enabled=True,
        database_url="postgresql+asyncpg://user:secret@db/nodeskclaw_task",
    )
    readiness = ReadinessService(settings, database_probe=FailingProbe())

    async with api_client(
        create_app(settings, readiness_service=readiness)
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["dependencies"]["database"] == {
        "state": "unhealthy",
        "required": True,
        "detail": "check_failed:TimeoutError",
    }
    assert "private dependency detail" not in response.text


async def test_ready_returns_503_for_unusable_runtime_filesystem(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    blocked_cache_dir = tmp_path / "private-runtime-cache"
    blocked_cache_dir.write_text("not-a-directory", encoding="utf-8")
    object_storage = HealthyClosableProbe()
    task_api = HealthyClosableProbe()
    monkeypatch.setattr(
        app_module,
        "build_object_storage",
        lambda _: object_storage,
    )
    settings = Settings(
        _env_file=None,
        app_env="test",
        minio_enabled=True,
        minio_endpoint_url="http://object-storage.test",
        minio_access_key="test-access-key",
        minio_secret_key="test-secret-key",
        runtime_enabled=True,
        runtime_cache_dir=blocked_cache_dir,
        runtime_work_dir=tmp_path / "work",
    )
    app = create_app(
        settings,
        task_client=cast(TaskWorkerApiClient, task_api),
    )

    async with api_client(app) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["dependencies"]["runtimeFilesystem"] == {
        "state": "unhealthy",
        "required": True,
        "detail": "check_failed:FileExistsError",
    }
    assert "private-runtime-cache" not in response.text


async def test_unknown_route_uses_standard_404_response() -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    async with api_client(app) as client:
        response = await client.get("/not-found")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


async def test_flow_registry_returns_safe_503_when_dependencies_disabled() -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))
    async with api_client(app) as client:
        response = await client.get(
            "/api/v1/flows",
            headers={"X-Actor-Id": "test-actor"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "FLOW_REGISTRY_UNAVAILABLE",
            "message": (
                "Flow Registry requires enabled database and object storage "
                "dependencies"
            ),
            "details": None,
        }
    }


async def test_flow_registry_requires_test_actor_header_without_echoing_input() -> None:
    service = cast(FlowRegistryService, object())
    app = create_app(
        Settings(_env_file=None, app_env="test"),
        flow_registry_service=service,
    )
    async with api_client(app) as client:
        response = await client.get("/api/v1/flows")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert response.json()["error"]["details"][0]["field"] == "header.X-Actor-Id"
    assert "input" not in response.text


async def test_binding_validation_static_route_precedes_uuid_route() -> None:
    class FakeFlowService:
        async def validate_binding(
            self,
            actor: ActorContext,
            request: BindingValidationRequest,
        ) -> BindingValidationResponse:
            assert actor.actor_id == "test-actor"
            assert request.rpa_flow_id == "rpa_flow_test"
            return BindingValidationResponse(
                valid=False,
                reason_code="FLOW_VERSION_NOT_FOUND",
                version=None,
            )

    service = cast(FlowRegistryService, FakeFlowService())
    app = create_app(
        Settings(_env_file=None, app_env="test"),
        flow_registry_service=service,
    )
    async with api_client(app) as client:
        response = await client.post(
            "/api/v1/flow-versions/validate-binding",
            headers={"X-Actor-Id": "test-actor"},
            json={
                "rpaFlowId": "rpa_flow_test",
                "rpaFlowVersion": "1.0.0",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "valid": False,
        "reasonCode": "FLOW_VERSION_NOT_FOUND",
        "version": None,
        "rpaFlowVersionId": None,
        "packageChecksum": None,
        "checksum": None,
    }


def test_binding_validation_exposes_temporary_task_snapshot_aliases() -> None:
    flow_version_id = UUID("ffd5687a-b213-4f10-9265-1813addb48ec")
    checksum = (
        "sha256:4950d0cc1302b11af330ef0abea5b2a603a310210b717d898c9981e64b83fd37"
    )
    timestamp = datetime(2026, 7, 16, tzinfo=UTC)
    version = FlowVersionResponse(
        rpa_flow_version_id=flow_version_id,
        rpa_flow_id="rpa_flow_mock_srm_fetch_po",
        version="1.1.0",
        status=FlowVersionStatus.PUBLISHED,
        engine_type="PLAYWRIGHT_CDP",
        entrypoint="flow.py:run",
        manifest={},
        supported_workflow_codes=["srm_fetch_po"],
        supported_portal_types=["MOCK_SRM"],
        input_schema=[],
        capabilities=["PLAYWRIGHT_CDP"],
        minimum_engine_version="0.5.0",
        package_uri="https://object-storage.example/flow.zip",
        package_size_bytes=3979,
        package_checksum=checksum,
        created_by="test-actor",
        created_at=timestamp,
        published_at=timestamp,
        updated_at=timestamp,
    )

    response = BindingValidationResponse(
        valid=True,
        reason_code=None,
        version=version,
    ).model_dump(mode="json", by_alias=True)

    assert response["version"]["rpaFlowVersionId"] == str(flow_version_id)
    assert response["version"]["packageChecksum"] == checksum
    assert response["rpaFlowVersionId"] == str(flow_version_id)
    assert response["packageChecksum"] == checksum
    assert response["checksum"] == checksum


def test_phase_3_openapi_exposes_flow_registry_and_worker_routes() -> None:
    app = create_app(Settings(_env_file=None, app_env="test"))

    assert set(app.openapi()["paths"]) == {
        "/health/live",
        "/health/ready",
        "/api/v1/flows",
        "/api/v1/flows/packages",
        "/api/v1/flows/{rpa_flow_id}",
        "/api/v1/flows/{rpa_flow_id}/versions",
        "/api/v1/flows/{rpa_flow_id}/disable",
        "/api/v1/flows/{rpa_flow_id}/rollback",
        "/api/v1/flow-versions/validate-binding",
        "/api/v1/flow-versions/{flow_version_id}",
        "/api/v1/flow-versions/{flow_version_id}/validate",
        "/api/v1/flow-versions/{flow_version_id}/publish",
        "/api/v1/flow-versions/{flow_version_id}/deprecate",
        "/api/v1/flow-versions/{flow_version_id}/disable",
        "/api/v1/flow-versions/{flow_version_id}/package",
        "/api/v1/workers",
        "/api/v1/workers/{worker_id}",
    }
