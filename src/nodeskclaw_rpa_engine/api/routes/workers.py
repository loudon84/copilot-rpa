from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status

from nodeskclaw_rpa_engine.api.routes.flows import actor_context
from nodeskclaw_rpa_engine.flows.schemas import ActorContext, ErrorResponse
from nodeskclaw_rpa_engine.workers.errors import WorkerError
from nodeskclaw_rpa_engine.workers.schemas import (
    WorkerListResponse,
    WorkerResponse,
    WorkerStatus,
)
from nodeskclaw_rpa_engine.workers.service import WorkerQueryService

router = APIRouter(prefix="/api/v1/workers", tags=["workers"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}


def worker_service(request: Request) -> WorkerQueryService:
    service: WorkerQueryService | None = request.app.state.worker_query_service
    if service is None:
        raise WorkerError(
            "WORKER_REGISTRY_UNAVAILABLE",
            "Worker Registry requires the database dependency",
            status_code=503,
        )
    return service


@router.get(
    "",
    response_model=WorkerListResponse,
    responses=ERROR_RESPONSES,
)
async def list_workers(
    _: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[WorkerQueryService, Depends(worker_service)],
    worker_status: Annotated[WorkerStatus | None, Query(alias="status")] = None,
    capability: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorkerListResponse:
    return await service.list_workers(
        status=worker_status,
        capability=capability,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{worker_id}",
    response_model=WorkerResponse,
    responses=ERROR_RESPONSES,
)
async def get_worker(
    worker_id: str,
    _: Annotated[ActorContext, Depends(actor_context)],
    service: Annotated[WorkerQueryService, Depends(worker_service)],
) -> WorkerResponse:
    return await service.get_worker(worker_id)
