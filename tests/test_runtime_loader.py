from __future__ import annotations

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
from uuid import uuid4

import pytest

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.runtime.errors import RpaFatalError
from nodeskclaw_rpa_engine.runtime.loader import FlowLoader
from nodeskclaw_rpa_engine.workers.schemas import ResolvedFlowVersion


def flow_package(*, minimum_engine_version: str | None = None) -> bytes:
    output = io.BytesIO()
    manifest = {
        "rpaFlowId": "rpa_flow_runtime_test",
        "name": "Runtime Test",
        "version": "1.0.0",
        "engineType": "PLAYWRIGHT_CDP",
        "entrypoint": "flow.py:run",
        "supportedWorkflowCodes": ["runtime_test"],
        "supportedPortalTypes": ["MOCK"],
        "inputSchema": [
            {"name": "record_id", "type": "string", "required": True}
        ],
        "capabilities": [],
    }
    if minimum_engine_version is not None:
        manifest["minimumEngineVersion"] = minimum_engine_version
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest),
        )
        archive.writestr(
            "flow.py",
            "async def run(ctx):\n    return ctx.input['record_id']\n",
        )
        archive.writestr(
            "selectors.json",
            json.dumps({"search": "#search"}),
        )
    return output.getvalue()


class MemorySource:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0

    async def fetch(self, _flow: ResolvedFlowVersion) -> bytes:
        self.calls += 1
        return self.content


def resolved(content: bytes) -> ResolvedFlowVersion:
    return ResolvedFlowVersion(
        flow_version_id=uuid4(),
        rpa_flow_id="rpa_flow_runtime_test",
        version="1.0.0",
        engine_type="PLAYWRIGHT_CDP",
        package_uri="http://engine/package",
        package_checksum=hashlib.sha256(content).hexdigest(),
        package_object_key="flows/runtime/1.0.0/package.zip",
        supported_workflow_codes=["runtime_test"],
        capabilities=[],
    )


async def test_loader_fetches_validates_caches_and_executes(tmp_path) -> None:
    content = flow_package()
    source = MemorySource(content)
    settings = Settings(
        _env_file=None,
        runtime_cache_dir=tmp_path / "flows",
        runtime_work_dir=tmp_path / "runs",
    )
    loader = FlowLoader(settings, source)
    flow = resolved(content)

    loaded = await loader.load(flow)
    result = await loaded.run(SimpleNamespace(input={"record_id": "record-1"}))
    loaded_again = await loader.load(flow)

    assert result == "record-1"
    assert loaded.selectors == {"search": "#search"}
    assert loaded.root == loaded_again.root
    assert source.calls == 1
    assert (loaded.root / ".ready").read_text(encoding="utf-8") == (
        flow.package_checksum
    )


async def test_loader_rejects_registry_checksum_mismatch(tmp_path) -> None:
    content = flow_package()
    source = MemorySource(content + b"tampered")
    loader = FlowLoader(
        Settings(
            _env_file=None,
            runtime_cache_dir=tmp_path / "flows",
            runtime_work_dir=tmp_path / "runs",
        ),
        source,
    )

    with pytest.raises(RpaFatalError) as captured:
        await loader.load(resolved(content))

    assert captured.value.code == "FLOW_PACKAGE_CHECKSUM_MISMATCH"


async def test_loader_repairs_a_corrupt_local_cache(tmp_path) -> None:
    content = flow_package()
    source = MemorySource(content)
    loader = FlowLoader(
        Settings(
            _env_file=None,
            runtime_cache_dir=tmp_path / "flows",
            runtime_work_dir=tmp_path / "runs",
        ),
        source,
    )
    flow = resolved(content)
    loaded = await loader.load(flow)
    (loaded.root / "package.zip").write_bytes(b"corrupt")

    repaired = await loader.load(flow)

    assert source.calls == 2
    assert repaired.manifest.version == "1.0.0"


async def test_loader_rejects_flow_requiring_newer_engine(tmp_path) -> None:
    content = flow_package(minimum_engine_version="99.0.0")
    loader = FlowLoader(
        Settings(
            _env_file=None,
            runtime_cache_dir=tmp_path / "flows",
            runtime_work_dir=tmp_path / "runs",
        ),
        MemorySource(content),
    )

    with pytest.raises(RpaFatalError) as captured:
        await loader.load(resolved(content))

    assert captured.value.code == "ENGINE_VERSION_INCOMPATIBLE"


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (PermissionError("cache denied"), "FLOW_CACHE_ACCESS_DENIED"),
        (OSError("cache write failed"), "FLOW_CACHE_WRITE_FAILED"),
    ],
)
async def test_loader_maps_cache_os_errors(
    tmp_path,
    monkeypatch,
    error: OSError,
    expected_code: str,
) -> None:
    content = flow_package()
    loader = FlowLoader(
        Settings(
            _env_file=None,
            runtime_cache_dir=tmp_path / "flows",
            runtime_work_dir=tmp_path / "runs",
        ),
        MemorySource(content),
    )

    def fail_cache_write(*_args: object) -> None:
        raise error

    monkeypatch.setattr(loader, "_replace_cache", fail_cache_write)

    with pytest.raises(RpaFatalError) as captured:
        await loader.load(resolved(content))

    assert captured.value.code == expected_code
    assert captured.value.__cause__ is error
