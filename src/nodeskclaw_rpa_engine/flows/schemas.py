from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from nodeskclaw_rpa_engine.flows.manifest import SEMVER_PATTERN


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class FlowScope(StrEnum):
    GLOBAL = "GLOBAL"
    TENANT = "TENANT"


class FlowStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ARCHIVED = "ARCHIVED"


class FlowVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    DISABLED = "DISABLED"


class ValidationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"


class ActorContext(ApiModel):
    actor_id: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("actor_id", "tenant_id")
    @classmethod
    def identifiers_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("identifier must not be blank")
        return stripped


class FlowSummary(ApiModel):
    id: UUID
    rpa_flow_id: str
    scope: FlowScope
    tenant_id: str | None
    name: str
    description: str | None
    status: FlowStatus
    labels: list[str]
    created_by: str
    created_at: datetime
    updated_at: datetime


class FlowVersionResponse(ApiModel):
    rpa_flow_version_id: UUID
    rpa_flow_id: str
    version: str
    status: FlowVersionStatus
    engine_type: str
    entrypoint: str
    manifest: dict[str, Any]
    supported_workflow_codes: list[str]
    supported_portal_types: list[str]
    input_schema: list[Any]
    capabilities: list[str]
    minimum_engine_version: str | None
    package_uri: str | None
    package_size_bytes: int | None
    package_checksum: str | None
    created_by: str
    created_at: datetime
    published_at: datetime | None
    updated_at: datetime


class FlowDetail(FlowSummary):
    versions: list[FlowVersionResponse]


class FlowListResponse(ApiModel):
    items: list[FlowSummary]
    total: int
    limit: int
    offset: int


class FlowVersionListResponse(ApiModel):
    items: list[FlowVersionResponse]


class ValidationResponse(ApiModel):
    validation_run_id: UUID
    flow_version_id: UUID
    trigger_type: str
    status: ValidationStatus
    checks: list[Any]
    errors: list[Any]
    warnings: list[Any]
    result_summary: str | None
    requested_by: str
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class FlowPackageUploadResponse(ApiModel):
    flow: FlowSummary
    version: FlowVersionResponse
    validation: ValidationResponse


class BindingValidationRequest(ApiModel):
    rpa_flow_version_id: UUID | None = None
    rpa_flow_id: str | None = Field(default=None, min_length=1, max_length=255)
    rpa_flow_version: str | None = Field(
        default=None,
        pattern=SEMVER_PATTERN,
        max_length=64,
    )
    workflow_code: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_version_reference(self) -> Self:
        by_id = self.rpa_flow_version_id is not None
        by_key = self.rpa_flow_id is not None or self.rpa_flow_version is not None
        if not by_id and not (
            self.rpa_flow_id is not None
            and self.rpa_flow_version is not None
        ):
            raise ValueError(
                "Provide rpaFlowVersionId or both rpaFlowId and rpaFlowVersion"
            )
        if by_id and by_key:
            raise ValueError("Use only one Flow Version reference form")
        return self


class BindingValidationResponse(ApiModel):
    valid: bool
    reason_code: str | None
    version: FlowVersionResponse | None
    # Task 临时兼容字段。权威快照仍嵌套在 ``version`` 下；待 nodeskclaw-task
    # 正确读取 version.rpaFlowVersionId/version.packageChecksum 后删除这些别名。
    rpa_flow_version_id: UUID | None = Field(default=None, deprecated=True)
    package_checksum: str | None = Field(default=None, deprecated=True)
    checksum: str | None = Field(default=None, deprecated=True)

    @model_validator(mode="after")
    def populate_task_compatibility_snapshot(self) -> Self:
        if self.version is not None:
            self.rpa_flow_version_id = self.version.rpa_flow_version_id
            self.package_checksum = self.version.package_checksum
            self.checksum = self.version.package_checksum
        return self


class RollbackRequest(ApiModel):
    target_flow_version_id: UUID
    reason: str | None = Field(default=None, max_length=2000)


class StatusChangeRequest(ApiModel):
    reason: str | None = Field(default=None, max_length=2000)


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict[str, Any] | list[Any] | None = None


class ErrorResponse(ApiModel):
    error: ErrorBody
