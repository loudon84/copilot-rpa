from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.task_api.auth import (
    TaskApiAuthProvider,
    build_task_auth_provider,
)
from nodeskclaw_rpa_engine.workers.errors import TaskApiError
from nodeskclaw_rpa_engine.workers.schemas import (
    ArtifactUploadTarget,
    ArtifactUploadUrlRequest,
    LeaseRenewal,
    LeaseRunCommand,
    RunArtifactCreate,
    RunEventRequest,
    RunFinishRequest,
    TaskEnvelope,
    WorkerLeaseRenewRequest,
    WorkerLeaseRequest,
    WorkerRegisterRequest,
)


class TaskWorkerApiClient:
    """当前 Task Worker API 的类型化兼容客户端。"""

    def __init__(
        self,
        settings: Settings,
        *,
        auth_provider: TaskApiAuthProvider | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._auth_provider = auth_provider or build_task_auth_provider(settings)
        self._artifact_upload_base_url = settings.task_artifact_upload_base_url
        self._client = httpx.AsyncClient(
            base_url=settings.task_api_base_url.rstrip("/") + "/",
            timeout=settings.task_api_timeout_seconds,
            transport=transport,
            trust_env=False,
        )

    async def check(self) -> None:
        await self._request_http("GET", "health", expect_envelope=False)

    async def register(self, request: WorkerRegisterRequest) -> Any:
        return await self._request_data(
            "POST",
            "worker-api/workers/register",
            json=request.model_dump(mode="json", by_alias=False),
        )

    async def heartbeat(self, worker_id: str) -> Any:
        return await self._request_data(
            "POST",
            f"worker-api/workers/{quote(worker_id, safe='')}/heartbeat",
        )

    async def lease(self, request: WorkerLeaseRequest) -> list[LeaseRunCommand]:
        data = await self._request_data(
            "POST",
            "worker-api/tasks/lease",
            json=request.model_dump(mode="json", by_alias=False),
        )
        if data is None:
            return []
        values = data if isinstance(data, list) else [data]
        try:
            return [LeaseRunCommand.model_validate(item) for item in values]
        except ValidationError as exc:
            raise TaskApiError(
                "TASK_LEASE_CONTRACT_INVALID",
                "Task lease response does not satisfy the Phase 3 contract",
            ) from exc

    async def renew(
        self,
        task_id: str,
        request: WorkerLeaseRenewRequest,
    ) -> LeaseRenewal:
        data = await self._request_data(
            "POST",
            f"worker-api/tasks/{quote(task_id, safe='')}/lease/renew",
            json=request.model_dump(mode="json", by_alias=False),
        )
        try:
            return LeaseRenewal.model_validate(data)
        except ValidationError as exc:
            raise TaskApiError(
                "TASK_RENEW_CONTRACT_INVALID",
                "Task lease renewal response is missing leaseExpiresAt",
            ) from exc

    async def event(
        self,
        run_id: str,
        request: RunEventRequest,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        return await self._request_data(
            "POST",
            f"worker-api/runs/{quote(run_id, safe='')}/events",
            json=request.model_dump(mode="json", by_alias=False),
            extra_headers=self._idempotency_headers(idempotency_key),
        )

    async def request_artifact_upload_url(
        self,
        request: ArtifactUploadUrlRequest,
    ) -> ArtifactUploadTarget:
        data = await self._request_data(
            "POST",
            "worker-api/artifacts/upload-url",
            json=request.model_dump(
                mode="json",
                by_alias=False,
                exclude_none=True,
            ),
        )
        try:
            return ArtifactUploadTarget.model_validate(data)
        except ValidationError as exc:
            raise TaskApiError(
                "TASK_ARTIFACT_UPLOAD_CONTRACT_INVALID",
                "Task artifact upload response is invalid",
            ) from exc

    async def upload_signed_artifact(
        self,
        upload_url: str,
        content: bytes,
        *,
        content_type: str,
    ) -> None:
        resolved_upload_url = self._resolve_artifact_upload_url(upload_url)
        try:
            response = await self._client.put(
                resolved_upload_url,
                content=content,
                headers={"Content-Type": content_type},
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TaskApiError(
                "ARTIFACT_UPLOAD_TIMEOUT",
                "Artifact upload timed out",
            ) from exc
        except httpx.HTTPError as exc:
            raise TaskApiError(
                "ARTIFACT_UPLOAD_FAILED",
                "Artifact upload failed",
            ) from exc

    def _resolve_artifact_upload_url(self, upload_url: str) -> str:
        if self._artifact_upload_base_url is None:
            return upload_url

        parsed_upload_url = urlsplit(upload_url)
        if parsed_upload_url.scheme not in {
            "http",
            "https",
        } or parsed_upload_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return upload_url

        parsed_base_url = urlsplit(self._artifact_upload_base_url)
        return urlunsplit(
            (
                parsed_base_url.scheme,
                parsed_base_url.netloc,
                parsed_upload_url.path,
                parsed_upload_url.query,
                parsed_upload_url.fragment,
            )
        )

    async def artifact(self, run_id: str, request: RunArtifactCreate) -> Any:
        return await self._request_data(
            "POST",
            f"worker-api/runs/{quote(run_id, safe='')}/artifacts",
            json=request.model_dump(
                mode="json",
                by_alias=False,
                exclude_none=True,
            ),
        )

    async def finish(
        self,
        run_id: str,
        request: RunFinishRequest,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        return await self._request_data(
            "POST",
            f"worker-api/runs/{quote(run_id, safe='')}/finish",
            json=request.model_dump(mode="json", by_alias=False, exclude_none=True),
            extra_headers=self._idempotency_headers(idempotency_key),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request_data(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self._request_http(
            method,
            path,
            json=json,
            extra_headers=extra_headers,
        )
        try:
            envelope = TaskEnvelope.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise TaskApiError(
                "TASK_RESPONSE_INVALID",
                "Task API returned an invalid response envelope",
            ) from exc
        if not self._is_success_code(envelope.code):
            error_code = (
                envelope.error_code or envelope.message_key or "TASK_REQUEST_REJECTED"
            )
            raise TaskApiError(
                str(error_code),
                envelope.message or "Task API rejected the Worker request",
            )
        return envelope.data

    async def _request_http(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        expect_envelope: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        del expect_envelope  # 表明健康检查端点使用不同的响应契约
        try:
            headers = await self._auth_provider.headers()
            if extra_headers:
                headers = {**headers, **extra_headers}
            response = await self._client.request(
                method,
                path,
                json=json,
                headers=headers,
            )
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise TaskApiError(
                "TASK_API_TIMEOUT",
                "Task API request timed out",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise TaskApiError(
                "TASK_API_HTTP_ERROR",
                f"Task API returned HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise TaskApiError(
                "TASK_API_UNAVAILABLE",
                "Task API is unavailable",
            ) from exc

    @staticmethod
    def _idempotency_headers(value: str | None) -> dict[str, str] | None:
        return {"Idempotency-Key": value} if value is not None else None

    @staticmethod
    def _is_success_code(code: int | str) -> bool:
        if isinstance(code, int):
            return code in {0, 200}
        return code.upper() in {"0", "200", "OK", "SUCCESS"}
