from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.workers.errors import TaskApiError
from nodeskclaw_rpa_engine.workers.schemas import (
    ArtifactUploadUrlRequest,
    AttemptStatus,
    RunArtifactCreate,
    RunConfig,
    RunEventRequest,
    RunFinishRequest,
    WorkerLeaseRenewRequest,
    WorkerLeaseRequest,
    WorkerRegisterRequest,
)
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient


def worker_settings() -> Settings:
    return Settings(_env_file=None, app_env="test", task_api_base_url="http://task/api")


def test_artifact_upload_url_request_requires_run_id() -> None:
    with pytest.raises(ValidationError):
        ArtifactUploadUrlRequest.model_validate(
            {
                "worker_id": "worker-1",
                "task_id": "task-1",
                "name": "evidence.png",
                "mime_type": "image/png",
            }
        )


def test_run_config_requires_portal_url() -> None:
    with pytest.raises(ValidationError):
        RunConfig.model_validate(
            {
                "browserSession": {
                    "mode": "MANAGED",
                    "headless": True,
                    "channel": "chrome",
                    "profileRef": None,
                    "cdpEndpointRef": None,
                    "closePolicy": "CLOSE_ON_FINISH",
                }
            }
        )


async def test_task_client_handles_snake_requests_and_camel_lease_response() -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    expires_at = datetime.now(UTC) + timedelta(minutes=2)

    def handler(request: httpx.Request) -> httpx.Response:
        body = None
        if request.content:
            body = __import__("json").loads(request.content)
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/tasks/lease"):
            data: object = {
                "taskId": "task-1",
                "runId": "run-1",
                "leaseId": "lease-1",
                "workflowBindingId": "binding-1",
                "portalAccountId": "portal-1",
                "rpaFlowId": "rpa_flow_test",
                "input": {"poNo": "PO-1"},
                "tenantId": "tenant-1",
                "workflowTemplateId": "template-1",
                "workflowCode": "fetch_po",
                "rpaEngineType": "PLAYWRIGHT_CDP",
                "rpaFlowVersion": "1.2.3",
                "credentialRef": "credential-1",
                "config": {
                    "portalUrl": "http://mock.test",
                    "browserSession": {
                        "mode": "MANAGED",
                        "headless": True,
                        "channel": "chromium",
                        "profileRef": None,
                        "cdpEndpointRef": None,
                        "closePolicy": "ALWAYS",
                    }
                },
                "leaseExpiresAt": expires_at.isoformat(),
            }
        elif request.url.path.endswith("/lease/renew"):
            data = {"leaseExpiresAt": expires_at.isoformat()}
        else:
            data = {"accepted": True}
        return httpx.Response(200, json={"code": 200, "data": data})

    client = TaskWorkerApiClient(
        worker_settings(),
        transport=httpx.MockTransport(handler),
    )
    await client.register(
        WorkerRegisterRequest(
            worker_id="worker-1",
            worker_type="SERVER_WORKER",
            device_name="test-device",
            capabilities=["PLAYWRIGHT_CDP"],
        )
    )
    await client.heartbeat("worker-1")
    leases = await client.lease(
        WorkerLeaseRequest(
            worker_id="worker-1",
            capabilities=["PLAYWRIGHT_CDP"],
            limit=1,
        )
    )
    renewal = await client.renew(
        "task-1",
        WorkerLeaseRenewRequest(worker_id="worker-1", lease_id="lease-1"),
    )
    await client.event(
        "run-1",
        RunEventRequest(
            worker_id="worker-1",
            type="RUN_STARTED",
            message="started",
        ),
    )
    await client.finish(
        "run-1",
        RunFinishRequest(status=AttemptStatus.SUCCESS),
    )
    await client.close()

    assert leases[0].rpa_flow_version == "1.2.3"
    assert leases[0].config.portal_url == "http://mock.test"
    assert leases[0].config.browser_session.mode == "MANAGED"
    assert renewal.lease_expires_at == expires_at
    assert calls[0][2] == {
        "worker_id": "worker-1",
        "worker_type": "SERVER_WORKER",
        "device_name": "test-device",
        "capabilities": ["PLAYWRIGHT_CDP"],
        "app_version": None,
        "agent_version": None,
        "os": None,
    }
    assert calls[2][2] == {
        "worker_id": "worker-1",
        "capabilities": ["PLAYWRIGHT_CDP"],
        "limit": 1,
    }
    assert calls[-1][2] == {"status": "SUCCESS"}


