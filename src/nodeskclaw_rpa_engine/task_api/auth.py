from __future__ import annotations

from typing import Protocol

from pydantic import SecretStr

from nodeskclaw_rpa_engine.core.config import Settings, TaskAuthMode


class TaskApiAuthProvider(Protocol):
    async def headers(self) -> dict[str, str]: ...


class NoAuthProvider:
    """当前测试环境 Worker API 的临时兼容实现。"""

    async def headers(self) -> dict[str, str]:
        return {}


class ServiceAccountAuthProvider:
    """仅保存凭据；Token 交换功能按计划延后实现。"""

    def __init__(self, client_id: str, client_secret: SecretStr) -> None:
        self._client_id = client_id
        self._client_secret = client_secret

    async def headers(self) -> dict[str, str]:
        raise NotImplementedError(
            "Service-account token exchange is not implemented yet"
        )


def build_task_auth_provider(settings: Settings) -> TaskApiAuthProvider:
    if settings.task_auth_mode is TaskAuthMode.NONE:
        return NoAuthProvider()
    if settings.task_client_id is None or settings.task_client_secret is None:
        raise ValueError("Task service-account credentials are not configured")
    return ServiceAccountAuthProvider(
        settings.task_client_id,
        settings.task_client_secret,
    )
