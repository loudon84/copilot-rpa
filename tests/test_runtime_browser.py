from __future__ import annotations

from pathlib import Path

import pytest

from nodeskclaw_rpa_engine.runtime.browser import ManagedBrowserSessionManager
from nodeskclaw_rpa_engine.runtime.errors import RpaFatalError
from nodeskclaw_rpa_engine.workers.schemas import BrowserSessionConfig


class FakeTracing:
    def __init__(self) -> None:
        self.started = False
        self.stopped: str | None = None

    async def start(self, **_kwargs) -> None:
        self.started = True

    async def stop(self, *, path: str | None = None) -> None:
        self.stopped = path or "discarded"


class FakeContext:
    def __init__(self) -> None:
        self.tracing = FakeTracing()
        self.closed = False
        self.page = object()

    async def new_page(self):
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakeContext()
        self.closed = False
        self.context_options: dict[str, object] = {}

    async def new_context(self, **kwargs):
        self.context_options = kwargs
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self) -> None:
        self.browser = FakeBrowser()
        self.launch_options: dict[str, object] = {}

    async def launch(self, **kwargs):
        self.launch_options = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakeController:
    def __init__(self) -> None:
        self.playwright = FakePlaywright()

    async def start(self):
        return self.playwright


def config(**updates: object) -> BrowserSessionConfig:
    values = {
        "mode": "MANAGED",
        "headless": True,
        "channel": "chromium",
        "profile_ref": None,
        "cdp_endpoint_ref": None,
        "close_policy": "CLOSE_ON_FINISH",
    }
    values.update(updates)
    return BrowserSessionConfig(**values)


async def test_managed_browser_owns_context_page_trace_and_cleanup(tmp_path) -> None:
    controller = FakeController()
    manager = ManagedBrowserSessionManager(lambda: controller)

    session = await manager.start(
        config(),
        run_directory=tmp_path,
        trace_enabled=True,
    )
    trace_path = tmp_path / "trace.zip"
    await session.stop_trace(trace_path)
    await session.close()

    playwright = controller.playwright
    assert playwright.chromium.launch_options == {
        "headless": True,
        "channel": None,
        "downloads_path": str(tmp_path / "downloads"),
    }
    assert playwright.chromium.browser.context_options == {
        "accept_downloads": True
    }
    assert playwright.chromium.browser.context.tracing.started is True
    assert playwright.chromium.browser.context.tracing.stopped == str(trace_path)
    assert playwright.chromium.browser.context.closed is True
    assert playwright.chromium.browser.closed is True
    assert playwright.stopped is True


@pytest.mark.parametrize("channel", ["chrome", "msedge"])
async def test_managed_browser_preserves_requested_branded_channel(
    tmp_path: Path,
    channel: str,
) -> None:
    controller = FakeController()
    manager = ManagedBrowserSessionManager(lambda: controller)

    session = await manager.start(
        config(channel=channel),
        run_directory=tmp_path,
        trace_enabled=False,
    )
    await session.close()

    assert controller.playwright.chromium.launch_options["channel"] == channel


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"mode": "CDP_ATTACH"}, "BROWSER_SESSION_MODE_UNSUPPORTED"),
        ({"profile_ref": "profile-1"}, "BROWSER_SESSION_REFERENCE_FORBIDDEN"),
        ({"cdp_endpoint_ref": "cdp-1"}, "BROWSER_SESSION_REFERENCE_FORBIDDEN"),
        ({"channel": "firefox"}, "BROWSER_CHANNEL_UNSUPPORTED"),
        ({"close_policy": "KEEP_OPEN"}, "BROWSER_CLOSE_POLICY_UNSUPPORTED"),
    ],
)
async def test_managed_browser_rejects_unsupported_configuration(
    tmp_path: Path,
    updates: dict[str, object],
    code: str,
) -> None:
    manager = ManagedBrowserSessionManager(lambda: FakeController())
    with pytest.raises(RpaFatalError) as captured:
        await manager.start(
            config(**updates),
            run_directory=tmp_path,
            trace_enabled=False,
        )
    assert captured.value.code == code