async def test_task_client_rejects_business_error_envelope() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "code": 409,
                "errorCode": "WORKER_REJECTED",
                "message": "rejected",
                "data": None,
            },
        )
    )
    client = TaskWorkerApiClient(worker_settings(), transport=transport)
    with pytest.raises(TaskApiError, match="rejected") as captured:
        await client.heartbeat("worker-1")
    await client.close()
    assert captured.value.code == "WORKER_REJECTED"


async def test_task_client_rejects_incomplete_phase_3_lease() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "taskId": "task-1",
                    "runId": "run-1",
                    "leaseId": "lease-1",
                    "rpaFlowId": "flow-1",
                    "input": {},
                },
            },
        )
    )
    client = TaskWorkerApiClient(worker_settings(), transport=transport)
    with pytest.raises(TaskApiError) as captured:
        await client.lease(
            WorkerLeaseRequest(
                worker_id="worker-1",
                capabilities=["PLAYWRIGHT_CDP"],
            )
        )
    await client.close()
    assert captured.value.code == "TASK_LEASE_CONTRACT_INVALID"


async def test_task_client_maps_timeout_without_leaking_url() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private-host-detail", request=request)

    client = TaskWorkerApiClient(
        worker_settings(), transport=httpx.MockTransport(timeout)
    )
    with pytest.raises(TaskApiError) as captured:
        await client.heartbeat("worker-1")
    await client.close()
    assert captured.value.code == "TASK_API_TIMEOUT"
    assert "private-host" not in captured.value.message


async def test_task_client_uploads_artifact_with_mixed_task_contract() -> None:
    calls: list[tuple[str, str, dict[str, object] | bytes | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "storage.test":
            calls.append((request.method, request.url.path, request.content))
            return httpx.Response(200)
        body = __import__("json").loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/artifacts/upload-url"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "uploadUrl": (
                            "http://storage.test/upload?X-Amz-Signature=secret"
                        ),
                        "storageKey": "artifacts/run-1/evidence.png",
                    },
                },
            )
        return httpx.Response(200, json={"code": 0, "data": {"id": "a-1"}})

    client = TaskWorkerApiClient(
        worker_settings(),
        transport=httpx.MockTransport(handler),
    )
    target = await client.request_artifact_upload_url(
        ArtifactUploadUrlRequest(
            worker_id="worker-1",
            task_id="task-1",
            run_id="run-1",
            name="evidence.png",
            mime_type="image/png",
        )
    )
    await client.upload_signed_artifact(
        target.upload_url,
        b"image",
        content_type="image/png",
    )
    await client.artifact(
        "run-1",
        RunArtifactCreate(
            type="screenshot",
            name="evidence.png",
            storage_key=target.storage_key,
            size=5,
            mime_type="image/png",
        ),
    )
    await client.close()

    assert calls[0][:2] == ("POST", "/api/worker-api/artifacts/upload-url")
    assert calls[0][2] == {
        "worker_id": "worker-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "name": "evidence.png",
        "mime_type": "image/png",
    }
    assert calls[1] == ("PUT", "/upload", b"image")
    assert calls[2][2] == {
        "type": "screenshot",
        "name": "evidence.png",
        "storage_key": "artifacts/run-1/evidence.png",
        "size": 5,
        "mime_type": "image/png",
    }
