from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from nodeskclaw_rpa_engine.core.health import (
    LivenessResponse,
    ReadinessResponse,
    ReadinessService,
)

router = APIRouter(tags=["health"])


def _readiness_service(request: Request) -> ReadinessService:
    service: ReadinessService = request.app.state.readiness_service
    return service


@router.get("/health/live", response_model=LivenessResponse)
async def live(request: Request) -> LivenessResponse:
    return _readiness_service(request).liveness()


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(request: Request) -> ReadinessResponse | JSONResponse:
    response, is_ready = await _readiness_service(request).readiness()
    if not is_ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(mode="json"),
        )
    return response
