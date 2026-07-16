from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nodeskclaw_rpa_engine.api.app import create_app
from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.core.health import ReadinessService
from nodeskclaw_rpa_engine.flows.schemas import (
    ActorContext,
    BindingValidationRequest,
    BindingValidationResponse,
)
from nodeskclaw_rpa_engine.flows.service import FlowRegistryService


class FailingProbe:
    async def check(self) -> None:
        raise TimeoutError("private dependency detail")


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
        "version": "0.5.0",
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
    }


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
