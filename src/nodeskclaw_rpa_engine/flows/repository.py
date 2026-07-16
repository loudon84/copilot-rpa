from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from nodeskclaw_rpa_engine.db.models import (
    RpaFlow,
    RpaFlowReleaseAudit,
    RpaFlowValidationRun,
    RpaFlowVersion,
)
from nodeskclaw_rpa_engine.flows.schemas import FlowScope


class SqlAlchemyFlowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_flows(
        self,
        *,
        tenant_id: str | None,
        scope: FlowScope | None,
        status: str | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[RpaFlow], int]:
        filters = self._visibility_filters(tenant_id=tenant_id, scope=scope)
        if status is not None:
            filters.append(RpaFlow.status == status)
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    RpaFlow.flow_key.ilike(pattern),
                    RpaFlow.name.ilike(pattern),
                )
            )

        total_statement = select(func.count()).select_from(RpaFlow).where(*filters)
        total = int((await self._session.execute(total_statement)).scalar_one())
        statement = (
            select(RpaFlow)
            .where(*filters)
            .order_by(RpaFlow.updated_at.desc(), RpaFlow.flow_key)
            .limit(limit)
            .offset(offset)
        )
        flows = (await self._session.execute(statement)).scalars().all()
        return flows, total

    async def get_flow(
        self,
        flow_key: str,
        *,
        scope: FlowScope,
        tenant_id: str | None,
        for_update: bool = False,
    ) -> RpaFlow | None:
        statement: Select[tuple[RpaFlow]] = select(RpaFlow).where(
            RpaFlow.flow_key == flow_key,
            RpaFlow.scope == scope.value,
        )
        if scope is FlowScope.GLOBAL:
            statement = statement.where(RpaFlow.tenant_id.is_(None))
        else:
            statement = statement.where(RpaFlow.tenant_id == tenant_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_versions(self, flow_id: UUID) -> Sequence[RpaFlowVersion]:
        statement = (
            select(RpaFlowVersion)
            .where(RpaFlowVersion.flow_id == flow_id)
            .order_by(RpaFlowVersion.created_at.desc())
        )
        return (await self._session.execute(statement)).scalars().all()

    async def get_version(
        self,
        flow_version_id: UUID,
        *,
        tenant_id: str | None,
        for_update: bool = False,
    ) -> tuple[RpaFlowVersion, RpaFlow] | None:
        statement = (
            select(RpaFlowVersion, RpaFlow)
            .join(RpaFlow, RpaFlow.id == RpaFlowVersion.flow_id)
            .where(
                RpaFlowVersion.id == flow_version_id,
                self._visible_expression(tenant_id),
            )
        )
        if for_update:
            statement = statement.with_for_update(of=RpaFlowVersion)
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    async def get_version_by_key(
        self,
        flow_key: str,
        version: str,
        *,
        tenant_id: str | None,
    ) -> tuple[RpaFlowVersion, RpaFlow] | None:
        statement = (
            select(RpaFlowVersion, RpaFlow)
            .join(RpaFlow, RpaFlow.id == RpaFlowVersion.flow_id)
            .where(
                RpaFlow.flow_key == flow_key,
                RpaFlowVersion.version == version,
                self._visible_expression(tenant_id),
            )
            .order_by(
                (RpaFlow.scope == FlowScope.TENANT.value).desc(),
            )
        )
        rows = (await self._session.execute(statement)).all()
        if not rows:
            return None
        return rows[0][0], rows[0][1]

    async def get_version_for_flow(
        self,
        flow_id: UUID,
        version: str,
        *,
        for_update: bool = False,
    ) -> RpaFlowVersion | None:
        statement = select(RpaFlowVersion).where(
            RpaFlowVersion.flow_id == flow_id,
            RpaFlowVersion.version == version,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_version_for_flow_by_id(
        self,
        flow_id: UUID,
        flow_version_id: UUID,
        *,
        for_update: bool = False,
    ) -> RpaFlowVersion | None:
        statement = select(RpaFlowVersion).where(
            RpaFlowVersion.flow_id == flow_id,
            RpaFlowVersion.id == flow_version_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_published_versions(
        self,
        flow_id: UUID,
        *,
        exclude_id: UUID | None = None,
    ) -> Sequence[RpaFlowVersion]:
        statement = select(RpaFlowVersion).where(
            RpaFlowVersion.flow_id == flow_id,
            RpaFlowVersion.status == "PUBLISHED",
        )
        if exclude_id is not None:
            statement = statement.where(RpaFlowVersion.id != exclude_id)
        statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalars().all()

    def add_flow(self, flow: RpaFlow) -> None:
        self._session.add(flow)

    def add_version(self, version: RpaFlowVersion) -> None:
        self._session.add(version)

    def add_validation(self, validation: RpaFlowValidationRun) -> None:
        self._session.add(validation)

    def add_audit(self, audit: RpaFlowReleaseAudit) -> None:
        self._session.add(audit)

    async def flush(self) -> None:
        await self._session.flush()

    async def refresh(self, instance: object) -> None:
        await self._session.refresh(instance)

    @staticmethod
    def _visible_expression(
        tenant_id: str | None,
    ) -> ColumnElement[bool]:
        if tenant_id is None:
            return RpaFlow.scope == FlowScope.GLOBAL.value
        return or_(
            RpaFlow.scope == FlowScope.GLOBAL.value,
            (
                (RpaFlow.scope == FlowScope.TENANT.value)
                & (RpaFlow.tenant_id == tenant_id)
            ),
        )

    @classmethod
    def _visibility_filters(
        cls,
        *,
        tenant_id: str | None,
        scope: FlowScope | None,
    ) -> list[ColumnElement[bool]]:
        if scope is FlowScope.GLOBAL:
            return [
                RpaFlow.scope == FlowScope.GLOBAL.value,
                RpaFlow.tenant_id.is_(None),
            ]
        if scope is FlowScope.TENANT:
            if tenant_id is None:
                return [RpaFlow.id.is_(None)]
            return [
                RpaFlow.scope == FlowScope.TENANT.value,
                RpaFlow.tenant_id == tenant_id,
            ]
        return [cls._visible_expression(tenant_id)]
