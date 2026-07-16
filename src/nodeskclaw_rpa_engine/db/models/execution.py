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
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from nodeskclaw_rpa_engine.db.base import Base


class RpaWorkerInstance(Base):
    __tablename__ = "rpa_worker_instances"
    __table_args__ = (
        UniqueConstraint(
            "worker_id",
            name="uq_rpa_worker_instances_worker_id",
        ),
        CheckConstraint(
            "cardinality(capabilities) > 0",
            name="ck_rpa_worker_instances_capabilities",
        ),
        CheckConstraint(
            "max_concurrent_runs > 0 AND current_task_count >= 0 "
            "AND current_task_count <= max_concurrent_runs "
            "AND browser_count >= 0",
            name="ck_rpa_worker_instances_concurrency",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_rpa_worker_instances_metadata",
        ),
        CheckConstraint(
            "status IN ('ONLINE', 'BUSY', 'OFFLINE', 'DRAINING')",
            name="ck_rpa_worker_instances_status",
        ),
        CheckConstraint(
            "worker_type IN ('SERVER_WORKER', 'LOCAL_AGENT')",
            name="ck_rpa_worker_instances_type",
        ),
        CheckConstraint(
            "btrim(worker_id) <> ''",
            name="ck_rpa_worker_instances_worker_id",
        ),
        Index(
            "ix_rpa_worker_instances_capabilities_gin",
            "capabilities",
            postgresql_using="gin",
        ),
        Index(
            "ix_rpa_worker_instances_status_heartbeat",
            "status",
            "last_heartbeat_at",
        ),
        Index(
            "ix_rpa_worker_instances_tags_gin",
            "tags",
            postgresql_using="gin",
        ),
        {
            "comment": "Engine-internal Worker state; public.rpa_workers remains "
            "Task dispatch authority"
        },
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    worker_id: Mapped[str] = mapped_column(String(64))
    worker_type: Mapped[str] = mapped_column(String(32))
    device_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(16), server_default=text("'OFFLINE'::character varying")
    )
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text))
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("ARRAY[]::text[]")
    )
    app_version: Mapped[str | None] = mapped_column(String(64))
    agent_version: Mapped[str | None] = mapped_column(String(64))
    os: Mapped[str | None] = mapped_column(String(128))
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    current_task_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    browser_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RpaExecutionAttempt(Base):
    __tablename__ = "rpa_execution_attempts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "attempt_no",
            name="uq_rpa_execution_attempts_run_attempt",
        ),
        CheckConstraint(
            "attempt_no > 0",
            name="ck_rpa_execution_attempts_attempt_no",
        ),
        CheckConstraint(
            "jsonb_typeof(browser_session_snapshot) = 'object'",
            name="ck_rpa_execution_attempts_browser_session",
        ),
        CheckConstraint(
            "package_checksum_snapshot ~ '^[0-9a-f]{64}$'",
            name="ck_rpa_execution_attempts_checksum",
        ),
        CheckConstraint(
            "dispatch_mode IN ('LEASE', 'QUEUE')",
            name="ck_rpa_execution_attempts_dispatch_mode",
        ),
        CheckConstraint(
            "(dispatch_mode = 'LEASE' AND lease_id IS NOT NULL "
            "AND btrim(lease_id) <> '') OR "
            "(dispatch_mode = 'QUEUE' AND command_id IS NOT NULL "
            "AND btrim(command_id) <> '')",
            name="ck_rpa_execution_attempts_dispatch_reference",
        ),
        CheckConstraint(
            "jsonb_typeof(error_details) = 'object'",
            name="ck_rpa_execution_attempts_error_details",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_rpa_execution_attempts_input",
        ),
        CheckConstraint(
            "status IN ('RECEIVED', 'LEASED', 'RUNNING', 'SUCCESS', 'FAILED', "
            "'WAITING_HUMAN', 'CANCELLED', 'ABANDONED')",
            name="ck_rpa_execution_attempts_status",
        ),
        CheckConstraint(
            "((status IN ('SUCCESS', 'FAILED', 'WAITING_HUMAN', 'CANCELLED', "
            "'ABANDONED')) AND ended_at IS NOT NULL) OR "
            "((status NOT IN ('SUCCESS', 'FAILED', 'WAITING_HUMAN', "
            "'CANCELLED', 'ABANDONED')) AND ended_at IS NULL)",
            name="ck_rpa_execution_attempts_terminal_time",
        ),
        CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="ck_rpa_execution_attempts_time_order",
        ),
        Index(
            "ix_rpa_execution_attempts_retention",
            "ended_at",
            postgresql_where=text("ended_at IS NOT NULL"),
        ),
        Index(
            "ix_rpa_execution_attempts_run_received",
            "run_id",
            text("received_at DESC"),
        ),
        Index(
            "ix_rpa_execution_attempts_status_received",
            "status",
            "received_at",
        ),
        Index(
            "ix_rpa_execution_attempts_task_received",
            "task_id",
            text("received_at DESC"),
        ),
        Index(
            "ix_rpa_execution_attempts_worker_status",
            "worker_id",
            "status",
        ),
        Index(
            "uq_rpa_execution_attempts_command_id",
            "command_id",
            unique=True,
            postgresql_where=text("command_id IS NOT NULL"),
        ),
        Index(
            "uq_rpa_execution_attempts_lease_id",
            "lease_id",
            unique=True,
            postgresql_where=text("lease_id IS NOT NULL"),
        ),
        {
            "comment": "Engine technical attempts; public.rpa_runs remains Task "
            "Run authority"
        },
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    dispatch_mode: Mapped[str] = mapped_column(String(16))
    command_id: Mapped[str | None] = mapped_column(String(128))
    lease_id: Mapped[str | None] = mapped_column(String(128))
    task_id: Mapped[str] = mapped_column(
        String(128), comment="External Task ID; no cross-Schema foreign key"
    )
    run_id: Mapped[str] = mapped_column(
        String(128), comment="External Run ID; no cross-Schema foreign key"
    )
    workflow_binding_id: Mapped[str | None] = mapped_column(String(128))
    portal_account_id: Mapped[str | None] = mapped_column(String(128))
    worker_instance_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "rpa_engine.rpa_worker_instances.id",
            ondelete="SET NULL",
            name="rpa_execution_attempts_worker_instance_id_fkey",
        ),
    )
    worker_id: Mapped[str] = mapped_column(String(64))
    flow_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "rpa_engine.rpa_flow_versions.id",
            ondelete="RESTRICT",
            name="rpa_execution_attempts_flow_version_id_fkey",
        ),
    )
    rpa_flow_id_snapshot: Mapped[str] = mapped_column(String(255))
    rpa_flow_version_snapshot: Mapped[str] = mapped_column(String(64))
    package_checksum_snapshot: Mapped[str] = mapped_column(CHAR(64))
    attempt_no: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        String(24), server_default=text("'RECEIVED'::character varying")
    )
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    browser_session_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RpaCallbackOutbox(Base):
    __tablename__ = "rpa_callback_outbox"
    __table_args__ = (
        UniqueConstraint(
            "execution_attempt_id",
            "sequence_no",
            name="uq_rpa_callback_outbox_attempt_sequence",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_rpa_callback_outbox_idempotency",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="ck_rpa_callback_outbox_attempts",
        ),
        CheckConstraint(
            "destination = 'NODESKCLAW_TASK'",
            name="ck_rpa_callback_outbox_destination",
        ),
        CheckConstraint(
            "endpoint_path LIKE '/%' AND endpoint_path NOT LIKE 'http://%' "
            "AND endpoint_path NOT LIKE 'https://%'",
            name="ck_rpa_callback_outbox_endpoint",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_rpa_callback_outbox_payload",
        ),
        CheckConstraint(
            "response_status IS NULL OR "
            "(response_status >= 100 AND response_status <= 599)",
            name="ck_rpa_callback_outbox_response_status",
        ),
        CheckConstraint(
            "status <> 'SENT' OR sent_at IS NOT NULL",
            name="ck_rpa_callback_outbox_sent",
        ),
        CheckConstraint(
            "sequence_no > 0",
            name="ck_rpa_callback_outbox_sequence",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'IN_FLIGHT', 'RETRY', 'SENT', 'DEAD')",
            name="ck_rpa_callback_outbox_status",
        ),
        CheckConstraint(
            "callback_type IN ('EVENT', 'ARTIFACT', 'FINISH')",
            name="ck_rpa_callback_outbox_type",
        ),
        Index(
            "ix_rpa_callback_outbox_attempt",
            "execution_attempt_id",
            "sequence_no",
        ),
        Index(
            "ix_rpa_callback_outbox_locked",
            "locked_at",
            postgresql_where=text("status = 'IN_FLIGHT'"),
        ),
        Index(
            "ix_rpa_callback_outbox_poll",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'RETRY')"),
        ),
        Index(
            "ix_rpa_callback_outbox_retention",
            "sent_at",
            postgresql_where=text("status = 'SENT'"),
        ),
        {"comment": "Ordered, idempotent callbacks to nodeskclaw-task"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    execution_attempt_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "rpa_engine.rpa_execution_attempts.id",
            ondelete="RESTRICT",
            name="rpa_callback_outbox_execution_attempt_id_fkey",
        ),
    )
    destination: Mapped[str] = mapped_column(
        String(32), server_default=text("'NODESKCLAW_TASK'::character varying")
    )
    callback_type: Mapped[str] = mapped_column(String(16))
    aggregate_id: Mapped[str] = mapped_column(String(128))
    sequence_no: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    endpoint_path: Mapped[str] = mapped_column(
        Text,
        comment="Relative Task API path only; never store credentials or signed URLs",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(16), server_default=text("'PENDING'::character varying")
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("10"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    response_status: Mapped[int | None] = mapped_column(Integer)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
