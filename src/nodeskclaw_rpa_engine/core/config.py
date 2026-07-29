from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nodeskclaw_rpa_engine import __version__


class TaskAuthMode(StrEnum):
    NONE = "none"
    SERVICE_ACCOUNT = "service_account"


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class WorkerType(StrEnum):
    SERVER_WORKER = "SERVER_WORKER"
    LOCAL_AGENT = "LOCAL_AGENT"


class RuntimeTraceMode(StrEnum):
    OFF = "OFF"
    ON_FAILURE = "ON_FAILURE"
    ALWAYS = "ALWAYS"


class CredentialResolverMode(StrEnum):
    DISABLED = "disabled"
    MOCK_ENV = "mock_env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "nodeskclaw-rpa-engine"
    app_version: str = __version__
    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=4610, ge=1, le=65535)
    rpa_engine_public_base_url: str = "http://127.0.0.1:4610"
    log_level: str = "INFO"

    flow_package_max_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1024,
    )
    flow_package_max_uncompressed_bytes: int = Field(
        default=200 * 1024 * 1024,
        ge=1024,
    )
    flow_package_max_files: int = Field(default=500, ge=2, le=10_000)
    flow_package_max_compression_ratio: float = Field(
        default=100.0,
        ge=1.0,
        le=10_000.0,
    )
    flow_package_url_ttl_seconds: int = Field(
        default=900,
        ge=60,
        le=86_400,
    )

    database_enabled: bool = False
    database_url: SecretStr | None = None
    database_schema: str = "rpa_engine"
    database_pool_size: int = Field(default=5, ge=1, le=50)

    minio_enabled: bool = False
    minio_endpoint_url: str | None = None
    minio_access_key: SecretStr | None = None
    minio_secret_key: SecretStr | None = None
    minio_bucket: str = "rpa-flow-packages"
    minio_region: str = "us-east-1"

    task_api_base_url: str = "http://127.0.0.1:4520/api/v1/autotask"
    task_artifact_upload_base_url: str | None = None
    task_auth_mode: TaskAuthMode = TaskAuthMode.NONE
    task_client_id: str | None = None
    task_client_secret: SecretStr | None = None
    task_api_timeout_seconds: float = Field(default=10.0, gt=0, le=120)

    worker_enabled: bool = False
    worker_lease_enabled: bool = False
    worker_id: str = Field(default="server-worker-001", min_length=1, max_length=64)
    worker_type: WorkerType = WorkerType.SERVER_WORKER
    worker_device_name: str = Field(
        default="nodeskclaw-rpa-engine",
        min_length=1,
        max_length=255,
    )
    worker_capabilities: list[str] = Field(
        default_factory=lambda: [
            "PLAYWRIGHT_CDP",
            "BROWSER_SESSION_MANAGED",
            "SCREENSHOT",
            "DOWNLOAD",
        ]
    )
    worker_tags: list[str] = Field(default_factory=list)
    worker_agent_version: str | None = Field(default=None, max_length=64)
    worker_os: str | None = Field(default=None, max_length=128)
    worker_max_concurrent_runs: int = Field(default=1, ge=1, le=32)
    worker_heartbeat_interval_seconds: float = Field(default=15.0, gt=0, le=300)
    worker_poll_interval_seconds: float = Field(default=5.0, gt=0, le=300)
    worker_lease_renew_interval_seconds: float = Field(default=20.0, gt=0, le=300)
    worker_offline_threshold_seconds: float = Field(default=45.0, gt=0, le=3600)
    worker_shutdown_grace_seconds: float = Field(default=30.0, ge=0, le=300)

    runtime_enabled: bool = False
    runtime_cache_dir: Path = Path("runtime-cache/flows")
    runtime_work_dir: Path = Path("runtime-cache/runs")
    runtime_timeout_seconds: float = Field(default=900.0, gt=0, le=86_400)
    runtime_max_retries: int = Field(default=2, ge=0, le=10)
    runtime_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=300)
    runtime_cleanup_on_finish: bool = True
    runtime_trace_mode: RuntimeTraceMode = RuntimeTraceMode.ON_FAILURE
    runtime_output_max_bytes: int = Field(
        default=1024 * 1024,
        ge=1024,
        le=50 * 1024 * 1024,
    )
    artifact_max_bytes: int = Field(
        default=200 * 1024 * 1024,
        ge=1024,
        le=5 * 1024 * 1024 * 1024,
    )

    credential_resolver_mode: CredentialResolverMode = (
        CredentialResolverMode.DISABLED
    )
    mock_srm_credential_ref: str | None = None
    mock_srm_username: SecretStr | None = None
    mock_srm_password: SecretStr | None = None
    mock_srm_allowed_tenant_id: str | None = None
    mock_srm_allowed_portal_account_id: str | None = None

    @field_validator(
        "database_url",
        "minio_access_key",
        "minio_secret_key",
        "task_client_secret",
        "mock_srm_username",
        "mock_srm_password",
        mode="before",
    )
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator(
        "minio_endpoint_url",
        "task_client_id",
        "task_artifact_upload_base_url",
        "worker_agent_version",
        "worker_os",
        "mock_srm_credential_ref",
        "mock_srm_allowed_tenant_id",
        "mock_srm_allowed_portal_account_id",
        mode="before",
    )
    @classmethod
    def empty_string_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("database_schema")
    @classmethod
    def validate_database_schema(cls, value: str) -> str:
        if value != "rpa_engine":
            raise ValueError("DATABASE_SCHEMA must be rpa_engine")
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL is invalid")
        return normalized

    @field_validator("task_api_base_url")
    @classmethod
    def validate_task_api_url(cls, value: str) -> str:
        return cls._validate_http_url(value, "TASK_API_BASE_URL")

    @field_validator("task_artifact_upload_base_url")
    @classmethod
    def validate_task_artifact_upload_base_url(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return cls._validate_http_url(
            value,
            "TASK_ARTIFACT_UPLOAD_BASE_URL",
        )

    @field_validator("rpa_engine_public_base_url")
    @classmethod
    def validate_engine_public_url(cls, value: str) -> str:
        return cls._validate_http_url(value, "RPA_ENGINE_PUBLIC_BASE_URL")

    @field_validator("minio_endpoint_url")
    @classmethod
    def validate_minio_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return cls._validate_http_url(value, "MINIO_ENDPOINT_URL")

    @field_validator("worker_capabilities", "worker_tags")
    @classmethod
    def validate_worker_string_lists(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 128 for item in normalized):
            raise ValueError("Worker capability and tag values must be non-blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Worker capability and tag values must be unique")
        return normalized

    @staticmethod
    def _validate_http_url(value: str, field_name: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{field_name} must be an HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError(f"{field_name} must not contain credentials")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_enabled_dependencies(self) -> Self:
        if self.database_enabled and self.database_url is None:
            raise ValueError("DATABASE_URL is required when DATABASE_ENABLED=true")
        if self.database_url is not None:
            parsed_database_url = urlsplit(
                self.database_url.get_secret_value()
            )
            if parsed_database_url.scheme != "postgresql+asyncpg":
                raise ValueError(
                    "DATABASE_URL must use the postgresql+asyncpg driver"
                )
            if parsed_database_url.path.rstrip("/") != "/nodeskclaw_task":
                raise ValueError(
                    "DATABASE_URL must target the nodeskclaw_task database"
                )
        if self.minio_enabled:
            required: dict[str, object | None] = {
                "MINIO_ENDPOINT_URL": self.minio_endpoint_url,
                "MINIO_ACCESS_KEY": self.minio_access_key,
                "MINIO_SECRET_KEY": self.minio_secret_key,
                "MINIO_BUCKET": self.minio_bucket,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "Missing object-storage settings: " + ", ".join(missing)
                )
        if self.task_auth_mode is TaskAuthMode.SERVICE_ACCOUNT:
            if not self.task_client_id or self.task_client_secret is None:
                raise ValueError(
                    "TASK_CLIENT_ID and TASK_CLIENT_SECRET are required for "
                    "service_account auth"
                )
        if self.worker_lease_enabled and not self.worker_enabled:
            raise ValueError(
                "WORKER_ENABLED=true is required when WORKER_LEASE_ENABLED=true"
            )
        if self.worker_enabled:
            if not self.database_enabled:
                raise ValueError(
                    "DATABASE_ENABLED=true is required when WORKER_ENABLED=true"
                )
            required_capabilities = {
                "PLAYWRIGHT_CDP",
                "BROWSER_SESSION_MANAGED",
            }
            missing_capabilities = sorted(
                required_capabilities.difference(self.worker_capabilities)
            )
            if missing_capabilities:
                raise ValueError(
                    "Missing required Worker capabilities: "
                    + ", ".join(missing_capabilities)
                )
            if (
                self.worker_offline_threshold_seconds
                <= self.worker_heartbeat_interval_seconds
            ):
                raise ValueError(
                    "WORKER_OFFLINE_THRESHOLD_SECONDS must be greater than "
                    "WORKER_HEARTBEAT_INTERVAL_SECONDS"
                )
        if self.runtime_enabled and not self.minio_enabled:
            raise ValueError(
                "MINIO_ENABLED=true is required when RUNTIME_ENABLED=true"
            )
        if self.credential_resolver_mode is CredentialResolverMode.MOCK_ENV:
            if self.app_env not in {
                AppEnvironment.DEVELOPMENT,
                AppEnvironment.TEST,
            }:
                raise ValueError(
                    "CREDENTIAL_RESOLVER_MODE=mock_env is allowed only in "
                    "development or test"
                )
            mock_required: dict[str, object | None] = {
                "MOCK_SRM_CREDENTIAL_REF": self.mock_srm_credential_ref,
                "MOCK_SRM_USERNAME": self.mock_srm_username,
                "MOCK_SRM_PASSWORD": self.mock_srm_password,
                "MOCK_SRM_ALLOWED_TENANT_ID": (
                    self.mock_srm_allowed_tenant_id
                ),
                "MOCK_SRM_ALLOWED_PORTAL_ACCOUNT_ID": (
                    self.mock_srm_allowed_portal_account_id
                ),
            }
            missing_mock = [
                name for name, value in mock_required.items() if not value
            ]
            if missing_mock:
                raise ValueError(
                    "Missing mock credential settings: "
                    + ", ".join(missing_mock)
                )
        if self.runtime_cache_dir.resolve() == self.runtime_work_dir.resolve():
            raise ValueError(
                "RUNTIME_CACHE_DIR and RUNTIME_WORK_DIR must be different"
            )
        return self

    def public_snapshot(self) -> dict[str, Any]:
        """返回安全的运行配置，不包含可能携带秘密的端点。"""
        return {
            "appName": self.app_name,
            "appVersion": self.app_version,
            "environment": self.app_env.value,
            "host": self.app_host,
            "port": self.app_port,
            "logLevel": self.log_level,
            "databaseEnabled": self.database_enabled,
            "databaseSchema": self.database_schema,
            "objectStorageEnabled": self.minio_enabled,
            "taskAuthMode": self.task_auth_mode.value,
            "workerEnabled": self.worker_enabled,
            "workerLeaseEnabled": self.worker_lease_enabled,
            "workerId": self.worker_id,
            "runtimeEnabled": self.runtime_enabled,
            "credentialResolverMode": self.credential_resolver_mode.value,
        }
