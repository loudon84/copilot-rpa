from __future__ import annotations

import importlib.util
import io
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.flows.package import FlowPackageValidator, PackageLimits
from nodeskclaw_rpa_engine.runtime.errors import (
    RpaBusinessError,
    RpaFatalError,
    RpaHumanRequiredError,
    RpaRetryableError,
)

FLOW_ROOT = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "mock-srm-flow"
    / "1.1.0"
)
ALL_FLOW_ROOTS = {
    "1.0.0": FLOW_ROOT.parent / "1.0.0",
    "1.1.0": FLOW_ROOT,
}
PO_NO = "POJS2606030010"
XLSX_CONTENT = b"PK\x03\x04supplier-portal-demo"


class FakeDownload:
    def __init__(
        self,
        path: Path,
        *,
        suggested_filename: str = "order-20260709122735.xlsx",
        content: bytes = XLSX_CONTENT,
    ) -> None:
        self.suggested_filename = suggested_filename
        self._path = path
        self._path.write_bytes(content)

    async def path(self) -> Path:
        return self._path

    async def save_as(self, path: str) -> None:
        shutil.copy2(self._path, path)


class FakeDownloadInfo:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def __aenter__(self) -> FakeDownloadInfo:
        self._page.actions.append("expect_download")
        if self._page.download_timeout:
            raise TimeoutError("download timeout")
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    @property
    async def value(self) -> FakeDownload:
        return self._page.download


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    async def wait_for(self, **_kwargs: Any) -> None:
        if self._selector == self._page.selectors["captcha_image"]:
            return
        if self._selector == self._page.selectors["order_page"]:
            return
        if self._selector == self._page.order_row_selector:
            if self._page.order_found:
                return
            raise TimeoutError("order row not found")
        if self._selector in {
            self._page.selectors["detail_page"],
            self._page.detail_po_selector,
        }:
            if self._page.detail_available:
                return
            raise TimeoutError("order detail unavailable")
        if self._selector == self._page.selectors["download_confirm"]:
            if self._page.download_modal_open:
                return
            raise TimeoutError("download confirmation unavailable")
        raise AssertionError(f"unexpected wait selector: {self._selector}")

    async def get_attribute(self, name: str) -> str | None:
        assert name == "src"
        assert self._selector == self._page.selectors["captcha_image"]
        return self._page.captcha_src

    async def is_checked(self) -> bool:
        assert self._selector == self._page.selectors["agreement"]
        return self._page.agreement_checked

    async def check(self) -> None:
        assert self._selector == self._page.selectors["agreement"]
        self._page.agreement_checked = True
        self._page.actions.append("check_agreement")

    async def is_visible(self) -> bool:
        if self._selector == self._page.selectors["login_success"]:
            return self._page.login_submitted and self._page.login_result == "success"
        if self._selector == self._page.selectors["login_error"]:
            return self._page.login_submitted and self._page.login_result == "error"
        return False

    async def click(self) -> None:
        assert self._selector == self._page.selectors["download_confirm"]
        self._page.actions.append("confirm_download")


class FakePage:
    def __init__(
        self,
        tmp_path: Path,
        selectors: dict[str, str],
        *,
        captcha_src: str = "/assets/captcha/code01.png",
        login_result: str = "success",
        order_found: bool = True,
        detail_available: bool = True,
        download_timeout: bool = False,
        download_filename: str = "order-20260709122735.xlsx",
        download_content: bytes = XLSX_CONTENT,
    ) -> None:
        self.selectors = selectors
        self.captcha_src = captcha_src
        self.login_result = login_result
        self.order_found = order_found
        self.detail_available = detail_available
        self.download_timeout = download_timeout
        self.download = FakeDownload(
            tmp_path / "portal-download",
            suggested_filename=download_filename,
            content=download_content,
        )
        self.url: str | None = None
        self.values: dict[str, str] = {}
        self.actions: list[str] = []
        self.login_submitted = False
        self.agreement_checked = False
        self.download_modal_open = False
        self.order_row_selector = selectors["order_row"].replace("{po_no}", PO_NO)
        self.detail_po_selector = selectors["detail_po_number"].replace(
            "{po_no}", PO_NO
        )

    async def goto(self, url: str, *, wait_until: str) -> None:
        assert wait_until == "domcontentloaded"
        self.url = url
        self.actions.append(f"goto:{url}")

    async def fill(self, selector: str, value: str) -> None:
        self.values[selector] = value

    async def click(self, selector: str) -> None:
        self.actions.append(f"click:{selector}")
        if selector == self.selectors["login_button"]:
            self.login_submitted = True
        elif selector == self.selectors["download_order"]:
            self.download_modal_open = True

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def expect_download(self, *, timeout: int) -> FakeDownloadInfo:
        assert timeout == 15000
        return FakeDownloadInfo(self)


