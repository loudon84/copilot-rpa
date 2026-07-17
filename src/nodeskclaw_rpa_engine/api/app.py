from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from nodeskclaw_rpa_engine.api.routes.flows import router as flows_router
from nodeskclaw_rpa_engine.api.routes.health import router as health_router
from nodeskclaw_rpa_engine.api.routes.workers import router as workers_router
from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.core.health import ReadinessService
from nodeskclaw_rpa_engine.core.logging import configure_logging
from nodeskclaw_rpa_engine.db.session import DatabaseManager
from nodeskclaw_rpa_engine.flows.errors import FlowRegistryError
from nodeskclaw_rpa_engine.flows.schemas import ErrorBody, ErrorResponse
from nodeskclaw_rpa_engine.flows.service import FlowRegistryService
from nodeskclaw_rpa_engine.object_storage.factory import build_object_storage
from nodeskclaw_rpa_engine.runtime.artifacts import TaskArtifactSink
from nodeskclaw_rpa_engine.runtime.browser import ManagedBrowserSessionManager
from nodeskclaw_rpa_engine.runtime.callbacks import TaskRuntimeEventSink
from nodeskclaw_rpa_engine.runtime.credentials import build_credential_resolver
from nodeskclaw_rpa_engine.runtime.engine import RpaRuntime
from nodeskclaw_rpa_engine.runtime.filesystem import RuntimeFilesystemProbe
from nodeskclaw_rpa_engine.runtime.loader import (
    FlowLoader,
    ObjectStorageFlowPackageSource,
)
from nodeskclaw_rpa_engine.workers.errors import WorkerError
from nodeskclaw_rpa_engine.workers.outbox import (
    CallbackOutboxDispatcher,
    CallbackOutboxService,
)
from nodeskclaw_rpa_engine.workers.pool import RunCommandHandler, WorkerPool
from nodeskclaw_rpa_engine.workers.service import WorkerQueryService
from nodeskclaw_rpa_engine.workers.source import (
    LeaseRunCommandSource,
    RunCommandSource,
)
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient

logger = logging.getLogger(__name__)


async def _shutdown_component(
    name: str,
    closer: Callable[[], Awaitable[None]],
) -> None:
    try:
        await closer()
    except Exception:
        logger.exception(
            "RPA Engine shutdown component failed",
            extra={"component": name},
        )


