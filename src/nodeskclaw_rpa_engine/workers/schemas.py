from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class WorkerStatus(StrEnum):
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    OFFLINE = "OFFLINE"
    DRAINING = "DRAINING"


class AttemptStatus(StrEnum):
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    WAITING_HUMAN = "WAITING_HUMAN"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"


TERMINAL_ATTEMPT_STATUSES = {
    AttemptStatus.SUCCESS,
    AttemptStatus.FAILED,
    AttemptStatus.WAITING_HUMAN,
    AttemptStatus.CANCELLED,
    AttemptStatus.ABANDONED,
}


class TaskEnvelope(CamelModel):
    code: int | str
    data: Any = None
    error_code: int | str | None = None
    message_key: str | None = None
    message: str | None = None


class WorkerRegisterRequest(CamelModel):
    worker_id: str
    worker_type: str
    device_name: str
    capabilities: list[str]
    app_version: str | None = None
    agent_version: str | None = None
    os: str | None = None


class WorkerLeaseRequest(CamelModel):
    worker_id: str
    capabilities: list[str]
    limit: int = Field(default=1, ge=1)


class WorkerLeaseRenewRequest(CamelModel):
    worker_id: str
    lease_id: str


class BrowserSessionConfig(CamelModel):
    mode: str
    headless: bool
    channel: str
    profile_ref: str | None
    cdp_endpoint_ref: str | None
    close_policy: str


class RunConfig(CamelModel):
    browser_session: BrowserSessionConfig
    portal_url: str

    @field_validator("portal_url")
    @classmethod
    def validate_portal_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("portalUrl must be an HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("portalUrl must not contain credentials")
        return value


class LeaseRunCommand(CamelModel):
    task_id: str
    run_id: str
    lease_id: str
    workflow_binding_id: str | None = None
    portal_account_id: str | None = None
    rpa_flow_id: str
    input: dict[str, Any] = Field(default_factory=dict)

    tenant_id: str | None
    workflow_template_id: str
    workflow_code: str
    rpa_engine_type: str
    rpa_flow_version: str
    credential_ref: str | None
    config: RunConfig
    lease_expires_at: datetime

    @field_validator("lease_expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("leaseExpiresAt must include a timezone")
        return value


class LeaseRenewal(CamelModel):
    lease_expires_at: datetime

    @field_validator("lease_expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("leaseExpiresAt must include a timezone")
        return value


class RunEventRequest(CamelModel):
    worker_id: str | None = None
    type: str
    level: str = "INFO"
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RunFinishRequest(CamelModel):
    status: AttemptStatus
    error_code: str | None = None
    error_message: str | None = None
    output: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_output_status(self) -> RunFinishRequest:
        if self.output is not None and self.status is not AttemptStatus.SUCCESS:
            raise ValueError("Run output is allowed only for SUCCESS")
        return self


class ArtifactUploadUrlRequest(CamelModel):
    worker_id: str
    task_id: str
    run_id: str
    name: str
    mime_type: str | None = None


class ArtifactUploadTarget(CamelModel):
    upload_url: str
    storage_key: str


class RunArtifactCreate(CamelModel):
    type: str
    name: str
    storage_key: str
    size: int = Field(default=0, ge=0)
    mime_type: str | None = None


class ResolvedFlowVersion(CamelModel):
    flow_version_id: UUID
    rpa_flow_id: str
    version: str
    engine_type: str
    package_uri: str
    package_checksum: str
    package_object_key: str | None = Field(default=None, exclude=True)
    supported_workflow_codes: list[str]
    capabilities: list[str]


class RunCommand(CamelModel):
    lease: LeaseRunCommand
    flow: ResolvedFlowVersion


class RunResult(CamelModel):
    status: AttemptStatus
    error_code: str | None = None
    error_message: str | None = None
    output: dict[str, Any] | None = None

    @field_validator("status")
    @classmethod
    def require_terminal(cls, value: AttemptStatus) -> AttemptStatus:
        if value not in TERMINAL_ATTEMPT_STATUSES:
            raise ValueError("RunResult status must be terminal")
        return value

    @model_validator(mode="after")
    def validate_output_status(self) -> RunResult:
        if self.output is not None and self.status is not AttemptStatus.SUCCESS:
            raise ValueError("Run output is allowed only for SUCCESS")
        return self


class WorkerResponse(CamelModel):
    id: UUID
    worker_id: str
    worker_type: str
    device_name: str
    status: WorkerStatus
    capabilities: list[str]
    tags: list[str]
    app_version: str | None
    agent_version: str | None
    os: str | None
    max_concurrent_runs: int
    current_task_count: int
    browser_count: int
    registered_at: datetime
    last_heartbeat_at: datetime
    updated_at: datetime

    @classmethod
    def effective_status(
        cls,
        stored_status: str,
        last_heartbeat_at: datetime,
        offline_threshold_seconds: float,
        *,
        now: datetime | None = None,
    ) -> WorkerStatus:
        status = WorkerStatus(stored_status)
        if status in {WorkerStatus.OFFLINE, WorkerStatus.DRAINING}:
            return status
        current = now or datetime.now(UTC)
        if (current - last_heartbeat_at).total_seconds() > offline_threshold_seconds:
            return WorkerStatus.OFFLINE
        return status


class WorkerListResponse(CamelModel):
    items: list[WorkerResponse]
    total: int
    limit: int
    offset: int
