from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from nodeskclaw_rpa_engine.db.base import Base


class RpaFlow(Base):
    __tablename__ = "rpa_flows"
    __table_args__ = (
        CheckConstraint(
            "btrim(flow_key) <> ''",
            name="ck_rpa_flows_flow_key_not_blank",
        ),
        CheckConstraint(
            "btrim(name) <> ''",
            name="ck_rpa_flows_name_not_blank",
        ),
        CheckConstraint(
            "scope IN ('GLOBAL', 'TENANT')",
            name="ck_rpa_flows_scope",
        ),
        CheckConstraint(
            "(scope = 'GLOBAL' AND tenant_id IS NULL) OR "
            "(scope = 'TENANT' AND tenant_id IS NOT NULL "
            "AND btrim(tenant_id) <> '')",
            name="ck_rpa_flows_scope_tenant",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED', 'ARCHIVED')",
            name="ck_rpa_flows_status",
        ),
        Index("ix_rpa_flows_labels_gin", "labels", postgresql_using="gin"),
        Index("ix_rpa_flows_status", "status"),
        Index(
            "ix_rpa_flows_tenant_status",
            "tenant_id",
            "status",
            postgresql_where=text("scope = 'TENANT'"),
        ),
        Index(
            "uq_rpa_flows_global_flow_key",
            "flow_key",
            unique=True,
            postgresql_where=text("scope = 'GLOBAL'"),
        ),
        Index(
            "uq_rpa_flows_tenant_flow_key",
            "tenant_id",
            "flow_key",
            unique=True,
            postgresql_where=text("scope = 'TENANT'"),
        ),
        {"comment": "Stable GLOBAL or TENANT RPA Flow identity"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    flow_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Public rpaFlowId; stable across versions",
    )
    scope: Mapped[str] = mapped_column(String(16))
    tenant_id: Mapped[str | None] = mapped_column(
        String(128),
        comment="External tenant reference; NULL only for GLOBAL Flow",
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'ACTIVE'::character varying"),
    )
    labels: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("ARRAY[]::text[]"),
    )
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


