from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.flows.package import FlowPackageValidator, PackageLimits
from nodeskclaw_rpa_engine.runtime.errors import (
    RpaBusinessError,
    RpaHumanRequiredError,
)

FLOW_ROOT = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "mock-srm-flow"
    / "1.0.0"
)
SUCCESS_PO = "PO-20260708-001"
NOT_FOUND_PO = "PO-NOT-FOUND"
MANUAL_PO = "PO-MANUAL-001"


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    async def wait_for(self, **kwargs: Any) -> None:
        assert kwargs == {"state": "visible", "timeout": 5000}
        if not await self.is_visible():
            raise AssertionError(f"selector is not visible: {self._selector}")

    async def is_visible(self) -> bool:
        if self._selector == "#workspace":
            return self._page.logged_in
        if self._selector == "#login-error":
            return False
        if self._selector == "#human-check":
            return self._page.po_no == MANUAL_PO
        if self._selector == "#not-found":
            return self._page.po_no == NOT_FOUND_PO
        if self._selector == "#po-result":
            return self._page.po_no == SUCCESS_PO
        return False


class FakeDownload:
    suggested_filename = f"{SUCCESS_PO}-contract.pdf"


class FakeDownloadInfo:
    async def __aenter__(self) -> FakeDownloadInfo:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    @property
    async def value(self) -> FakeDownload:
        return FakeDownload()


class FakePage:
    def __init__(self) -> None:
        self.logged_in = False
        self.po_no: str | None = None
        self.values: dict[str, str] = {}
        self.url: str | None = None

    async def goto(self, url: str, *, wait_until: str) -> None:
        self.url = url
        assert wait_until == "domcontentloaded"

    async def fill(self, selector: str, value: str) -> None:
        self.values[selector] = value
        if selector == "#po-number":
            self.po_no = value

    async def click(self, selector: str) -> None:
        if selector == "#login-button":
            self.logged_in = True

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def expect_download(self, *, timeout: int) -> FakeDownloadInfo:
        assert timeout == 10000
        return FakeDownloadInfo()


class RecordingArtifacts:
    def __init__(self) -> None:
        self.screenshots: list[tuple[str, str | None]] = []
        self.downloads: list[tuple[str, str | None]] = []

    async def screenshot(
        self,
        name: str,
        *,
        full_page: bool = True,
        step_id: str | None = None,
    ) -> None:
        assert full_page is True
        self.screenshots.append((name, step_id))

    async def save_download(
        self,
        _download: Any,
        name: str | None = None,
        *,
        step_id: str | None = None,
    ) -> None:
        self.downloads.append((str(name), step_id))


class RecordingEvents:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def emit(self, event_type: str, **kwargs: Any) -> None:
        self.items.append({"type": event_type, **kwargs})


class RecordingLog:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any] | None]] = []

    async def info(
        self,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.items.append((message, payload))


def load_flow_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "phase5_mock_srm_flow",
        FLOW_ROOT / "flow.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def context(po_no: str) -> SimpleNamespace:
    selectors = {
        key: value
        for key, value in json.loads(
            (FLOW_ROOT / "selectors.json").read_text(encoding="utf-8")
        ).items()
    }
    return SimpleNamespace(
        input={"po_no": po_no},
        credentials={"username": "mock-user", "password": uuid4().hex},
        page=FakePage(),
        portal_url="http://127.0.0.1:4600",
        selectors=selectors,
        artifacts=RecordingArtifacts(),
        events=RecordingEvents(),
        log=RecordingLog(),
    )


def test_phase5_flow_package_passes_publish_policy() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "selectors.json", "flow.py"):
            archive.writestr(name, (FLOW_ROOT / name).read_bytes())
    settings = Settings(_env_file=None, app_env="test")
    package = FlowPackageValidator(
        PackageLimits(
            max_bytes=settings.flow_package_max_bytes,
            max_uncompressed_bytes=settings.flow_package_max_uncompressed_bytes,
            max_files=settings.flow_package_max_files,
            max_compression_ratio=settings.flow_package_max_compression_ratio,
        )
    ).validate("mock-srm.zip", output.getvalue())

    assert package.manifest.rpa_flow_id == "rpa_flow_mock_srm_fetch_po"
    assert package.manifest.version == "1.0.0"
    assert package.manifest.minimum_engine_version == "0.5.0"


async def test_phase5_success_flow_records_result_and_download() -> None:
    module = load_flow_module()
    ctx = context(SUCCESS_PO)

    await module.run(ctx)

    assert ctx.page.url == "http://127.0.0.1:4600"
    assert ctx.artifacts.screenshots == [
        ("mock-srm-po-result", "srm.search_po")
    ]
    assert ctx.artifacts.downloads == [
        (f"{SUCCESS_PO}-contract.pdf", "file.download")
    ]
    assert ctx.events.items[-1]["type"] == "MOCK_SRM_COMPLETED"


async def test_phase5_not_found_flow_is_business_failure() -> None:
    module = load_flow_module()
    ctx = context(NOT_FOUND_PO)

    with pytest.raises(RpaBusinessError) as captured:
        await module.run(ctx)

    assert captured.value.code == "BUSINESS_NOT_FOUND"
    assert ctx.artifacts.screenshots == [
        ("mock-srm-po-not-found", "srm.search_po")
    ]
    assert ctx.artifacts.downloads == []
    assert ctx.events.items[-1]["type"] == "STEP_FAILED"


async def test_phase5_manual_flow_requires_human() -> None:
    module = load_flow_module()
    ctx = context(MANUAL_PO)

    with pytest.raises(RpaHumanRequiredError) as captured:
        await module.run(ctx)

    assert captured.value.code == "HUMAN_VERIFICATION_REQUIRED"
    assert ctx.artifacts.screenshots == [
        ("mock-srm-human-required", "srm.search_po")
    ]
    assert ctx.artifacts.downloads == []
    assert ctx.events.items[-1]["type"] == "STEP_WAITING_HUMAN"
