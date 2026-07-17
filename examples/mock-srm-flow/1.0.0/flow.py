from nodeskclaw_rpa_engine.runtime import (
    RpaBusinessError,
    RpaFatalError,
    RpaHumanRequiredError,
)


class MockSrmAdapter:
    def __init__(self, ctx):
        self.ctx = ctx
        self.page = ctx.page
        self.selectors = ctx.selectors

    def selector(self, name):
        value = self.selectors.get(name)
        if not isinstance(value, str) or not value:
            raise RpaFatalError(
                "FLOW_SELECTOR_MISSING",
                f"Required Mock SRM selector is missing: {name}",
            )
        return value

    async def login(self):
        step_id = "srm.login"
        username = str(self.ctx.credentials.get("username", "")).strip()
        password = str(self.ctx.credentials.get("password", ""))
        if not username or not password:
            raise RpaFatalError(
                "SRM_CREDENTIALS_MISSING",
                "Mock SRM credentials are unavailable",
            )

        await self.ctx.events.emit(
            "STEP_STARTED",
            message="Logging in to Mock SRM",
            payload={"stepId": step_id, "stepType": "srm.login"},
        )
        await self.page.goto(self.ctx.portal_url, wait_until="domcontentloaded")
        await self.page.fill(self.selector("username"), username)
        await self.page.fill(self.selector("password"), password)
        await self.page.click(self.selector("login_button"))
        await self.page.locator(self.selector("workspace")).wait_for(
            state="visible",
            timeout=5000,
        )
        if await self.page.locator(self.selector("login_error")).is_visible():
            raise RpaBusinessError(
                "SRM_LOGIN_FAILED",
                "Mock SRM login failed",
            )
        await self.ctx.events.emit(
            "STEP_SUCCEEDED",
            message="Mock SRM login completed",
            payload={"stepId": step_id},
        )

    async def search_po(self, po_no):
        step_id = "srm.search_po"
        await self.ctx.events.emit(
            "STEP_STARTED",
            message="Searching for purchase order",
            payload={"stepId": step_id, "stepType": "srm.search_po"},
        )
        await self.page.fill(self.selector("po_number"), po_no)
        await self.page.click(self.selector("search_button"))

        if await self.page.locator(self.selector("human_check")).is_visible():
            await self.ctx.artifacts.screenshot(
                "mock-srm-human-required",
                step_id=step_id,
            )
            await self.ctx.events.emit(
                "STEP_WAITING_HUMAN",
                level="WARNING",
                message="Mock SRM requires CAPTCHA or MFA verification",
                payload={"stepId": step_id, "reason": "CAPTCHA_OR_MFA"},
            )
            raise RpaHumanRequiredError(
                "HUMAN_VERIFICATION_REQUIRED",
                "Mock SRM requires CAPTCHA or MFA verification",
            )

        if await self.page.locator(self.selector("not_found")).is_visible():
            await self.ctx.artifacts.screenshot(
                "mock-srm-po-not-found",
                step_id=step_id,
            )
            await self.ctx.events.emit(
                "STEP_FAILED",
                level="ERROR",
                message="Purchase order was not found",
                payload={"stepId": step_id, "errorCode": "BUSINESS_NOT_FOUND"},
            )
            raise RpaBusinessError(
                "BUSINESS_NOT_FOUND",
                "Purchase order was not found",
            )

        await self.page.locator(self.selector("po_result")).wait_for(
            state="visible",
            timeout=5000,
        )
        await self.ctx.artifacts.screenshot(
            "mock-srm-po-result",
            step_id=step_id,
        )
        await self.ctx.events.emit(
            "STEP_SUCCEEDED",
            message="Purchase order was found",
            payload={"stepId": step_id, "poNo": po_no},
        )

    async def download_contract(self, po_no):
        step_id = "file.download"
        await self.ctx.events.emit(
            "STEP_STARTED",
            message="Downloading purchase order contract",
            payload={"stepId": step_id, "stepType": "file.download"},
        )
        async with self.page.expect_download(timeout=10000) as download_info:
            await self.page.click(self.selector("download_contract"))
        download = await download_info.value
        await self.ctx.artifacts.save_download(
            download,
            f"{po_no}-contract.pdf",
            step_id=step_id,
        )
        await self.ctx.events.emit(
            "STEP_SUCCEEDED",
            message="Purchase order contract downloaded",
            payload={"stepId": step_id, "poNo": po_no},
        )


async def run(ctx):
    if not ctx.portal_url:
        raise RpaFatalError(
            "PORTAL_URL_MISSING",
            "Mock SRM portal URL is unavailable",
        )
    po_no = str(ctx.input.get("po_no", "")).strip().upper()
    if not po_no:
        raise RpaBusinessError(
            "FLOW_INPUT_INVALID",
            "Purchase order number is required",
        )

    await ctx.log.info("Starting Mock SRM purchase order flow", {"poNo": po_no})
    adapter = MockSrmAdapter(ctx)
    await adapter.login()
    await adapter.search_po(po_no)
    await adapter.download_contract(po_no)
    await ctx.events.emit(
        "MOCK_SRM_COMPLETED",
        message="Mock SRM purchase order flow completed",
        payload={"poNo": po_no},
    )
