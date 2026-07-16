from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from nodeskclaw_rpa_engine.runtime.errors import RpaFatalError
from nodeskclaw_rpa_engine.workers.schemas import BrowserSessionConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BrowserSession:
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    trace_started: bool
    _trace_stopped: bool = False

    async def stop_trace(self, path: Path) -> Path | None:
        if not self.trace_started or self._trace_stopped:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.context.tracing.stop(path=str(path))
        self._trace_stopped = True
        return path

    async def close(self) -> None:
        if self.trace_started and not self._trace_stopped:
            try:
                await self.context.tracing.stop()
            except Exception:
                logger.warning("Browser trace stop failed during cleanup")
            self._trace_stopped = True
        for operation in (self.context.close, self.browser.close, self.playwright.stop):
            try:
                await operation()
            except Exception:
                logger.warning("Browser resource cleanup failed")


class ManagedBrowserSessionManager:
    def __init__(
        self,
        playwright_factory: Callable[[], Any] = async_playwright,
    ) -> None:
        self._playwright_factory = playwright_factory

    async def start(
        self,
        config: BrowserSessionConfig,
        *,
        run_directory: Path,
        trace_enabled: bool,
    ) -> BrowserSession:
        self._validate_config(config)
        await asyncio.to_thread(
            run_directory.mkdir,
            parents=True,
            exist_ok=True,
        )
        playwright: Playwright | None = None
        browser: Browser | None = None
        context: BrowserContext | None = None
        try:
            playwright = await self._playwright_factory().start()
            channel = None if config.channel == "chromium" else config.channel
            browser = await playwright.chromium.launch(
                headless=config.headless,
                channel=channel,
                downloads_path=str(run_directory / "downloads"),
            )
            context = await browser.new_context(accept_downloads=True)
            if trace_enabled:
                await context.tracing.start(
                    screenshots=True,
                    snapshots=True,
                    sources=True,
                )
            page = await context.new_page()
            return BrowserSession(
                playwright=playwright,
                browser=browser,
                context=context,
                page=page,
                trace_started=trace_enabled,
            )
        except Exception as exc:
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()
            if playwright is not None:
                await playwright.stop()
            raise RpaFatalError(
                "BROWSER_LAUNCH_FAILED",
                "Managed browser session could not be started",
            ) from exc

    @staticmethod
    def _validate_config(config: BrowserSessionConfig) -> None:
        if config.mode != "MANAGED":
            raise RpaFatalError(
                "BROWSER_SESSION_MODE_UNSUPPORTED",
                "Only MANAGED browser sessions are supported",
            )
        if config.profile_ref is not None or config.cdp_endpoint_ref is not None:
            raise RpaFatalError(
                "BROWSER_SESSION_REFERENCE_FORBIDDEN",
                "MANAGED sessions cannot use Profile or CDP references",
            )
        if config.channel not in {"chromium", "chrome", "msedge"}:
            raise RpaFatalError(
                "BROWSER_CHANNEL_UNSUPPORTED",
                "Browser channel is not supported",
            )
        if config.close_policy not in {"ALWAYS", "CLOSE_ON_FINISH"}:
            raise RpaFatalError(
                "BROWSER_CLOSE_POLICY_UNSUPPORTED",
                "MANAGED sessions must close when the run finishes",
            )
