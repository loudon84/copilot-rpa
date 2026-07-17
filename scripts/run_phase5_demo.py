from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import shutil
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import uvicorn

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.mock_srm.app import create_mock_srm_app
from nodeskclaw_rpa_engine.runtime.artifacts import ArtifactType
from nodeskclaw_rpa_engine.runtime.browser import ManagedBrowserSessionManager
from nodeskclaw_rpa_engine.runtime.engine import RpaRuntime
from nodeskclaw_rpa_engine.runtime.loader import FlowLoader
from nodeskclaw_rpa_engine.workers.schemas import (
    AttemptStatus,
    LeaseRunCommand,
    ResolvedFlowVersion,
    RunCommand,
)

SCENARIOS = {
    "success": ("PO-20260708-001", AttemptStatus.SUCCESS),
    "failed": ("PO-NOT-FOUND", AttemptStatus.FAILED),
    "waiting_human": ("PO-MANUAL-001", AttemptStatus.WAITING_HUMAN),
}


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


class MemoryPackageSource:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def fetch(self, _flow: ResolvedFlowVersion) -> bytes:
        return self._content


class DemoCredentialResolver:
    async def resolve(
        self,
        credential_ref: str | None,
        *,
        tenant_id: str | None,
        portal_account_id: str | None,
    ) -> dict[str, str]:
        if (
            credential_ref != "credential-ref-mock-srm"
            or tenant_id != "tenant-phase5-demo"
            or portal_account_id != "portal-account-mock-srm"
        ):
            raise ValueError("Unexpected Phase 5 credential scope")
        return {"username": "phase5-demo", "password": uuid4().hex}


class LocalArtifactSink:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self.items: dict[str, list[dict[str, Any]]] = {}

    async def upload(
        self,
        *,
        task_id: str,
        run_id: str,
        artifact_type: ArtifactType,
        name: str,
        path: Path,
        size: int,
        mime_type: str,
    ) -> str:
        destination = self._root / run_id / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, path, destination)
        storage_key = destination.relative_to(self._root).as_posix()
        self.items.setdefault(run_id, []).append(
            {
                "taskId": task_id,
                "type": artifact_type.value,
                "name": name,
                "size": size,
                "mimeType": mime_type,
                "storageKey": storage_key,
            }
        )
        return storage_key