class RecordingArtifacts:
    def __init__(self) -> None:
        self.screenshots: list[tuple[str, str | None]] = []
        self.downloads: list[tuple[str, str | None, int]] = []

    async def screenshot(
        self,
        name: str,
        *,
        full_page: bool = True,
        step_id: str | None = None,
    ) -> SimpleNamespace:
        assert full_page is True
        self.screenshots.append((name, step_id))
        return SimpleNamespace(size=1)

    async def save_download(
        self,
        download: FakeDownload,
        name: str | None = None,
        *,
        step_id: str | None = None,
    ) -> SimpleNamespace:
        source = await download.path()
        size = source.stat().st_size
        self.downloads.append((str(name), step_id, size))
        return SimpleNamespace(size=size)


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
        "phase5_supplier_portal_flow",
        FLOW_ROOT / "flow.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def flow_context(tmp_path: Path, **page_options: Any) -> SimpleNamespace:
    selectors = json.loads((FLOW_ROOT / "selectors.json").read_text("utf-8"))
    page = FakePage(tmp_path, selectors, **page_options)
    return SimpleNamespace(
        input={"po_no": PO_NO},
        credentials={"username": "supplier-demo", "password": "secret-value"},
        page=page,
        portal_url="https://supplier-portal.example/",
        selectors=selectors,
        artifacts=RecordingArtifacts(),
        events=RecordingEvents(),
        log=RecordingLog(),
    )


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0"])
def test_phase5_versioned_packages_pass_publish_policy(version: str) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "selectors.json", "flow.py"):
            archive.writestr(name, (ALL_FLOW_ROOTS[version] / name).read_bytes())
    settings = Settings(_env_file=None, app_env="test")
    package = FlowPackageValidator(
        PackageLimits(
            max_bytes=settings.flow_package_max_bytes,
            max_uncompressed_bytes=settings.flow_package_max_uncompressed_bytes,
            max_files=settings.flow_package_max_files,
            max_compression_ratio=settings.flow_package_max_compression_ratio,
        )
    ).validate(f"phase5-{version}.zip", output.getvalue())

    assert package.manifest.rpa_flow_id == "rpa_flow_mock_srm_fetch_po"
    assert package.manifest.version == version
    assert package.manifest.minimum_engine_version == "0.5.0"


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("code01", "mp3s"),
        ("code02", "0ada"),
        ("code03", "sez0"),
        ("code04", "ggmh"),
        ("code05", "rpyt"),
        ("code06", "y5na"),
        ("code07", "elhx"),
        ("code08", "el0m"),
        ("code09", "aqh9"),
        ("code10", "gqcy"),
    ],
)
def test_fixed_captcha_map(stem: str, expected: str) -> None:
    module = load_flow_module()

    assert module.resolve_captcha_code(
        f"/captcha/{stem.upper()}.PNG?cache=1"
    ) == expected


def test_dynamic_order_selectors_only_target_visible_elements() -> None:
    selectors = json.loads((FLOW_ROOT / "selectors.json").read_text("utf-8"))

    assert selectors["order_row"].endswith(":visible")
    assert selectors["order_detail"].endswith(":visible")
    assert selectors["detail_po_number"].endswith(":visible")