def create_app(
    settings: Settings | None = None,
    *,
    readiness_service: ReadinessService | None = None,
    flow_registry_service: FlowRegistryService | None = None,
    worker_query_service: WorkerQueryService | None = None,
    task_client: TaskWorkerApiClient | None = None,
    worker_pool: WorkerPool | None = None,
    run_command_source: RunCommandSource | None = None,
    run_command_handler: RunCommandHandler | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(resolved_settings)

    if readiness_service is None:
        database_manager = (
            DatabaseManager.from_settings(resolved_settings)
            if resolved_settings.database_enabled
            else None
        )
        object_storage = build_object_storage(resolved_settings)
        resolved_task_client = task_client
        if (
            resolved_settings.worker_enabled or resolved_settings.runtime_enabled
        ) and resolved_task_client is None:
            resolved_task_client = TaskWorkerApiClient(resolved_settings)
        resolved_readiness = ReadinessService(
            resolved_settings,
            database_probe=database_manager,
            object_storage_probe=(
                object_storage if resolved_settings.minio_enabled else None
            ),
            task_api_probe=resolved_task_client,
            runtime_filesystem_probe=(
                RuntimeFilesystemProbe(
                    resolved_settings.runtime_cache_dir,
                    resolved_settings.runtime_work_dir,
                )
                if resolved_settings.runtime_enabled
                else None
            ),
        )
    else:
        # 测试会注入探针，绝不会构造真实的外部客户端。
        database_manager = None
        object_storage = build_object_storage(
            resolved_settings.model_copy(update={"minio_enabled": False})
        )
        resolved_readiness = readiness_service
        resolved_task_client = task_client

    resolved_flow_registry = flow_registry_service
    if (
        resolved_flow_registry is None
        and database_manager is not None
        and resolved_settings.minio_enabled
    ):
        resolved_flow_registry = FlowRegistryService(
            resolved_settings,
            database_manager,
            object_storage,
        )

    resolved_worker_query = worker_query_service
    if resolved_worker_query is None and database_manager is not None:
        resolved_worker_query = WorkerQueryService(
            resolved_settings,
            database_manager,
        )

    resolved_callback_outbox = (
        CallbackOutboxService(database_manager)
        if database_manager is not None
        else None
    )
    resolved_outbox_dispatcher = (
        CallbackOutboxDispatcher(
            database_manager,
            resolved_task_client,
            worker_id=resolved_settings.worker_id,
        )
        if (
            database_manager is not None
            and resolved_task_client is not None
            and (resolved_settings.worker_enabled or resolved_settings.runtime_enabled)
        )
        else None
    )

    resolved_runtime: RpaRuntime | None = None
    resolved_run_handler = run_command_handler
    if resolved_settings.runtime_enabled and resolved_run_handler is None:
        if resolved_task_client is None:
            raise ValueError("Enabled Runtime requires a Task API client")
        resolved_runtime = RpaRuntime(
            resolved_settings,
            loader=FlowLoader(
                resolved_settings,
                ObjectStorageFlowPackageSource(object_storage),
            ),
            browser_manager=ManagedBrowserSessionManager(),
            artifact_sink=TaskArtifactSink(
                resolved_task_client,
                worker_id=resolved_settings.worker_id,
            ),
            event_sink_factory=lambda command: TaskRuntimeEventSink(
                resolved_callback_outbox,
                lease_id=command.lease.lease_id,
                run_id=command.lease.run_id,
                worker_id=resolved_settings.worker_id,
            ),
            credential_resolver=build_credential_resolver(resolved_settings),
        )
        resolved_run_handler = resolved_runtime

    resolved_worker_pool = worker_pool
    if resolved_worker_pool is None and resolved_settings.worker_enabled:
        if database_manager is None or resolved_task_client is None:
            raise ValueError("Enabled Worker requires database and Task API clients")
        resolved_source = run_command_source
        if resolved_settings.worker_lease_enabled and resolved_source is None:
            resolved_source = LeaseRunCommandSource(
                resolved_task_client,
                worker_id=resolved_settings.worker_id,
                capabilities=resolved_settings.worker_capabilities,
            )
        resolved_worker_pool = WorkerPool(
            resolved_settings,
            database_manager,
            resolved_task_client,
            command_source=resolved_source,
            command_handler=resolved_run_handler,
            callback_outbox=resolved_callback_outbox,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("RPA Engine starting", extra=resolved_settings.public_snapshot())
        try:
            if resolved_outbox_dispatcher is not None:
                await resolved_outbox_dispatcher.start()
            if resolved_worker_pool is not None:
                await resolved_worker_pool.start()
            yield
        finally:
            if resolved_worker_pool is not None:
                await _shutdown_component("workerPool", resolved_worker_pool.stop)
            if resolved_outbox_dispatcher is not None:
                await _shutdown_component(
                    "callbackOutbox",
                    resolved_outbox_dispatcher.stop,
                )
            if resolved_task_client is not None:
                await _shutdown_component("taskApiClient", resolved_task_client.close)
            if database_manager is not None:
                await _shutdown_component("database", database_manager.close)
            await _shutdown_component("objectStorage", object_storage.close)
            logger.info("RPA Engine stopped")

    app = FastAPI(
        title="NoDeskClaw RPA Engine",
        version=resolved_settings.app_version,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.readiness_service = resolved_readiness
    app.state.database_manager = database_manager
    app.state.object_storage = object_storage
    app.state.flow_registry_service = resolved_flow_registry
    app.state.worker_query_service = resolved_worker_query
    app.state.worker_pool = resolved_worker_pool
    app.state.callback_outbox = resolved_callback_outbox
    app.state.outbox_dispatcher = resolved_outbox_dispatcher
    app.state.task_client = resolved_task_client
    app.state.runtime = resolved_runtime

    @app.exception_handler(FlowRegistryError)
    async def flow_registry_error_handler(
        _: Request,
        exc: FlowRegistryError,
    ) -> JSONResponse:
        response = ErrorResponse(
            error=ErrorBody(
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=response.model_dump(mode="json", by_alias=True),
        )

    @app.exception_handler(WorkerError)
    async def worker_error_handler(
        _: Request,
        exc: WorkerError,
    ) -> JSONResponse:
        response = ErrorResponse(
            error=ErrorBody(
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=response.model_dump(mode="json", by_alias=True),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in exc.errors()
        ]
        response = ErrorResponse(
            error=ErrorBody(
                code="REQUEST_VALIDATION_FAILED",
                message="Request validation failed",
                details=details,
            )
        )
        return JSONResponse(
            status_code=422,
            content=response.model_dump(mode="json", by_alias=True),
        )

    app.include_router(health_router)
    app.include_router(flows_router)
    app.include_router(workers_router)
    return app
