from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from nodeskclaw_rpa_engine.core.config import Settings


def test_environment_variables_override_dotenv(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_PORT=4700\nLOG_LEVEL=warning\n", encoding="utf-8")
    monkeypatch.setenv("APP_PORT", "4800")

    settings = Settings(_env_file=env_file)

    assert settings.app_port == 4800
    assert settings.log_level == "WARNING"


def test_enabled_database_requires_url() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None, database_enabled=True)


def test_database_url_is_restricted_to_engine_driver_and_task_database() -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg"):
        Settings(
            _env_file=None,
            database_url="postgresql://user:secret@db/nodeskclaw_task",
        )
    with pytest.raises(ValidationError, match="nodeskclaw_task"):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://user:secret@db/other_database",
        )


def test_enabled_minio_requires_all_connection_settings() -> None:
    with pytest.raises(ValidationError, match="MINIO_ENDPOINT_URL"):
        Settings(_env_file=None, minio_enabled=True)


def test_schema_is_fixed_to_engine_boundary() -> None:
    with pytest.raises(ValidationError, match="rpa_engine"):
        Settings(_env_file=None, database_schema="public")


def test_endpoint_must_not_embed_credentials() -> None:
    with pytest.raises(ValidationError, match="must not contain credentials"):
        Settings(
            _env_file=None,
            task_api_base_url="http://user:password@example.test/api",
        )

    with pytest.raises(ValidationError, match="must not contain credentials"):
        Settings(
            _env_file=None,
            task_artifact_upload_base_url=(
                "http://user:password@storage-proxy.test"
            ),
        )


def test_artifact_upload_base_url_is_optional_and_empty_means_disabled() -> None:
    assert Settings(_env_file=None).task_artifact_upload_base_url is None
    assert (
        Settings(
            _env_file=None,
            task_artifact_upload_base_url="",
        ).task_artifact_upload_base_url
        is None
    )


def test_public_snapshot_does_not_expose_secrets() -> None:
    settings = Settings(
        _env_file=None,
        database_enabled=True,
        database_url="postgresql+asyncpg://task_user:db-secret@db/nodeskclaw_task",
        task_client_secret="task-secret",
    )

    serialized = json.dumps(settings.public_snapshot())

    assert "db-secret" not in serialized
    assert "task-secret" not in serialized
    assert "task_user" not in serialized
    assert "taskApiBaseUrl" not in settings.public_snapshot()
    assert "publicBaseUrl" not in settings.public_snapshot()


def test_worker_defaults_are_disabled_and_phase_3_values_are_fixed() -> None:
    settings = Settings(_env_file=None)

    assert settings.worker_enabled is False
    assert settings.worker_lease_enabled is False
    assert settings.worker_max_concurrent_runs == 1
    assert settings.worker_heartbeat_interval_seconds == 15
    assert settings.worker_poll_interval_seconds == 5
    assert settings.worker_lease_renew_interval_seconds == 20
    assert settings.worker_offline_threshold_seconds == 45
    assert settings.worker_capabilities == [
        "PLAYWRIGHT_CDP",
        "BROWSER_SESSION_MANAGED",
        "SCREENSHOT",
        "DOWNLOAD",
    ]


def test_enabled_worker_requires_database_and_technical_capabilities() -> None:
    with pytest.raises(ValidationError, match="DATABASE_ENABLED"):
        Settings(_env_file=None, worker_enabled=True)
    with pytest.raises(ValidationError, match="BROWSER_SESSION_MANAGED"):
        Settings(
            _env_file=None,
            database_enabled=True,
            database_url=(
                "postgresql+asyncpg://user:secret@db/nodeskclaw_task"
            ),
            worker_enabled=True,
            worker_capabilities=["PLAYWRIGHT_CDP"],
        )


def test_lease_cannot_be_enabled_without_worker() -> None:
    with pytest.raises(ValidationError, match="WORKER_ENABLED"):
        Settings(_env_file=None, worker_lease_enabled=True)


def test_runtime_defaults_are_safe_and_disabled() -> None:
    settings = Settings(_env_file=None)

    assert settings.runtime_enabled is False
    assert settings.runtime_timeout_seconds == 900
    assert settings.runtime_max_retries == 2
    assert settings.runtime_trace_mode.value == "ON_FAILURE"
    assert settings.runtime_output_max_bytes == 1024 * 1024

    with pytest.raises(ValidationError):
        Settings(_env_file=None, runtime_output_max_bytes=1023)


def test_enabled_runtime_requires_object_storage() -> None:
    with pytest.raises(ValidationError, match="MINIO_ENABLED"):
        Settings(_env_file=None, runtime_enabled=True)


def test_runtime_cache_and_work_directories_must_differ() -> None:
    with pytest.raises(ValidationError, match="must be different"):
        Settings(
            _env_file=None,
            runtime_cache_dir="runtime-cache/same",
            runtime_work_dir="runtime-cache/same",
        )


def test_mock_credential_resolver_requires_scoped_environment_settings() -> None:
    with pytest.raises(ValidationError, match="MOCK_SRM_CREDENTIAL_REF"):
        Settings(_env_file=None, credential_resolver_mode="mock_env")

    settings = Settings(
        _env_file=None,
        app_env="test",
        credential_resolver_mode="mock_env",
        mock_srm_credential_ref="mock-srm-demo",
        mock_srm_username="demo-user",
        mock_srm_password="demo-password",
        mock_srm_allowed_tenant_id="tenant-demo",
        mock_srm_allowed_portal_account_id="portal-demo",
    )

    assert settings.credential_resolver_mode.value == "mock_env"
    serialized = json.dumps(settings.public_snapshot())
    assert "demo-user" not in serialized
    assert "demo-password" not in serialized


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_mock_credential_resolver_is_rejected_outside_nonproduction(
    app_env: str,
) -> None:
    with pytest.raises(ValidationError, match="development or test"):
        Settings(
            _env_file=None,
            app_env=app_env,
            credential_resolver_mode="mock_env",
            mock_srm_credential_ref="mock-srm-demo",
            mock_srm_username="demo-user",
            mock_srm_password="demo-password",
            mock_srm_allowed_tenant_id="tenant-demo",
            mock_srm_allowed_portal_account_id="portal-demo",
        )


def test_public_defaults_do_not_reference_a_private_test_host() -> None:
    settings = Settings(_env_file=None)

    assert settings.task_api_base_url == (
        "http://127.0.0.1:4520/api/v1/autotask"
    )