class RpaFlowVersion(Base):
    __tablename__ = "rpa_flow_versions"
    __table_args__ = (
        UniqueConstraint(
            "flow_id",
            "version",
            name="uq_rpa_flow_versions_flow_version",
        ),
        CheckConstraint(
            "package_checksum_sha256 IS NULL OR "
            "package_checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_rpa_flow_versions_checksum",
        ),
        CheckConstraint(
            "engine_type = 'PLAYWRIGHT_CDP'",
            name="ck_rpa_flow_versions_engine_type",
        ),
        CheckConstraint(
            "entrypoint = 'flow.py:run'",
            name="ck_rpa_flow_versions_entrypoint",
        ),
        CheckConstraint(
            "jsonb_typeof(input_schema) = 'array'",
            name="ck_rpa_flow_versions_input_schema_array",
        ),
        CheckConstraint(
            "jsonb_typeof(manifest) = 'object'",
            name="ck_rpa_flow_versions_manifest_object",
        ),
        CheckConstraint(
            "package_size_bytes IS NULL OR package_size_bytes >= 0",
            name="ck_rpa_flow_versions_package_size",
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR "
            "(published_at IS NOT NULL AND package_bucket IS NOT NULL "
            "AND btrim(package_bucket) <> '' AND package_object_key IS NOT NULL "
            "AND btrim(package_object_key) <> '' AND package_size_bytes IS NOT NULL "
            "AND package_checksum_sha256 IS NOT NULL)",
            name="ck_rpa_flow_versions_published_package",
        ),
        CheckConstraint(
            "version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.]"
            "(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'",
            name="ck_rpa_flow_versions_semver",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'VALIDATING', 'PUBLISHED', "
            "'DEPRECATED', 'DISABLED')",
            name="ck_rpa_flow_versions_status",
        ),
        CheckConstraint(
            "cardinality(supported_workflow_codes) > 0",
            name="ck_rpa_flow_versions_workflow_codes",
        ),
        Index(
            "ix_rpa_flow_versions_capabilities_gin",
            "capabilities",
            postgresql_using="gin",
        ),
        Index("ix_rpa_flow_versions_flow_status", "flow_id", "status"),
        Index(
            "ix_rpa_flow_versions_manifest_gin",
            "manifest",
            postgresql_using="gin",
            postgresql_ops={"manifest": "jsonb_path_ops"},
        ),
        Index(
            "ix_rpa_flow_versions_portal_types_gin",
            "supported_portal_types",
            postgresql_using="gin",
        ),
        Index(
            "ix_rpa_flow_versions_published_at",
            text("published_at DESC"),
            postgresql_where=text("published_at IS NOT NULL"),
        ),
        Index("ix_rpa_flow_versions_status", "status"),
        Index(
            "ix_rpa_flow_versions_workflow_codes_gin",
            "supported_workflow_codes",
            postgresql_using="gin",
        ),
        {"comment": "Versioned immutable Flow manifest and package metadata"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    flow_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "rpa_engine.rpa_flows.id",
            ondelete="RESTRICT",
            name="rpa_flow_versions_flow_id_fkey",
        ),
    )
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'DRAFT'::character varying"),
    )
    engine_type: Mapped[str] = mapped_column(
        String(32),
        server_default=text("'PLAYWRIGHT_CDP'::character varying"),
    )
    entrypoint: Mapped[str] = mapped_column(
        String(255),
        server_default=text("'flow.py:run'::character varying"),
    )
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    supported_workflow_codes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    supported_portal_types: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("ARRAY[]::text[]"),
    )
    input_schema: Mapped[list[Any]] = mapped_column(
        JSONB,
        server_default=text("'[]'::jsonb"),
    )
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("ARRAY[]::text[]"),
    )
    minimum_engine_version: Mapped[str | None] = mapped_column(
        String(64)
    )
    package_bucket: Mapped[str | None] = mapped_column(
        String(255)
    )
    package_object_key: Mapped[str | None] = mapped_column(
        Text,
        comment="Stable MinIO/S3 object key; never store a signed URL",
    )
    package_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    package_checksum_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RpaFlowValidationRun(Base):
    __tablename__ = "rpa_flow_validation_runs"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(checks) = 'array'",
            name="ck_rpa_flow_validation_checks_array",
        ),
        CheckConstraint(
            "jsonb_typeof(errors) = 'array'",
            name="ck_rpa_flow_validation_errors_array",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'PASSED', 'FAILED')",
            name="ck_rpa_flow_validation_status",
        ),
        CheckConstraint(
            "status NOT IN ('PASSED', 'FAILED') OR ended_at IS NOT NULL",
            name="ck_rpa_flow_validation_terminal_time",
        ),
        CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="ck_rpa_flow_validation_time_order",
        ),
        CheckConstraint(
            "trigger_type IN ('UPLOAD', 'MANUAL', 'PUBLISH', 'CI')",
            name="ck_rpa_flow_validation_trigger",
        ),
        CheckConstraint(
            "jsonb_typeof(warnings) = 'array'",
            name="ck_rpa_flow_validation_warnings_array",
        ),
        Index("ix_rpa_flow_validation_status", "status", "created_at"),
        Index(
            "ix_rpa_flow_validation_version_created",
            "flow_version_id",
            text("created_at DESC"),
        ),
        {"comment": "Upload, manual, publish, and CI Flow validation results"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    flow_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "rpa_engine.rpa_flow_versions.id",
            ondelete="RESTRICT",
            name="rpa_flow_validation_runs_flow_version_id_fkey",
        ),
    )
    trigger_type: Mapped[str] = mapped_column(
        String(16)
    )
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'PENDING'::character varying"),
    )
    checks: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    errors: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    warnings: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    result_summary: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(
        String(128)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RpaFlowReleaseAudit(Base):
    __tablename__ = "rpa_flow_release_audits"
    __table_args__ = (
        CheckConstraint(
            "action IN ('UPLOADED', 'VALIDATION_STARTED', 'VALIDATION_PASSED', "
            "'VALIDATION_FAILED', 'PUBLISHED', 'DEPRECATED', 'DISABLED', "
            "'ROLLED_BACK', 'STATUS_CHANGED')",
            name="ck_rpa_flow_release_audit_action",
        ),
        CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name="ck_rpa_flow_release_audit_details",
        ),
        Index(
            "ix_rpa_flow_release_audits_flow_created",
            "flow_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_rpa_flow_release_audits_version_created",
            "flow_version_id",
            text("created_at DESC"),
            postgresql_where=text("flow_version_id IS NOT NULL"),
        ),
        {"comment": "Append-only Flow publication and status audit trail"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    flow_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "rpa_engine.rpa_flows.id",
            ondelete="RESTRICT",
            name="rpa_flow_release_audits_flow_id_fkey",
        ),
    )
    flow_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "rpa_engine.rpa_flow_versions.id",
            ondelete="RESTRICT",
            name="rpa_flow_release_audits_flow_version_id_fkey",
        ),
    )
    action: Mapped[str] = mapped_column(String(32))
    from_status: Mapped[str | None] = mapped_column(
        String(16)
    )
    to_status: Mapped[str | None] = mapped_column(
        String(16)
    )
    actor_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
