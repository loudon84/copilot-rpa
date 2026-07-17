from __future__ import annotations

from pathlib import Path

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.core.health import (
    DependencyState,
    ReadinessService,
)
from nodeskclaw_rpa_engine.runtime.filesystem import RuntimeFilesystemProbe


class HealthyProbe:
    async def check(self) -> None:
        return None


class FailingProbe:
    async def check(self) -> None:
        raise ConnectionError("must-not-leak-host-or-credentials")


async def test_disabled_dependencies_do_not_block_readiness() -> None:
    settings = Settings(_env_file=None, app_env="test")
    response, is_ready = await ReadinessService(settings).readiness()

    assert is_ready is True
    assert response.status == "ready"
    assert response.dependencies["database"].state is DependencyState.DISABLED
    assert (
        response.dependencies["objectStorage"].state
        is DependencyState.DISABLED
    )
    assert response.dependencies["taskApi"].state is DependencyState.NOT_CHECKED
    runtime_filesystem = response.dependencies["runtimeFilesystem"]
    assert runtime_filesystem.state is DependencyState.DISABLED
    assert runtime_filesystem.required is False


async def test_runtime_filesystem_probe_checks_both_directories(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    work_dir = tmp_path / "work"
    settings = Settings(
        _env_file=None,
        app_env="test",
        minio_enabled=True,
        minio_endpoint_url="http://object-storage.test",
        minio_access_key="test-access-key",
        minio_secret_key="test-secret-key",
        runtime_enabled=True,
        runtime_cache_dir=cache_dir,
        runtime_work_dir=work_dir,
    )

    response, is_ready = await ReadinessService(
        settings,
        object_storage_probe=HealthyProbe(),
        task_api_probe=HealthyProbe(),
        runtime_filesystem_probe=RuntimeFilesystemProbe(cache_dir, work_dir),
    ).readiness()

    dependency = response.dependencies["runtimeFilesystem"]
    assert is_ready is True
    assert dependency.required is True
    assert dependency.state is DependencyState.HEALTHY
    assert cache_dir.is_dir()
    assert work_dir.is_dir()
    assert list(cache_dir.iterdir()) == []
    assert list(work_dir.iterdir()) == []


async def test_enabled_healthy_dependency_is_required() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_enabled=True,
        database_url="postgresql+asyncpg://user:secret@db/nodeskclaw_task",
    )
    response, is_ready = await ReadinessService(
        settings,
        database_probe=HealthyProbe(),
    ).readiness()

    assert is_ready is True
    assert response.dependencies["database"].required is True
    assert response.dependencies["database"].state is DependencyState.HEALTHY


async def test_probe_failure_is_safe_and_blocks_readiness() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_enabled=True,
        database_url="postgresql+asyncpg://user:secret@db/nodeskclaw_task",
    )
    response, is_ready = await ReadinessService(
        settings,
        database_probe=FailingProbe(),
    ).readiness()

    dependency = response.dependencies["database"]
    assert is_ready is False
    assert response.status == "not_ready"
    assert dependency.state is DependencyState.UNHEALTHY
    assert dependency.detail == "check_failed:ConnectionError"
    assert "must-not-leak" not in response.model_dump_json()


async def test_task_api_is_required_when_worker_is_enabled() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_enabled=True,
        database_url="postgresql+asyncpg://user:secret@db/nodeskclaw_task",
        worker_enabled=True,
    )
    healthy, is_ready = await ReadinessService(
        settings,
        database_probe=HealthyProbe(),
        task_api_probe=HealthyProbe(),
    ).readiness()
    failed, failed_is_ready = await ReadinessService(
        settings,
        database_probe=HealthyProbe(),
        task_api_probe=FailingProbe(),
    ).readiness()

    assert is_ready is True
    assert healthy.dependencies["taskApi"].required is True
    assert healthy.dependencies["taskApi"].state is DependencyState.HEALTHY
    assert failed_is_ready is False
    assert failed.dependencies["taskApi"].state is DependencyState.UNHEALTHY


async def test_task_api_is_required_when_only_runtime_is_enabled() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        minio_enabled=True,
        minio_endpoint_url="http://object-storage.test",
        minio_access_key="test-access-key",
        minio_secret_key="test-secret-key",
        runtime_enabled=True,
    )
    healthy, is_ready = await ReadinessService(
        settings,
        object_storage_probe=HealthyProbe(),
        task_api_probe=HealthyProbe(),
        runtime_filesystem_probe=HealthyProbe(),
    ).readiness()
    failed, failed_is_ready = await ReadinessService(
        settings,
        object_storage_probe=HealthyProbe(),
        task_api_probe=FailingProbe(),
        runtime_filesystem_probe=HealthyProbe(),
    ).readiness()

    assert is_ready is True
    assert healthy.dependencies["taskApi"].required is True
    assert healthy.dependencies["taskApi"].state is DependencyState.HEALTHY
    assert failed_is_ready is False
    assert failed.dependencies["taskApi"].state is DependencyState.UNHEALTHY
