from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from nodeskclaw_rpa_engine.core.config import Settings
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

FLOW_ID = "rpa_flow_mock_srm_fetch_po"
FLOW_VERSION = "1.1.0"
FLOW_FILES = ("manifest.json", "selectors.json", "flow.py")
DEFAULT_PO_NO = "POJS2606030010"
CREDENTIAL_REF = "credential-ref-supplier-portal-demo"
TENANT_ID = "tenant-supplier-portal-demo"
PORTAL_ACCOUNT_ID = "portal-account-supplier-portal-demo"


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


class MemoryPackageSource:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def fetch(self, _flow: ResolvedFlowVersion) -> bytes:
        return self._content


class EnvironmentCredentialResolver:
    def __init__(self, *, username: str, password: str) -> None:
        self._username = username
        self._password = password

    async def resolve(
        self,
        credential_ref: str | None,
        *,
        tenant_id: str | None,
        portal_account_id: str | None,
    ) -> dict[str, str]:
        if (
            credential_ref != CREDENTIAL_REF
            or tenant_id != TENANT_ID
            or portal_account_id != PORTAL_ACCOUNT_ID
        ):
            raise ValueError("Unexpected supplier portal credential scope")
        return {"username": self._username, "password": self._password}


class LocalArtifactSink:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
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
        destination = self.root / run_id / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, path, destination)
        storage_key = destination.relative_to(self.root).as_posix()
        self.items.setdefault(run_id, []).append(
            {
                "taskId": task_id,
                "type": artifact_type.value,
                "name": name,
                "size": size,
                "mimeType": mime_type,
                "storageKey": storage_key,
                "path": str(destination),
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


def build_package(root: Path) -> bytes:
    source = root / "examples" / "mock-srm-flow" / FLOW_VERSION
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in FLOW_FILES:
            source_file = source / name
            if not source_file.is_file():
                raise FileNotFoundError(
                    f"Flow {FLOW_VERSION} source file is missing: {source_file}"
                )
            archive.write(source_file, name)
    return output.getvalue()


def command(
    *,
    po_no: str,
    portal_url: str,
    channel: str,
    headless: bool,
    checksum: str,
) -> RunCommand:
    run_id = f"supplier-portal-{uuid4().hex[:8]}"
    lease = LeaseRunCommand.model_validate(
        {
            "taskId": f"task-{run_id}",
            "runId": run_id,
            "leaseId": f"lease-{run_id}",
            "workflowBindingId": "binding-supplier-portal-demo",
            "portalAccountId": PORTAL_ACCOUNT_ID,
            "rpaFlowId": FLOW_ID,
            "input": {"po_no": po_no},
            "tenantId": TENANT_ID,
            "workflowTemplateId": "template-srm-fetch-po",
            "workflowCode": "srm_fetch_po",
            "rpaEngineType": "PLAYWRIGHT_CDP",
            "rpaFlowVersion": FLOW_VERSION,
            "credentialRef": CREDENTIAL_REF,
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
            rpa_flow_id=FLOW_ID,
            version=FLOW_VERSION,
            engine_type="PLAYWRIGHT_CDP",
            package_uri="memory://supplier-portal/flow.zip",
            package_checksum=checksum,
            package_object_key="supplier-portal/flow.zip",
            supported_workflow_codes=["srm_fetch_po"],
            capabilities=[
                "PLAYWRIGHT_CDP",
                "BROWSER_SESSION_MANAGED",
                "SCREENSHOT",
                "DOWNLOAD",
            ],
        ),
    )


def download_is_valid(artifact: dict[str, Any]) -> bool:
    if artifact.get("type") != ArtifactType.DOWNLOAD.value:
        return False
    name = artifact.get("name")
    path_value = artifact.get("path")
    size = artifact.get("size")
    if (
        not isinstance(name, str)
        or not name.lower().endswith(".xlsx")
        or not isinstance(path_value, str)
        or not isinstance(size, int)
        or size <= 0
    ):
        return False
    path = Path(path_value)
    if not path.is_file():
        return False
    with path.open("rb") as source:
        return source.read(4) == b"PK\x03\x04"


async def run_demo(args: argparse.Namespace, *, username: str, password: str) -> int:
    root = repository_root()
    package = build_package(root)
    checksum = hashlib.sha256(package).hexdigest()
    runtime_root = root / "runtime-cache" / "supplier-portal-demo"
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_enabled=False,
        minio_enabled=False,
        worker_enabled=False,
        worker_lease_enabled=False,
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
        credential_resolver=EnvironmentCredentialResolver(
            username=username,
            password=password,
        ),
    )
    run_command = command(
        po_no=args.po_no,
        portal_url=args.portal_url,
        channel=args.channel,
        headless=args.headless,
        checksum=checksum,
    )
    result = await runtime.handle(run_command)
    run_id = run_command.lease.run_id
    artifacts = artifact_sink.items.get(run_id, [])
    download_verified = any(download_is_valid(item) for item in artifacts)
    summary = {
        "flowVersion": FLOW_VERSION,
        "poNo": args.po_no,
        "status": result.status.value,
        "errorCode": result.error_code,
        "downloadVerified": download_verified,
        "artifacts": artifacts,
        "events": [item["type"] for item in event_sinks[run_id].items],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"artifactDirectory={artifact_sink.root}")
    return int(
        result.status is not AttemptStatus.SUCCESS or not download_verified
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Flow 1.1.0 against a configured supplier portal"
    )
    parser.add_argument(
        "--portal-url",
        default=os.getenv("SUPPLIER_PORTAL_URL"),
        help="Portal URL (or set SUPPLIER_PORTAL_URL)",
    )
    parser.add_argument("--po-no", default=DEFAULT_PO_NO)
    parser.add_argument(
        "--channel",
        choices=["chromium", "chrome", "msedge"],
        default="chrome",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    args = parser.parse_args()
    if not args.portal_url or not args.portal_url.strip():
        parser.error("--portal-url or SUPPLIER_PORTAL_URL is required")
    if not args.po_no.strip():
        parser.error("--po-no must not be empty")
    args.portal_url = args.portal_url.strip()
    args.po_no = args.po_no.strip()
    return args


def main() -> None:
    args = parse_args()
    username = os.getenv("SUPPLIER_PORTAL_USERNAME")
    password = os.getenv("SUPPLIER_PORTAL_PASSWORD")
    if not username or not password:
        raise SystemExit(
            "SUPPLIER_PORTAL_USERNAME and SUPPLIER_PORTAL_PASSWORD are required"
        )
    try:
        exit_code = asyncio.run(
            run_demo(args, username=username, password=password)
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "errorCode": "SUPPLIER_PORTAL_SMOKE_SETUP_FAILED",
                    "errorType": type(exc).__name__,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        exit_code = 2
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
