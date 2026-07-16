from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse

from nodeskclaw_rpa_engine.flows.errors import FlowRegistryError
from nodeskclaw_rpa_engine.flows.schemas import (
    ActorContext,
    BindingValidationRequest,
    BindingValidationResponse,
    ErrorResponse,
    FlowDetail,
    FlowListResponse,
    FlowPackageUploadResponse,
    FlowScope,
    FlowStatus,
    FlowVersionListResponse,
    FlowVersionResponse,
    RollbackRequest,
    StatusChangeRequest,
    ValidationResponse,
)
from nodeskclaw_rpa_engine.flows.service import FlowRegistryService

router = APIRouter(prefix="/api/v1", tags=["flows"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}


def actor_context(
    actor_id: Annotated[
        str,
        Header(alias="X-Actor-Id", min_length=1, max_length=128),
    ],
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", min_length=1, max_length=128),
    ] = None,
) -> ActorContext:
    return ActorContext(actor_id=actor_id, tenant_id=tenant_id)


def flow_service(request: Request) -> FlowRegistryService:
    service: FlowRegistryService | None = request.app.state.flow_registry_service
    if service is None:
        raise FlowRegistryError(
            "FLOW_REGISTRY_UNAVAILABLE",
            "Flow Registry requires enabled database and object storage dependencies",
            status_code=503,
        )
    return service


@router.get(
    "/flows",
    response_model=FlowListResponse,
    responses=ERROR_RESPONSES,
)
async def list_flows(
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
    scope: Annotated[FlowScope | None, Query()] = None,
    flow_status: Annotated[FlowStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FlowListResponse:
    return await service.list_flows(
        actor,
        scope=scope,
        status=flow_status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/flows/packages",
    response_model=FlowPackageUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def upload_flow_package(
    request: Request,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
    package: Annotated[UploadFile, File()],
    scope: Annotated[FlowScope, Form()] = FlowScope.GLOBAL,
    description: Annotated[str | None, Form(max_length=5000)] = None,
    labels: Annotated[str, Form()] = "[]",
) -> FlowPackageUploadResponse:
    max_bytes: int = request.app.state.settings.flow_package_max_bytes
    content = await package.read(max_bytes + 1)
    parsed_labels = _parse_labels(labels)
    return await service.upload_package(
        actor,
        scope=scope,
        description=description,
        labels=parsed_labels,
        filename=package.filename,
        content=content,
    )


@router.get(
    "/flows/{rpa_flow_id}",
    response_model=FlowDetail,
    responses=ERROR_RESPONSES,
)
async def get_flow(
    rpa_flow_id: str,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
    scope: Annotated[FlowScope, Query()] = FlowScope.GLOBAL,
) -> FlowDetail:
    return await service.get_flow(actor, rpa_flow_id, scope=scope)


@router.get(
    "/flows/{rpa_flow_id}/versions",
    response_model=FlowVersionListResponse,
    responses=ERROR_RESPONSES,
)
async def list_flow_versions(
    rpa_flow_id: str,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
    scope: Annotated[FlowScope, Query()] = FlowScope.GLOBAL,
) -> FlowVersionListResponse:
    return await service.list_flow_versions(actor, rpa_flow_id, scope=scope)


@router.post(
    "/flows/{rpa_flow_id}/disable",
    response_model=FlowDetail,
    responses=ERROR_RESPONSES,
)
async def disable_flow(
    rpa_flow_id: str,
    body: StatusChangeRequest,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
    scope: Annotated[FlowScope, Query()] = FlowScope.GLOBAL,
) -> FlowDetail:
    return await service.disable_flow(
        actor,
        rpa_flow_id,
        scope=scope,
        reason=body.reason,
    )


@router.post(
    "/flows/{rpa_flow_id}/rollback",
    response_model=FlowVersionResponse,
    responses=ERROR_RESPONSES,
)
async def rollback_flow(
    rpa_flow_id: str,
    body: RollbackRequest,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
    scope: Annotated[FlowScope, Query()] = FlowScope.GLOBAL,
) -> FlowVersionResponse:
    return await service.rollback_flow(
        actor,
        rpa_flow_id,
        scope=scope,
        target_flow_version_id=body.target_flow_version_id,
        reason=body.reason,
    )


@router.post(
    "/flow-versions/validate-binding",
    response_model=BindingValidationResponse,
    responses=ERROR_RESPONSES,
)
async def validate_binding(
    body: BindingValidationRequest,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
) -> BindingValidationResponse:
    return await service.validate_binding(actor, body)


@router.get(
    "/flow-versions/{flow_version_id}",
    response_model=FlowVersionResponse,
    responses=ERROR_RESPONSES,
)
async def get_flow_version(
    flow_version_id: UUID,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
) -> FlowVersionResponse:
    return await service.get_version(actor, flow_version_id)


@router.post(
    "/flow-versions/{flow_version_id}/validate",
    response_model=ValidationResponse,
    responses=ERROR_RESPONSES,
)
async def validate_flow_version(
    flow_version_id: UUID,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
) -> ValidationResponse:
    return await service.validate_version(actor, flow_version_id)


@router.post(
    "/flow-versions/{flow_version_id}/publish",
    response_model=FlowVersionResponse,
    responses=ERROR_RESPONSES,
)
async def publish_flow_version(
    flow_version_id: UUID,
    body: StatusChangeRequest,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
) -> FlowVersionResponse:
    return await service.publish_version(
        actor,
        flow_version_id,
        reason=body.reason,
    )


@router.post(
    "/flow-versions/{flow_version_id}/deprecate",
    response_model=FlowVersionResponse,
    responses=ERROR_RESPONSES,
)
async def deprecate_flow_version(
    flow_version_id: UUID,
    body: StatusChangeRequest,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
) -> FlowVersionResponse:
    return await service.deprecate_version(
        actor,
        flow_version_id,
        reason=body.reason,
    )


@router.post(
    "/flow-versions/{flow_version_id}/disable",
    response_model=FlowVersionResponse,
    responses=ERROR_RESPONSES,
)
async def disable_flow_version(
    flow_version_id: UUID,
    body: StatusChangeRequest,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
) -> FlowVersionResponse:
    return await service.disable_version(
        actor,
        flow_version_id,
        reason=body.reason,
    )


@router.get(
    "/flow-versions/{flow_version_id}/package",
    response_class=RedirectResponse,
    responses=ERROR_RESPONSES,
)
async def download_flow_package(
    flow_version_id: UUID,
    actor: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[FlowRegistryService, Depends(flow_service)],
) -> RedirectResponse:
    download_url = await service.package_download_url(actor, flow_version_id)
    return RedirectResponse(
        download_url,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


def _parse_labels(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        raise FlowRegistryError(
            "FLOW_LABELS_INVALID",
            "labels must be a JSON array of unique non-blank strings",
            status_code=400,
        )
    normalized = [item.strip() for item in parsed]
    if (
        any(not item or len(item) > 128 for item in normalized)
        or len(set(normalized)) != len(normalized)
    ):
        raise FlowRegistryError(
            "FLOW_LABELS_INVALID",
            "labels must be a JSON array of unique non-blank strings",
            status_code=400,
        )
    return normalized