async def test_supplier_portal_success_uses_detail_confirmation_download(
    tmp_path: Path,
) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path)

    await module.run(ctx)

    assert ctx.page.url == "https://supplier-portal.example/#/supplier/orders"
    assert ctx.page.values[ctx.selectors["po_number"]] == PO_NO
    assert ctx.page.actions.index("expect_download") < ctx.page.actions.index(
        "confirm_download"
    )
    assert ctx.artifacts.downloads == [
        ("order-20260709122735.xlsx", "file.download", len(XLSX_CONTENT))
    ]
    assert ctx.events.items[-1]["type"] == "SUPPLIER_PORTAL_PO_COMPLETED"


async def test_unknown_captcha_requires_human(tmp_path: Path) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path, captcha_src="/captcha/unknown.png")

    with pytest.raises(RpaHumanRequiredError) as captured:
        await module.run(ctx)

    assert captured.value.code == "HUMAN_VERIFICATION_REQUIRED"
    assert ctx.artifacts.screenshots == [
        ("supplier-portal-captcha-unknown", "srm.login")
    ]
    assert ctx.page.values[ctx.selectors["username"]] == ""
    assert ctx.page.values[ctx.selectors["password"]] == ""
    assert ctx.events.items[-1]["type"] == "STEP_WAITING_HUMAN"


async def test_login_error_is_business_failure(tmp_path: Path) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path, login_result="error")

    with pytest.raises(RpaBusinessError) as captured:
        await module.run(ctx)

    assert captured.value.code == "SRM_LOGIN_FAILED"
    assert ctx.page.values[ctx.selectors["username"]] == ""
    assert ctx.page.values[ctx.selectors["password"]] == ""
    assert ctx.events.items[-1]["type"] == "STEP_FAILED"


async def test_login_timeout_is_retryable_and_redacted(tmp_path: Path) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path, login_result="timeout")

    with pytest.raises(RpaRetryableError) as captured:
        await module.run(ctx)

    assert captured.value.code == "SRM_LOGIN_TIMEOUT"
    assert ctx.page.values[ctx.selectors["username"]] == ""
    assert ctx.page.values[ctx.selectors["password"]] == ""


async def test_missing_order_is_business_failure(tmp_path: Path) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path, order_found=False)

    with pytest.raises(RpaBusinessError) as captured:
        await module.run(ctx)

    assert captured.value.code == "BUSINESS_NOT_FOUND"
    assert ctx.events.items[-1]["type"] == "STEP_FAILED"


async def test_unverified_order_detail_is_retryable(tmp_path: Path) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path, detail_available=False)

    with pytest.raises(RpaRetryableError) as captured:
        await module.run(ctx)

    assert captured.value.code == "ORDER_DETAIL_UNAVAILABLE"


async def test_download_timeout_is_retryable(tmp_path: Path) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path, download_timeout=True)

    with pytest.raises(RpaRetryableError) as captured:
        await module.run(ctx)

    assert captured.value.code == "ORDER_DOWNLOAD_FAILED"
    assert ctx.artifacts.downloads == []


async def test_download_extension_must_be_xlsx(tmp_path: Path) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path, download_filename="unexpected.pdf")

    with pytest.raises(RpaFatalError) as captured:
        await module.run(ctx)

    assert captured.value.code == "ORDER_DOWNLOAD_FILE_INVALID"
    assert ctx.artifacts.downloads == []


async def test_empty_download_is_rejected_before_artifact_upload(
    tmp_path: Path,
) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path, download_content=b"")

    with pytest.raises(RpaRetryableError) as captured:
        await module.run(ctx)

    assert captured.value.code == "ORDER_DOWNLOAD_FILE_EMPTY"
    assert ctx.artifacts.downloads == []


async def test_non_xlsx_signature_is_rejected_before_artifact_upload(
    tmp_path: Path,
) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path, download_content=b"not-an-xlsx")

    with pytest.raises(RpaFatalError) as captured:
        await module.run(ctx)

    assert captured.value.code == "ORDER_DOWNLOAD_FILE_INVALID"
    assert ctx.artifacts.downloads == []


async def test_unsafe_po_number_is_rejected(tmp_path: Path) -> None:
    module = load_flow_module()
    ctx = flow_context(tmp_path)
    ctx.input["po_no"] = "PO'] button"

    with pytest.raises(RpaBusinessError) as captured:
        await module.run(ctx)

    assert captured.value.code == "FLOW_INPUT_INVALID"