class LocalEventSink:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def emit(
        self,
        event_type: str,
        *,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.items.append(
            {
                "type": event_type,
                "level": level,
                "message": message,
                "payload": payload or {},
            }
        )


class LocalMockSrmServer:
    def __init__(self, portal_url: str) -> None:
        parsed = urlsplit(portal_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("The embedded Mock SRM server must use local HTTP")
        self._host = "127.0.0.1"
        self._port = parsed.port or 80
        self._health_url = f"http://127.0.0.1:{self._port}/health/live"
        self._server = uvicorn.Server(
            uvicorn.Config(
                create_mock_srm_app(),
                host=self._host,
                port=self._port,
                log_level="warning",
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            for _ in range(50):
                if not self._thread.is_alive():
                    raise RuntimeError("Mock SRM server stopped during startup")
                try:
                    if client.get(self._health_url).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("Mock SRM server did not become ready")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def build_package(root: Path) -> bytes:
    source = root / "examples" / "mock-srm-flow" / "1.0.0"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "selectors.json", "flow.py"):
            archive.write(source / name, name)
    return output.getvalue()


def command(
    *,
    scenario: str,
    po_no: str,
    portal_url: str,
    channel: str,
    headless: bool,
    checksum: str,
) -> RunCommand:
    run_id = f"phase5-{scenario}-{uuid4().hex[:8]}"
    lease = LeaseRunCommand.model_validate(
        {
            "taskId": f"task-{run_id}",
            "runId": run_id,
            "leaseId": f"lease-{run_id}",
            "workflowBindingId": "binding-phase5-demo",
            "portalAccountId": "portal-account-mock-srm",
            "rpaFlowId": "rpa_flow_mock_srm_fetch_po",
            "input": {"po_no": po_no},
            "tenantId": "tenant-phase5-demo",
            "workflowTemplateId": "template-srm-fetch-po",
            "workflowCode": "srm_fetch_po",
            "rpaEngineType": "PLAYWRIGHT_CDP",
            "rpaFlowVersion": "1.0.0",
            "credentialRef": "credential-ref-mock-srm",
            "config": {
                "portalUrl": portal_url,
                "browserSession": {
                    "mode": "MANAGED",
                    "headless": headless,
                    "channel": channel,
                    "profileRef": None,
                    "cdpEndpointRef": None,
                    "closePolicy": "CLOSE_ON_FINISH",
                },
            },
            "leaseExpiresAt": (
                datetime.now(UTC) + timedelta(minutes=10)
            ).isoformat(),
        }
    )
    return RunCommand(
        lease=lease,
        flow=ResolvedFlowVersion(
            flow_version_id=uuid4(),
            rpa_flow_id="rpa_flow_mock_srm_fetch_po",
            version="1.0.0",
            engine_type="PLAYWRIGHT_CDP",
            package_uri="memory://phase5/mock-srm-flow.zip",
            package_checksum=checksum,
            package_object_key="phase5/mock-srm-flow.zip",
            supported_workflow_codes=["srm_fetch_po"],
            capabilities=[
                "PLAYWRIGHT_CDP",
                "BROWSER_SESSION_MANAGED",
                "SCREENSHOT",
                "DOWNLOAD",
            ],
        ),
    )


async def run_demo(args: argparse.Namespace) -> int:
    root = repository_root()
    package = build_package(root)
    checksum = hashlib.sha256(package).hexdigest()
    runtime_root = root / "runtime-cache" / "phase5-demo"
    settings = Settings(
        _env_file=None,
        app_env="test",
        runtime_cache_dir=runtime_root / "flows",
        runtime_work_dir=runtime_root / "runs",
        runtime_max_retries=0,
        runtime_retry_backoff_seconds=0,
        runtime_trace_mode="ON_FAILURE",
        runtime_cleanup_on_finish=True,
    )
    artifact_sink = LocalArtifactSink(runtime_root / "artifacts")
    event_sinks: dict[str, LocalEventSink] = {}

    def event_sink_factory(run_command: RunCommand) -> LocalEventSink:
        sink = LocalEventSink()
        event_sinks[run_command.lease.run_id] = sink
        return sink

    runtime = RpaRuntime(
        settings,
        loader=FlowLoader(settings, MemoryPackageSource(package)),
        browser_manager=ManagedBrowserSessionManager(),
        artifact_sink=artifact_sink,
        event_sink_factory=event_sink_factory,
        credential_resolver=DemoCredentialResolver(),
    )
    selected = SCENARIOS if args.scenario == "all" else {
        args.scenario: SCENARIOS[args.scenario]
    }
    summary: list[dict[str, Any]] = []
    exit_code = 0
    for scenario, (po_no, expected) in selected.items():
        run_command = command(
            scenario=scenario,
            po_no=po_no,
            portal_url=args.portal_url,
            channel=args.channel,
            headless=not args.headful,
            checksum=checksum,
        )
        result = await runtime.handle(run_command)
        run_id = run_command.lease.run_id
        if result.status is not expected:
            exit_code = 1
        summary.append(
            {
                "scenario": scenario,
                "poNo": po_no,
                "expected": expected.value,
                "actual": result.status.value,
                "errorCode": result.error_code,
                "artifacts": artifact_sink.items.get(run_id, []),
                "events": [item["type"] for item in event_sinks[run_id].items],
            }
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"artifactDirectory={runtime_root / 'artifacts'}")
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Phase 5 browser demo")
    parser.add_argument(
        "--scenario",
        choices=["all", *SCENARIOS],
        default="all",
    )
    parser.add_argument(
        "--portal-url",
        default="http://127.0.0.1:4600",
    )
    parser.add_argument(
        "--channel",
        choices=["chromium", "chrome", "msedge"],
        default="chrome",
    )
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--start-mock-srm", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = LocalMockSrmServer(args.portal_url) if args.start_mock_srm else None
    try:
        if server is not None:
            server.start()
        raise SystemExit(asyncio.run(run_demo(args)))
    finally:
        if server is not None:
            server.stop()


if __name__ == "__main__":
    main()
