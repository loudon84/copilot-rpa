from __future__ import annotations

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.db.session import DatabaseManager
from nodeskclaw_rpa_engine.flows.repository import SqlAlchemyFlowRepository
from nodeskclaw_rpa_engine.workers.errors import RunCommandRejected
from nodeskclaw_rpa_engine.workers.schemas import (
    LeaseRunCommand,
    ResolvedFlowVersion,
)


class FlowVersionResolver:
    def __init__(self, settings: Settings, database: DatabaseManager) -> None:
        self._settings = settings
        self._database = database

    async def resolve(self, command: LeaseRunCommand) -> ResolvedFlowVersion:
        async with self._database.session() as session:
            result = await SqlAlchemyFlowRepository(session).get_version_by_key(
                command.rpa_flow_id,
                command.rpa_flow_version,
                tenant_id=command.tenant_id,
            )
        if result is None:
            raise RunCommandRejected(
                "FLOW_VERSION_NOT_FOUND",
                "The exact Flow version from the lease was not found",
            )
        version, flow = result
        if flow.status != "ACTIVE" or version.status != "PUBLISHED":
            raise RunCommandRejected(
                "FLOW_VERSION_NOT_EXECUTABLE",
                "The exact Flow version is not published and active",
            )
        if not version.package_object_key or not version.package_checksum_sha256:
            raise RunCommandRejected(
                "FLOW_PACKAGE_METADATA_INCOMPLETE",
                "The exact Flow version has incomplete package metadata",
            )
        return ResolvedFlowVersion(
            flow_version_id=version.id,
            rpa_flow_id=flow.flow_key,
            version=version.version,
            engine_type=version.engine_type,
            package_uri=(
                f"{self._settings.rpa_engine_public_base_url}"
                f"/api/v1/flow-versions/{version.id}/package"
            ),
            package_checksum=version.package_checksum_sha256,
            package_object_key=version.package_object_key,
            supported_workflow_codes=list(version.supported_workflow_codes),
            capabilities=list(version.capabilities),
        )
