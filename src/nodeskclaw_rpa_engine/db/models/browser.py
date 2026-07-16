from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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


class RpaBrowserProfile(Base):
    __tablename__ = "rpa_browser_profiles"
    __table_args__ = (
        UniqueConstraint("profile_ref", name="uq_rpa_browser_profiles_ref"),
        CheckConstraint(
            "status <> 'ACTIVE' OR "
            "(storage_ref IS NOT NULL AND btrim(storage_ref) <> '')",
            name="ck_rpa_browser_profiles_active_storage",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_rpa_browser_profiles_metadata",
        ),
        CheckConstraint(
            "owner_type IN ('PORTAL_ACCOUNT', 'TENANT')",
            name="ck_rpa_browser_profiles_owner_type",
        ),
        CheckConstraint(
            "btrim(portal_account_id) <> ''",
            name="ck_rpa_browser_profiles_portal",
        ),
        CheckConstraint(
            "btrim(profile_ref) <> ''",
            name="ck_rpa_browser_profiles_ref",
        ),
        CheckConstraint(
            "status IN ('DISABLED', 'ACTIVE', 'LOCKED', 'REVOKED')",
            name="ck_rpa_browser_profiles_status",
        ),
        CheckConstraint(
            "btrim(tenant_id) <> ''",
            name="ck_rpa_browser_profiles_tenant",
        ),
        Index("ix_rpa_browser_profiles_portal", "portal_account_id"),
        Index(
            "ix_rpa_browser_profiles_tenant_status",
            "tenant_id",
            "status",
        ),
        Index(
            "ix_rpa_browser_profiles_worker_tags_gin",
            "allowed_worker_tags",
            postgresql_using="gin",
        ),
        {"comment": "Future PERSISTENT_PROFILE metadata; disabled in P0"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    profile_ref: Mapped[str] = mapped_column(String(128))
    tenant_id: Mapped[str] = mapped_column(String(128))
    portal_account_id: Mapped[str] = mapped_column(String(128))
    owner_type: Mapped[str] = mapped_column(
        String(32), server_default=text("'PORTAL_ACCOUNT'::character varying")
    )
    storage_ref: Mapped[str | None] = mapped_column(Text)
    allowed_worker_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("ARRAY[]::text[]")
    )
    status: Mapped[str] = mapped_column(
        String(16), server_default=text("'DISABLED'::character varying")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    created_by: Mapped[str] = mapped_column(String(128))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RpaCdpEndpoint(Base):
    __tablename__ = "rpa_cdp_endpoints"
    __table_args__ = (
        UniqueConstraint("endpoint_ref", name="uq_rpa_cdp_endpoints_ref"),
        CheckConstraint(
            "status <> 'ACTIVE' OR (connection_secret_ref IS NOT NULL "
            "AND btrim(connection_secret_ref) <> '')",
            name="ck_rpa_cdp_endpoints_active_secret",
        ),
        CheckConstraint(
            "endpoint_kind IN ('LOCAL', 'REMOTE', 'MANAGED')",
            name="ck_rpa_cdp_endpoints_kind",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_rpa_cdp_endpoints_metadata",
        ),
        CheckConstraint(
            "btrim(endpoint_ref) <> ''",
            name="ck_rpa_cdp_endpoints_ref",
        ),
        CheckConstraint(
            "status IN ('DISABLED', 'ACTIVE', 'REVOKED')",
            name="ck_rpa_cdp_endpoints_status",
        ),
        CheckConstraint(
            "btrim(tenant_id) <> ''",
            name="ck_rpa_cdp_endpoints_tenant",
        ),
        Index(
            "ix_rpa_cdp_endpoints_portal",
            "portal_account_id",
            postgresql_where=text("portal_account_id IS NOT NULL"),
        ),
        Index(
            "ix_rpa_cdp_endpoints_tenant_status",
            "tenant_id",
            "status",
        ),
        Index(
            "ix_rpa_cdp_endpoints_worker_tags_gin",
            "allowed_worker_tags",
            postgresql_using="gin",
        ),
        {"comment": "Future CDP_ATTACH references; disabled in P0"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    endpoint_ref: Mapped[str] = mapped_column(String(128))
    tenant_id: Mapped[str] = mapped_column(String(128))
    portal_account_id: Mapped[str | None] = mapped_column(String(128))
    endpoint_kind: Mapped[str] = mapped_column(
        String(16), server_default=text("'REMOTE'::character varying")
    )
    connection_secret_ref: Mapped[str | None] = mapped_column(
        String(255),
        comment="Secret-manager reference only; never plaintext connection credentials",
    )
    allowed_worker_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("ARRAY[]::text[]")
    )
    status: Mapped[str] = mapped_column(
        String(16), server_default=text("'DISABLED'::character varying")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    created_by: Mapped[str] = mapped_column(String(128))
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
