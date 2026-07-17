import asyncio
import re
from pathlib import Path

from nodeskclaw_rpa_engine.runtime import (
    RpaBusinessError,
    RpaFatalError,
    RpaHumanRequiredError,
    RpaRetryableError,
)

CAPTCHA_CODES = {
    "code01": "mp3s",
    "code02": "0ada",
    "code03": "sez0",
    "code04": "ggmh",
    "code05": "rpyt",
    "code06": "y5na",
    "code07": "elhx",
    "code08": "el0m",
    "code09": "aqh9",
    "code10": "gqcy",
}
PO_NUMBER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,63}$")


def resolve_captcha_code(image_src):
    if not isinstance(image_src, str) or not image_src.strip():
        return None
    clean_src = image_src.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    filename = clean_src.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    stem = filename.rsplit(".", maxsplit=1)[0].casefold()
    return CAPTCHA_CODES.get(stem)


class SupplierPortalAdapter:
    def __init__(self, ctx):
        self.ctx = ctx
        self.page = ctx.page
        self.selectors = ctx.selectors

    def selector(self, name, *, po_no=None):
        value = self.selectors.get(name)
        if not isinstance(value, str) or not value:
            raise RpaFatalError(
                "FLOW_SELECTOR_MISSING",
                f"Required supplier portal selector is missing: {name}",
            )
        if "{po_no}" in value:
            if not po_no:
                raise RpaFatalError(
                    "FLOW_SELECTOR_INVALID",
                    "A purchase order number is required for a dynamic selector",
                )
            return value.replace("{po_no}", po_no)
        return value

    async def login(self):
        step_id = "srm.login"
        username = str(self.ctx.credentials.get("username", "")).strip()
        password = str(self.ctx.credentials.get("password", ""))
        if not username or not password:
            raise RpaFatalError(
                "SRM_CREDENTIALS_MISSING",
                "Supplier portal credentials are unavailable",
            )

        await self.ctx.events.emit(
            "STEP_STARTED",
            message="Logging in to supplier portal",
            payload={"stepId": step_id, "stepType": "srm.login"},
        )
        await self.page.goto(self.ctx.portal_url, wait_until="domcontentloaded")
        captcha_image = self.page.locator(self.selector("captcha_image"))
        await captcha_image.wait_for(state="visible", timeout=10000)
        captcha_src = await captcha_image.get_attribute("src")
        captcha_code = resolve_captcha_code(captcha_src)
        if captcha_code is None:
            await self._redact_login_fields()
            await self.ctx.artifacts.screenshot(
                "supplier-portal-captcha-unknown",
                step_id=step_id,
            )
            await self.ctx.events.emit(
                "STEP_WAITING_HUMAN",
                level="WARNING",
                message="Supplier portal CAPTCHA requires human verification",
                payload={"stepId": step_id, "reason": "CAPTCHA_UNKNOWN"},
            )
            raise RpaHumanRequiredError(
                "HUMAN_VERIFICATION_REQUIRED",
                "Supplier portal CAPTCHA requires human verification",
            )

        try:
            await self.page.fill(self.selector("username"), username)
            await self.page.fill(self.selector("password"), password)
            await self.page.fill(self.selector("captcha"), captcha_code)
            agreement = self.page.locator(self.selector("agreement"))
            if not await agreement.is_checked():
                await agreement.check()
            await self.page.click(self.selector("login_button"))
            await self._wait_for_login_result(step_id)
        except (RpaBusinessError, RpaRetryableError):
            raise
        except Exception as exc:
            await self._redact_login_fields()
            raise RpaRetryableError(
                "SRM_LOGIN_FAILED",
                "Supplier portal login failed",
            ) from exc
        await self.ctx.artifacts.screenshot(
            "supplier-portal-login-succeeded",
            step_id=step_id,
        )
        await self.ctx.events.emit(
            "STEP_SUCCEEDED",
            message="Supplier portal login completed",
            payload={"stepId": step_id},
        )

    async def _wait_for_login_result(self, step_id):
        success = self.page.locator(self.selector("login_success"))
        error = self.page.locator(self.selector("login_error"))
        for _ in range(50):
            if await success.is_visible():
                return
            if await error.is_visible():
                await self._redact_login_fields()
                await self.ctx.artifacts.screenshot(
                    "supplier-portal-login-failed",
                    step_id=step_id,
                )
                await self.ctx.events.emit(
                    "STEP_FAILED",
                    level="ERROR",
                    message="Supplier portal login failed",
                    payload={"stepId": step_id, "errorCode": "SRM_LOGIN_FAILED"},
                )
                raise RpaBusinessError(
                    "SRM_LOGIN_FAILED",
                    "Supplier portal login failed",
                )
            await self.page.wait_for_timeout(200)
        await self._redact_login_fields()
        await self.ctx.events.emit(
            "STEP_FAILED",
            level="ERROR",
            message="Supplier portal login timed out",
            payload={"stepId": step_id, "errorCode": "SRM_LOGIN_TIMEOUT"},
        )
        raise RpaRetryableError(
            "SRM_LOGIN_TIMEOUT",
            "Supplier portal login did not complete in time",
        )

    async def _redact_login_fields(self):
        for name in ("username", "password", "captcha"):
            try:
                await self.page.fill(self.selector(name), "")
            except Exception:
                # 即使失败的页面跳转已导致输入框脱离 DOM，证据采集也不能暴露凭据。
                continue

    async def open_orders(self):
        step_id = "srm.open_orders"
        await self.ctx.events.emit(
            "STEP_STARTED",
            message="Opening purchase order list",
            payload={"stepId": step_id, "stepType": "srm.open_orders"},
        )
        portal_root = self.ctx.portal_url.split("#", maxsplit=1)[0].rstrip("/")
        await self.page.goto(
            f"{portal_root}/#/supplier/orders",
            wait_until="domcontentloaded",
        )
        try:
            await self.page.locator(self.selector("order_page")).wait_for(
                state="visible",
                timeout=10000,
            )
        except Exception as exc:
            await self.ctx.events.emit(
                "STEP_FAILED",
                level="ERROR",
                message="Purchase order list could not be opened",
                payload={
                    "stepId": step_id,
                    "errorCode": "ORDER_LIST_UNAVAILABLE",
                },
            )
            raise RpaRetryableError(
                "ORDER_LIST_UNAVAILABLE",
                "Purchase order list could not be opened",
            ) from exc
        await self.ctx.events.emit(
            "STEP_SUCCEEDED",
            message="Purchase order list opened",
            payload={"stepId": step_id},
        )

    async def open_order_detail(self, po_no):
        step_id = "srm.search_po"
        await self.ctx.events.emit(
            "STEP_STARTED",
            message="Searching for purchase order",
            payload={"stepId": step_id, "stepType": "srm.search_po"},
        )
        await self.page.fill(self.selector("po_number"), po_no)
        await self.page.click(self.selector("search_button"))
        row = self.page.locator(self.selector("order_row", po_no=po_no))
        try:
            await row.wait_for(state="visible", timeout=10000)
        except Exception as exc:
            await self.ctx.artifacts.screenshot(
                "supplier-portal-po-not-found",
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
            ) from exc

        await self.page.click(self.selector("order_detail", po_no=po_no))
        try:
            await self.page.locator(self.selector("detail_page")).wait_for(
                state="visible",
                timeout=10000,
            )
            await self.page.locator(
                self.selector("detail_po_number", po_no=po_no)
            ).wait_for(state="visible", timeout=10000)
        except Exception as exc:
            await self.ctx.artifacts.screenshot(
                "supplier-portal-order-detail-failed",
                step_id=step_id,
            )
            await self.ctx.events.emit(
                "STEP_FAILED",
                level="ERROR",
                message="Purchase order detail could not be verified",
                payload={
                    "stepId": step_id,
                    "errorCode": "ORDER_DETAIL_UNAVAILABLE",
                },
            )
            raise RpaRetryableError(
                "ORDER_DETAIL_UNAVAILABLE",
                "Purchase order detail could not be verified",
            ) from exc

        await self.ctx.artifacts.screenshot(
            "supplier-portal-order-detail",
            step_id=step_id,
        )
        await self.ctx.events.emit(
            "STEP_SUCCEEDED",
            message="Purchase order detail opened",
            payload={"stepId": step_id, "poNo": po_no},
        )

    async def download_order(self, po_no):
        step_id = "file.download"
        await self.ctx.events.emit(
            "STEP_STARTED",
            message="Downloading purchase order",
            payload={"stepId": step_id, "stepType": "file.download"},
        )
        await self.page.click(self.selector("download_order"))
        confirm = self.page.locator(self.selector("download_confirm"))
        try:
            await confirm.wait_for(state="visible", timeout=5000)
            async with self.page.expect_download(timeout=15000) as download_info:
                await confirm.click()
            download = await download_info.value
        except Exception as exc:
            await self.ctx.artifacts.screenshot(
                "supplier-portal-download-failed",
                step_id=step_id,
            )
            await self.ctx.events.emit(
                "STEP_FAILED",
                level="ERROR",
                message="Purchase order download did not complete",
                payload={
                    "stepId": step_id,
                    "errorCode": "ORDER_DOWNLOAD_FAILED",
                },
            )
            raise RpaRetryableError(
                "ORDER_DOWNLOAD_FAILED",
                "Purchase order download did not complete",
            ) from exc

        suggested_filename = str(
            getattr(download, "suggested_filename", "") or ""
        ).strip()
        if not suggested_filename.lower().endswith(".xlsx"):
            await self.ctx.artifacts.screenshot(
                "supplier-portal-download-invalid",
                step_id=step_id,
            )
            await self.ctx.events.emit(
                "STEP_FAILED",
                level="ERROR",
                message="Supplier portal returned an unexpected download file",
                payload={
                    "stepId": step_id,
                    "errorCode": "ORDER_DOWNLOAD_FILE_INVALID",
                },
            )
            raise RpaFatalError(
                "ORDER_DOWNLOAD_FILE_INVALID",
                "Supplier portal returned an unexpected download file",
            )

        try:
            download_path = Path(await download.path())
            download_bytes = await asyncio.to_thread(download_path.read_bytes)
        except Exception as exc:
            await self.ctx.events.emit(
                "STEP_FAILED",
                level="ERROR",
                message="Supplier portal download could not be inspected",
                payload={
                    "stepId": step_id,
                    "errorCode": "ORDER_DOWNLOAD_FILE_UNREADABLE",
                },
            )
            raise RpaRetryableError(
                "ORDER_DOWNLOAD_FILE_UNREADABLE",
                "Supplier portal download could not be inspected",
            ) from exc
        if not download_bytes:
            await self.ctx.artifacts.screenshot(
                "supplier-portal-download-empty",
                step_id=step_id,
            )
            await self.ctx.events.emit(
                "STEP_FAILED",
                level="ERROR",
                message="Supplier portal returned an empty download file",
                payload={
                    "stepId": step_id,
                    "errorCode": "ORDER_DOWNLOAD_FILE_EMPTY",
                },
            )
            raise RpaRetryableError(
                "ORDER_DOWNLOAD_FILE_EMPTY",
                "Supplier portal returned an empty download file",
            )
        if not download_bytes.startswith(b"PK\x03\x04"):
            await self.ctx.artifacts.screenshot(
                "supplier-portal-download-invalid",
                step_id=step_id,
            )
            await self.ctx.events.emit(
                "STEP_FAILED",
                level="ERROR",
                message="Supplier portal returned an invalid XLSX file",
                payload={
                    "stepId": step_id,
                    "errorCode": "ORDER_DOWNLOAD_FILE_INVALID",
                },
            )
            raise RpaFatalError(
                "ORDER_DOWNLOAD_FILE_INVALID",
                "Supplier portal returned an invalid XLSX file",
            )

        artifact = await self.ctx.artifacts.save_download(
            download,
            suggested_filename,
            step_id=step_id,
        )
        artifact_size = getattr(artifact, "size", None)
        if not isinstance(artifact_size, int) or artifact_size <= 0:
            await self.ctx.events.emit(
                "STEP_FAILED",
                level="ERROR",
                message="Supplier portal returned an empty download file",
                payload={
                    "stepId": step_id,
                    "errorCode": "ORDER_DOWNLOAD_FILE_EMPTY",
                },
            )
            raise RpaRetryableError(
                "ORDER_DOWNLOAD_FILE_EMPTY",
                "Supplier portal returned an empty download file",
            )
        await self.ctx.artifacts.screenshot(
            "supplier-portal-download-succeeded",
            step_id=step_id,
        )
        await self.ctx.events.emit(
            "STEP_SUCCEEDED",
            message="Purchase order downloaded",
            payload={
                "stepId": step_id,
                "poNo": po_no,
                "artifactName": suggested_filename,
            },
        )


async def run(ctx):
    if not ctx.portal_url:
        raise RpaFatalError(
            "PORTAL_URL_MISSING",
            "Supplier portal URL is unavailable",
        )
    po_no = str(ctx.input.get("po_no", "")).strip().upper()
    if not PO_NUMBER_PATTERN.fullmatch(po_no):
        raise RpaBusinessError(
            "FLOW_INPUT_INVALID",
            "Purchase order number is missing or has an unsupported format",
        )

    await ctx.log.info(
        "Starting supplier portal purchase order flow",
        {"poNo": po_no},
    )
    adapter = SupplierPortalAdapter(ctx)
    await adapter.login()
    await adapter.open_orders()
    await adapter.open_order_detail(po_no)
    await adapter.download_order(po_no)
    await ctx.events.emit(
        "SUPPLIER_PORTAL_PO_COMPLETED",
        message="Supplier portal purchase order flow completed",
        payload={"poNo": po_no},
    )
