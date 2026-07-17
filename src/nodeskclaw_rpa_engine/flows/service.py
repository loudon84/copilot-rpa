from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.db.models import (
    RpaFlow,
    RpaFlowReleaseAudit,
    RpaFlowValidationRun,
    RpaFlowVersion,
)
from nodeskclaw_rpa_engine.db.session import DatabaseManager
from nodeskclaw_rpa_engine.flows.errors import (
    FlowRegistryError,
    PackageValidationError,
)
from nodeskclaw_rpa_engine.flows.package import (
    FlowPackageValidator,
    PackageLimits,
    ValidatedPackage,
)
from nodeskclaw_rpa_engine.flows.repository import SqlAlchemyFlowRepository
from nodeskclaw_rpa_engine.flows.schemas import (
    ActorContext,
    BindingValidationRequest,
    BindingValidationResponse,
    FlowDetail,
    FlowListResponse,
    FlowPackageUploadResponse,
    FlowScope,
    FlowStatus,
    FlowSummary,
    FlowVersionListResponse,
    FlowVersionResponse,
    FlowVersionStatus,
    ValidationResponse,
    ValidationStatus,
)
from nodeskclaw_rpa_engine.object_storage.base import ObjectStorageClient

logger = logging.getLogger(__name__)


class FlowRegistryService:
    def __init__(
        self,
        settings: Settings,
        database: DatabaseManager,
        object_storage: ObjectStorageClient,
    ) -> None:
        self._settings = settings
        self._database = database
        self._object_storage = object_storage
        self._validator = FlowPackageValidator(
            PackageLimits(
                max_bytes=settings.flow_package_max_bytes,
                max_uncompressed_bytes=(
                    settings.flow_package_max_uncompressed_bytes
                ),
                max_files=settings.flow_package_max_files,
                max_compression_ratio=(
                    settings.flow_package_max_compression_ratio
                ),
            )
        )

    async def list_flows(
        self,
        actor: ActorContext,
        *,
        scope: FlowScope | None,
        status: FlowStatus | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> FlowListResponse:
        async with self._database.session() as session:
            repository = SqlAlchemyFlowRepository(session)
            flows, total = await repository.list_flows(
                tenant_id=actor.tenant_id,
                scope=scope,
                status=status.value if status is not None else None,
                search=search,
                limit=limit,
                offset=offset,
            )
            return FlowListResponse(
                items=[self._flow_response(flow) for flow in flows],
                total=total,
                limit=limit,
                offset=offset,
            )

    async def get_flow(
        self,
        actor: ActorContext,
        flow_key: str,
        *,
        scope: FlowScope,
    ) -> FlowDetail:
        self._require_tenant_for_scope(actor, scope)
        async with self._database.session() as session:
            repository = SqlAlchemyFlowRepository(session)
            flow = await repository.get_flow(
                flow_key,
                scope=scope,
                tenant_id=actor.tenant_id,
            )
            if flow is None:
                raise self._not_found("FLOW_NOT_FOUND", "Flow was not found")
            versions = await repository.list_versions(flow.id)
            return FlowDetail(
                **self._flow_response(flow).model_dump(),
                versions=[
                    self._version_response(version, flow) for version in versions
                ],
            )

    async def get_version(
        self,
        actor: ActorContext,
        flow_version_id: UUID,
    ) -> FlowVersionResponse:
        async with self._database.session() as session:
            repository = SqlAlchemyFlowRepository(session)
            result = await repository.get_version(
                flow_version_id,
                tenant_id=actor.tenant_id,
            )
            if result is None:
                raise self._not_found(
                    "FLOW_VERSION_NOT_FOUND",
                    "Flow Version was not found",
                )
            version, flow = result
            return self._version_response(version, flow)

    async def list_flow_versions(
        self,
        actor: ActorContext,
        flow_key: str,
        *,
        scope: FlowScope,
    ) -> FlowVersionListResponse:
        detail = await self.get_flow(actor, flow_key, scope=scope)
        return FlowVersionListResponse(items=detail.versions)

    async def upload_package(
        self,
        actor: ActorContext,
        *,
        scope: FlowScope,
        description: str | None,
        labels: list[str],
        filename: str | None,
        content: bytes,
    ) -> FlowPackageUploadResponse:
        self._require_tenant_for_scope(actor, scope)
        package = await asyncio.to_thread(self._validator.validate, filename, content)
        manifest = package.manifest
        object_key = self._package_object_key(
            package,
            scope=scope,
            tenant_id=actor.tenant_id,
        )
        put_started = False
        db_body_prepared = False
        commit_confirmed = False

        try:
            put_started = True
            await self._object_storage.put_package(
                object_key,
                package.content,
                checksum_sha256=package.checksum_sha256,
            )

            async with self._database.session() as session:
                async with session.begin():
                    repository = SqlAlchemyFlowRepository(session)
                    flow = await repository.get_flow(
                        manifest.rpa_flow_id,
                        scope=scope,
                        tenant_id=actor.tenant_id,
                        for_update=True,
                    )
                    if flow is None:
                        flow = RpaFlow(
                            flow_key=manifest.rpa_flow_id,
                            scope=scope.value,
                            tenant_id=(
                                actor.tenant_id
                                if scope is FlowScope.TENANT
                                else None
                            ),
                            name=manifest.name,
                            description=description,
                            status=FlowStatus.ACTIVE.value,
                            labels=labels,
                            created_by=actor.actor_id,
                        )
                        repository.add_flow(flow)
                        await repository.flush()
                    else:
                        if flow.status != FlowStatus.ACTIVE.value:
                            raise FlowRegistryError(
                                "FLOW_NOT_ACTIVE",
                                "A new version cannot be added to an inactive Flow",
                                status_code=409,
                            )
                        flow.name = manifest.name
                        if description is not None:
                            flow.description = description
                        if labels:
                            flow.labels = labels

                    existing = await repository.get_version_for_flow(
                        flow.id,
                        manifest.version,
                        for_update=True,
                    )
                    if existing is not None:
                        raise FlowRegistryError(
                            "FLOW_VERSION_EXISTS",
                            "The Flow version already exists and cannot be overwritten",
                            status_code=409,
                        )

                    version = self._new_version(
                        flow=flow,
                        actor=actor,
                        package=package,
                        object_key=object_key,
                    )
                    repository.add_version(version)
                    await repository.flush()

                    validation = self._new_validation(
                        version.id,
                        actor.actor_id,
                        trigger_type="UPLOAD",
                        status=ValidationStatus.PASSED,
                        checks=package.checks,
                        errors=[],
                        warnings=package.warnings,
                        summary=(
                            "Package structure, manifest, entrypoint, and "
                            "checksum passed"
                        ),
                    )
                    repository.add_validation(validation)
                    repository.add_audit(
                        self._new_audit(
                            flow,
                            version,
                            actor.actor_id,
                            action="UPLOADED",
                            to_status=version.status,
                            details={
                                "checksumSha256": package.checksum_sha256,
                                "packageSizeBytes": package.size_bytes,
                            },
                        )
                    )
                    await repository.flush()
                    await repository.refresh(flow)
                    await repository.refresh(version)
                    await repository.refresh(validation)
                    db_body_prepared = True
                commit_confirmed = True
        except asyncio.CancelledError:
            if put_started and not db_body_prepared and not commit_confirmed:
                await asyncio.shield(self._delete_failed_upload(object_key))
            raise
        except IntegrityError as exc:
            if put_started and not commit_confirmed:
                await self._delete_failed_upload(object_key)
            raise FlowRegistryError(
                "FLOW_VERSION_EXISTS",
                "The Flow or version conflicts with an existing record",
                status_code=409,
            ) from exc
        except FlowRegistryError:
            if put_started and not db_body_prepared and not commit_confirmed:
                await self._delete_failed_upload(object_key)
            raise
        except Exception as exc:
            if put_started and not db_body_prepared and not commit_confirmed:
                await self._delete_failed_upload(object_key)
            raise FlowRegistryError(
                "FLOW_PACKAGE_STORAGE_FAILED",
                "Flow package could not be persisted",
                status_code=503,
            ) from exc

        return FlowPackageUploadResponse(
            flow=self._flow_response(flow),
            version=self._version_response(version, flow),
            validation=self._validation_response(validation),
        )

    async def validate_version(
        self,
        actor: ActorContext,
        flow_version_id: UUID,
    ) -> ValidationResponse:
        version, _, package = await self._load_and_validate_package(
            actor,
            flow_version_id,
            raise_on_invalid=False,
        )
        errors = package if isinstance(package, list) else []
        validated = package if isinstance(package, ValidatedPackage) else None
        status = (
            ValidationStatus.PASSED
            if validated is not None
            else ValidationStatus.FAILED
        )
        async with self._database.session() as session:
            async with session.begin():
                repository = SqlAlchemyFlowRepository(session)
                validation = self._new_validation(
                    version.id,
                    actor.actor_id,
                    trigger_type="MANUAL",
                    status=status,
                    checks=validated.checks if validated is not None else [],
                    errors=errors,
                    warnings=(
                        validated.warnings if validated is not None else []
                    ),
                    summary=(
                        "Manual package validation passed"
                        if validated is not None
                        else "Manual package validation failed"
                    ),
                )
                repository.add_validation(validation)
                await repository.flush()
                await repository.refresh(validation)
        return self._validation_response(validation)

    async def publish_version(
        self,
        actor: ActorContext,
        flow_version_id: UUID,
        *,
        reason: str | None,
    ) -> FlowVersionResponse:
        _, _, package_result = await self._load_and_validate_package(
            actor,
            flow_version_id,
            raise_on_invalid=False,
        )
        validation_errors = (
            package_result if isinstance(package_result, list) else []
        )
        validated = (
            package_result
            if isinstance(package_result, ValidatedPackage)
            else None
        )

        async with self._database.session() as session:
            async with session.begin():
                repository = SqlAlchemyFlowRepository(session)
                result = await repository.get_version(
                    flow_version_id,
                    tenant_id=actor.tenant_id,
                    for_update=True,
                )
                if result is None:
                    raise self._not_found(
                        "FLOW_VERSION_NOT_FOUND",
                        "Flow Version was not found",
                    )
                version, flow = result
                if validated is None:
                    validation = self._new_validation(
                        version.id,
                        actor.actor_id,
                        trigger_type="PUBLISH",
                        status=ValidationStatus.FAILED,
                        checks=[],
                        errors=validation_errors,
                        warnings=[],
                        summary="Publish validation failed",
                    )
                    repository.add_validation(validation)
                    await repository.flush()
                else:
                    from_status = version.status
                    if from_status == FlowVersionStatus.PUBLISHED.value:
                        return self._version_response(version, flow)
                    if from_status not in {
                        FlowVersionStatus.DRAFT.value,
                        FlowVersionStatus.VALIDATING.value,
                        FlowVersionStatus.DEPRECATED.value,
                    }:
                        raise self._invalid_transition(from_status, "PUBLISHED")
                    if from_status == FlowVersionStatus.DRAFT.value:
                        version.status = FlowVersionStatus.VALIDATING.value
                        await repository.flush()
                    version.status = FlowVersionStatus.PUBLISHED.value
                    validation = self._new_validation(
                        version.id,
                        actor.actor_id,
                        trigger_type="PUBLISH",
                        status=ValidationStatus.PASSED,
                        checks=validated.checks,
                        errors=[],
                        warnings=validated.warnings,
                        summary="Publish validation passed",
                    )
                    repository.add_validation(validation)
                    repository.add_audit(
                        self._new_audit(
                            flow,
                            version,
                            actor.actor_id,
                            action="PUBLISHED",
                            from_status=from_status,
                            to_status=FlowVersionStatus.PUBLISHED.value,
                            reason=reason,
                        )
                    )
                    await repository.flush()
                    await repository.refresh(version)

        if validated is None:
            raise PackageValidationError(validation_errors)
        return self._version_response(version, flow)

    async def deprecate_version(
        self,
        actor: ActorContext,
        flow_version_id: UUID,
        *,
        reason: str | None,
    ) -> FlowVersionResponse:
        return await self._change_version_status(
            actor,
            flow_version_id,
            target=FlowVersionStatus.DEPRECATED,
            action="DEPRECATED",
            reason=reason,
            allowed={FlowVersionStatus.PUBLISHED},
        )

    async def disable_version(
        self,
        actor: ActorContext,
        flow_version_id: UUID,
        *,
        reason: str | None,
    ) -> FlowVersionResponse:
        return await self._change_version_status(
            actor,
            flow_version_id,
            target=FlowVersionStatus.DISABLED,
            action="DISABLED",
            reason=reason,
            allowed={
                FlowVersionStatus.DRAFT,
                FlowVersionStatus.VALIDATING,
                FlowVersionStatus.PUBLISHED,
                FlowVersionStatus.DEPRECATED,
            },
        )

    async def disable_flow(
        self,
        actor: ActorContext,
        flow_key: str,
        *,
        scope: FlowScope,
        reason: str | None,
    ) -> FlowDetail:
        self._require_tenant_for_scope(actor, scope)
        async with self._database.session() as session:
            async with session.begin():
                repository = SqlAlchemyFlowRepository(session)
                flow = await repository.get_flow(
                    flow_key,
                    scope=scope,
                    tenant_id=actor.tenant_id,
                    for_update=True,
                )
                if flow is None:
                    raise self._not_found("FLOW_NOT_FOUND", "Flow was not found")
                from_status = flow.status
                flow.status = FlowStatus.DISABLED.value
                repository.add_audit(
                    self._new_audit(
                        flow,
                        None,
                        actor.actor_id,
                        action="DISABLED",
                        from_status=from_status,
                        to_status=FlowStatus.DISABLED.value,
                        reason=reason,
                    )
                )
                await repository.flush()
                await repository.refresh(flow)
                versions = await repository.list_versions(flow.id)
        return FlowDetail(
            **self._flow_response(flow).model_dump(),
            versions=[self._version_response(item, flow) for item in versions],
        )

    async def rollback_flow(
        self,
        actor: ActorContext,
        flow_key: str,
        *,
        scope: FlowScope,
        target_flow_version_id: UUID,
        reason: str | None,
    ) -> FlowVersionResponse:
        self._require_tenant_for_scope(actor, scope)
        async with self._database.session() as session:
            async with session.begin():
                repository = SqlAlchemyFlowRepository(session)
                flow = await repository.get_flow(
                    flow_key,
                    scope=scope,
                    tenant_id=actor.tenant_id,
                    for_update=True,
                )
                if flow is None:
                    raise self._not_found("FLOW_NOT_FOUND", "Flow was not found")
                target = await repository.get_version_for_flow_by_id(
                    flow.id,
                    target_flow_version_id,
                    for_update=True,
                )
                if target is None:
                    raise self._not_found(
                        "FLOW_VERSION_NOT_FOUND",
                        "Rollback target was not found",
                    )
                if target.status not in {
                    FlowVersionStatus.DEPRECATED.value,
                    FlowVersionStatus.PUBLISHED.value,
                }:
                    raise self._invalid_transition(target.status, "PUBLISHED")

                current_versions = await repository.list_published_versions(
                    flow.id,
                    exclude_id=target.id,
                )
                deprecated_ids: list[str] = []
                for current in current_versions:
                    current.status = FlowVersionStatus.DEPRECATED.value
                    deprecated_ids.append(str(current.id))
                if current_versions:
                    await repository.flush()
                from_status = target.status
                target.status = FlowVersionStatus.PUBLISHED.value
                repository.add_audit(
                    self._new_audit(
                        flow,
                        target,
                        actor.actor_id,
                        action="ROLLED_BACK",
                        from_status=from_status,
                        to_status=FlowVersionStatus.PUBLISHED.value,
                        reason=reason,
                        details={"deprecatedFlowVersionIds": deprecated_ids},
                    )
                )
                await repository.flush()
                await repository.refresh(target)
        return self._version_response(target, flow)

    async def validate_binding(
        self,
        actor: ActorContext,
        request: BindingValidationRequest,
    ) -> BindingValidationResponse:
        async with self._database.session() as session:
            repository = SqlAlchemyFlowRepository(session)
            if request.rpa_flow_version_id is not None:
                result = await repository.get_version(
                    request.rpa_flow_version_id,
                    tenant_id=actor.tenant_id,
                )
            else:
                assert request.rpa_flow_id is not None
                assert request.rpa_flow_version is not None
                result = await repository.get_version_by_key(
                    request.rpa_flow_id,
                    request.rpa_flow_version,
                    tenant_id=actor.tenant_id,
                )
            if result is None:
                return BindingValidationResponse(
                    valid=False,
                    reason_code="FLOW_VERSION_NOT_FOUND",
                    version=None,
                )
            version, flow = result
            response = self._version_response(version, flow)
            if flow.status != FlowStatus.ACTIVE.value:
                return BindingValidationResponse(
                    valid=False,
                    reason_code="FLOW_NOT_ACTIVE",
                    version=response,
                )
            if version.status != FlowVersionStatus.PUBLISHED.value:
                return BindingValidationResponse(
                    valid=False,
                    reason_code="FLOW_VERSION_NOT_PUBLISHED",
                    version=response,
                )
            if (
                request.workflow_code is not None
                and request.workflow_code not in version.supported_workflow_codes
            ):
                return BindingValidationResponse(
                    valid=False,
                    reason_code="WORKFLOW_CODE_NOT_SUPPORTED",
                    version=response,
                )
            return BindingValidationResponse(
                valid=True,
                reason_code=None,
                version=response,
            )

    async def package_download_url(
        self,
        actor: ActorContext,
        flow_version_id: UUID,
    ) -> str:
        async with self._database.session() as session:
            repository = SqlAlchemyFlowRepository(session)
            result = await repository.get_version(
                flow_version_id,
                tenant_id=actor.tenant_id,
            )
            if result is None:
                raise self._not_found(
                    "FLOW_VERSION_NOT_FOUND",
                    "Flow Version was not found",
                )
            version, _ = result
            if not version.package_object_key:
                raise FlowRegistryError(
                    "FLOW_PACKAGE_NOT_AVAILABLE",
                    "Flow package is not available",
                    status_code=409,
                )
            object_key = version.package_object_key
        try:
            return await self._object_storage.presign_download(
                object_key,
                expires_seconds=self._settings.flow_package_url_ttl_seconds,
            )
        except Exception as exc:
            raise FlowRegistryError(
                "FLOW_PACKAGE_STORAGE_FAILED",
                "Flow package download URL could not be generated",
                status_code=503,
            ) from exc

    async def _change_version_status(
        self,
        actor: ActorContext,
        flow_version_id: UUID,
        *,
        target: FlowVersionStatus,
        action: str,
        reason: str | None,
        allowed: set[FlowVersionStatus],
    ) -> FlowVersionResponse:
        async with self._database.session() as session:
            async with session.begin():
                repository = SqlAlchemyFlowRepository(session)
                result = await repository.get_version(
                    flow_version_id,
                    tenant_id=actor.tenant_id,
                    for_update=True,
                )
                if result is None:
                    raise self._not_found(
                        "FLOW_VERSION_NOT_FOUND",
                        "Flow Version was not found",
                    )
                version, flow = result
                if version.status == target.value:
                    return self._version_response(version, flow)
                if FlowVersionStatus(version.status) not in allowed:
                    raise self._invalid_transition(version.status, target.value)
                from_status = version.status
                version.status = target.value
                repository.add_audit(
                    self._new_audit(
                        flow,
                        version,
                        actor.actor_id,
                        action=action,
                        from_status=from_status,
                        to_status=target.value,
                        reason=reason,
                    )
                )
                await repository.flush()
                await repository.refresh(version)
        return self._version_response(version, flow)

    async def _load_and_validate_package(
        self,
        actor: ActorContext,
        flow_version_id: UUID,
        *,
        raise_on_invalid: bool,
    ) -> tuple[
        RpaFlowVersion,
        RpaFlow,
        ValidatedPackage | list[dict[str, Any]],
    ]:
        async with self._database.session() as session:
            repository = SqlAlchemyFlowRepository(session)
            result = await repository.get_version(
                flow_version_id,
                tenant_id=actor.tenant_id,
            )
            if result is None:
                raise self._not_found(
                    "FLOW_VERSION_NOT_FOUND",
                    "Flow Version was not found",
                )
            version, flow = result
            object_key = version.package_object_key
            expected_checksum = version.package_checksum_sha256
        if not object_key or not expected_checksum:
            raise FlowRegistryError(
                "FLOW_PACKAGE_NOT_AVAILABLE",
                "Flow package metadata is incomplete",
                status_code=409,
            )
        try:
            content = await self._object_storage.get_package(object_key)
        except Exception as exc:
            raise FlowRegistryError(
                "FLOW_PACKAGE_STORAGE_FAILED",
                "Flow package could not be loaded",
                status_code=503,
            ) from exc
        try:
            package = await asyncio.to_thread(
                self._validator.validate,
                "package.zip",
                content,
            )
            if package.checksum_sha256 != expected_checksum:
                raise PackageValidationError(
                    [
                        {
                            "code": "PACKAGE_CHECKSUM_MISMATCH",
                            "message": (
                                "Stored package checksum does not match metadata"
                            ),
                        }
                    ]
                )
            if (
                package.manifest.rpa_flow_id != flow.flow_key
                or package.manifest.version != version.version
            ):
                raise PackageValidationError(
                    [
                        {
                            "code": "PACKAGE_IDENTITY_MISMATCH",
                            "message": (
                                "Stored manifest identity does not match metadata"
                            ),
                        }
                    ]
                )
            return version, flow, package
        except PackageValidationError as exc:
            if raise_on_invalid:
                raise
            details = exc.details if isinstance(exc.details, list) else []
            return version, flow, details

    def _new_version(
        self,
        *,
        flow: RpaFlow,
        actor: ActorContext,
        package: ValidatedPackage,
        object_key: str,
    ) -> RpaFlowVersion:
        manifest = package.manifest
        manifest_json = manifest.model_dump(by_alias=True, mode="json")
        return RpaFlowVersion(
            flow_id=flow.id,
            version=manifest.version,
            status=FlowVersionStatus.DRAFT.value,
            engine_type=manifest.engine_type,
            entrypoint=manifest.entrypoint,
            manifest=manifest_json,
            supported_workflow_codes=manifest.supported_workflow_codes,
            supported_portal_types=manifest.supported_portal_types,
            input_schema=[
                item.model_dump(mode="json") for item in manifest.input_schema
            ],
            capabilities=manifest.capabilities,
            minimum_engine_version=manifest.minimum_engine_version,
            package_bucket=self._object_storage.bucket_name,
            package_object_key=object_key,
            package_size_bytes=package.size_bytes,
            package_checksum_sha256=package.checksum_sha256,
            created_by=actor.actor_id,
        )

    @staticmethod
    def _new_validation(
        version_id: UUID,
        actor_id: str,
        *,
        trigger_type: str,
        status: ValidationStatus,
        checks: list[Any],
        errors: list[Any],
        warnings: list[Any],
        summary: str,
    ) -> RpaFlowValidationRun:
        now = datetime.now(UTC)
        return RpaFlowValidationRun(
            flow_version_id=version_id,
            trigger_type=trigger_type,
            status=status.value,
            checks=checks,
            errors=errors,
            warnings=warnings,
            result_summary=summary,
            requested_by=actor_id,
            started_at=now,
            ended_at=now,
        )

    @staticmethod
    def _new_audit(
        flow: RpaFlow,
        version: RpaFlowVersion | None,
        actor_id: str,
        *,
        action: str,
        from_status: str | None = None,
        to_status: str | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> RpaFlowReleaseAudit:
        return RpaFlowReleaseAudit(
            flow_id=flow.id,
            flow_version_id=version.id if version is not None else None,
            action=action,
            from_status=from_status,
            to_status=to_status,
            actor_id=actor_id,
            reason=reason,
            details=details or {},
        )

    def _flow_response(self, flow: RpaFlow) -> FlowSummary:
        return FlowSummary(
            id=flow.id,
            rpa_flow_id=flow.flow_key,
            scope=FlowScope(flow.scope),
            tenant_id=flow.tenant_id,
            name=flow.name,
            description=flow.description,
            status=FlowStatus(flow.status),
            labels=list(flow.labels),
            created_by=flow.created_by,
            created_at=flow.created_at,
            updated_at=flow.updated_at,
        )

    def _version_response(
        self,
        version: RpaFlowVersion,
        flow: RpaFlow,
    ) -> FlowVersionResponse:
        package_uri = None
        if version.package_object_key:
            package_uri = (
                f"{self._settings.rpa_engine_public_base_url}"
                f"/api/v1/flow-versions/{version.id}/package"
            )
        return FlowVersionResponse(
            rpa_flow_version_id=version.id,
            rpa_flow_id=flow.flow_key,
            version=version.version,
            status=FlowVersionStatus(version.status),
            engine_type=version.engine_type,
            entrypoint=version.entrypoint,
            manifest=dict(version.manifest),
            supported_workflow_codes=list(version.supported_workflow_codes),
            supported_portal_types=list(version.supported_portal_types),
            input_schema=list(version.input_schema),
            capabilities=list(version.capabilities),
            minimum_engine_version=version.minimum_engine_version,
            package_uri=package_uri,
            package_size_bytes=version.package_size_bytes,
            package_checksum=(
                f"sha256:{version.package_checksum_sha256}"
                if version.package_checksum_sha256
                else None
            ),
            created_by=version.created_by,
            created_at=version.created_at,
            published_at=version.published_at,
            updated_at=version.updated_at,
        )

    @staticmethod
    def _validation_response(
        validation: RpaFlowValidationRun,
    ) -> ValidationResponse:
        return ValidationResponse(
            validation_run_id=validation.id,
            flow_version_id=validation.flow_version_id,
            trigger_type=validation.trigger_type,
            status=ValidationStatus(validation.status),
            checks=list(validation.checks),
            errors=list(validation.errors),
            warnings=list(validation.warnings),
            result_summary=validation.result_summary,
            requested_by=validation.requested_by,
            started_at=validation.started_at,
            ended_at=validation.ended_at,
            created_at=validation.created_at,
        )

    @staticmethod
    def _package_object_key(
        package: ValidatedPackage,
        *,
        scope: FlowScope,
        tenant_id: str | None,
    ) -> str:
        manifest = package.manifest
        if scope is FlowScope.TENANT:
            if tenant_id is None:
                raise FlowRegistryError(
                    "TENANT_CONTEXT_REQUIRED",
                    "X-Tenant-Id is required for TENANT Flow operations",
                    status_code=400,
                )
            tenant_fragment = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
            namespace = f"tenant/{tenant_fragment}"
        else:
            namespace = "global"
        upload_id = uuid4().hex
        return (
            f"flows/{namespace}/{manifest.rpa_flow_id}/{manifest.version}/"
            f"{upload_id}-{package.checksum_sha256}.zip"
        )

    async def _delete_failed_upload(self, object_key: str) -> None:
        try:
            await self._object_storage.delete_package(object_key)
        except Exception:
            logger.exception(
                "Failed to clean up an uncommitted Flow package",
                extra={"objectKey": object_key},
            )

    @staticmethod
    def _require_tenant_for_scope(
        actor: ActorContext,
        scope: FlowScope,
    ) -> None:
        if scope is FlowScope.TENANT and actor.tenant_id is None:
            raise FlowRegistryError(
                "TENANT_CONTEXT_REQUIRED",
                "X-Tenant-Id is required for TENANT Flow operations",
                status_code=400,
            )

    @staticmethod
    def _not_found(code: str, message: str) -> FlowRegistryError:
        return FlowRegistryError(code, message, status_code=404)

    @staticmethod
    def _invalid_transition(from_status: str, to_status: str) -> FlowRegistryError:
        return FlowRegistryError(
            "FLOW_VERSION_TRANSITION_INVALID",
            f"Flow Version cannot transition from {from_status} to {to_status}",
            status_code=409,
        )
