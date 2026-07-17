from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from nodeskclaw_rpa_engine.core.config import Settings


class DependencyState(StrEnum):
    DISABLED = "disabled"
    NOT_CHECKED = "not_checked"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class DependencyHealth(BaseModel):
    state: DependencyState
    required: bool
    detail: str | None = None


class LivenessResponse(BaseModel):
    service: str
    version: str
    environment: str
    status: str = "alive"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    service: str
    version: str
    environment: str
    status: str
    dependencies: dict[str, DependencyHealth]


class DependencyProbe(Protocol):
    async def check(self) -> None: ...


class ReadinessService:
    def __init__(
        self,
        settings: Settings,
        *,
        database_probe: DependencyProbe | None = None,
        object_storage_probe: DependencyProbe | None = None,
        task_api_probe: DependencyProbe | None = None,
        runtime_filesystem_probe: DependencyProbe | None = None,
    ) -> None:
        self._settings = settings
        self._database_probe = database_probe
        self._object_storage_probe = object_storage_probe
        self._task_api_probe = task_api_probe
        self._runtime_filesystem_probe = runtime_filesystem_probe

    def liveness(self) -> LivenessResponse:
        return LivenessResponse(
            service=self._settings.app_name,
            version=self._settings.app_version,
            environment=self._settings.app_env.value,
        )

    async def readiness(self) -> tuple[ReadinessResponse, bool]:
        database = await self._dependency_status(
            enabled=self._settings.database_enabled,
            probe=self._database_probe,
        )
        object_storage = await self._dependency_status(
            enabled=self._settings.minio_enabled,
            probe=self._object_storage_probe,
        )
        task_api = (
            await self._dependency_status(
                enabled=True,
                probe=self._task_api_probe,
            )
            if (
                self._settings.worker_enabled
                or self._settings.runtime_enabled
            )
            else DependencyHealth(
                state=DependencyState.NOT_CHECKED,
                required=False,
                detail="worker_disabled",
            )
        )
        runtime_filesystem = await self._dependency_status(
            enabled=self._settings.runtime_enabled,
            probe=self._runtime_filesystem_probe,
        )
        dependencies = {
            "database": database,
            "objectStorage": object_storage,
            "taskApi": task_api,
            "runtimeFilesystem": runtime_filesystem,
        }
        is_ready = all(
            not item.required or item.state is DependencyState.HEALTHY
            for item in dependencies.values()
        )
        return (
            ReadinessResponse(
                service=self._settings.app_name,
                version=self._settings.app_version,
                environment=self._settings.app_env.value,
                status="ready" if is_ready else "not_ready",
                dependencies=dependencies,
            ),
            is_ready,
        )

    async def _dependency_status(
        self,
        *,
        enabled: bool,
        probe: DependencyProbe | None,
    ) -> DependencyHealth:
        if not enabled:
            return DependencyHealth(
                state=DependencyState.DISABLED,
                required=False,
            )
        if probe is None:
            return DependencyHealth(
                state=DependencyState.UNHEALTHY,
                required=True,
                detail="probe_not_configured",
            )
        try:
            await probe.check()
        except Exception as exc:  # readiness 必须返回安全、稳定的响应
            return DependencyHealth(
                state=DependencyState.UNHEALTHY,
                required=True,
                detail=f"check_failed:{type(exc).__name__}",
            )
        return DependencyHealth(
            state=DependencyState.HEALTHY,
            required=True,
        )
