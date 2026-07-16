from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from nodeskclaw_rpa_engine.workers.schemas import AttemptStatus


class RpaRuntimeError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.details = details or {}


class RpaRetryableError(RpaRuntimeError):
    pass


class RpaBusinessError(RpaRuntimeError):
    pass


class RpaHumanRequiredError(RpaRuntimeError):
    pass


class RpaFatalError(RpaRuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ErrorDecision:
    status: AttemptStatus
    error_code: str
    error_message: str
    retry: bool = False


class ErrorHandler:
    def classify(
        self,
        error: Exception,
        *,
        attempt_no: int,
        max_retries: int,
    ) -> ErrorDecision:
        if isinstance(error, RpaHumanRequiredError):
            return ErrorDecision(
                status=AttemptStatus.WAITING_HUMAN,
                error_code=error.code,
                error_message=error.safe_message,
            )
        if isinstance(error, RpaBusinessError):
            return ErrorDecision(
                status=AttemptStatus.FAILED,
                error_code=error.code,
                error_message=error.safe_message,
            )
        if isinstance(error, RpaFatalError):
            return ErrorDecision(
                status=AttemptStatus.FAILED,
                error_code=error.code,
                error_message=error.safe_message,
            )
        if isinstance(error, (RpaRetryableError, TimeoutError, PlaywrightError)):
            code = (
                error.code
                if isinstance(error, RpaRetryableError)
                else (
                    "RUNTIME_TIMEOUT"
                    if isinstance(error, (TimeoutError, PlaywrightTimeoutError))
                    else "PLAYWRIGHT_OPERATION_FAILED"
                )
            )
            message = (
                error.safe_message
                if isinstance(error, RpaRetryableError)
                else (
                    "Flow execution timed out"
                    if isinstance(error, (TimeoutError, PlaywrightTimeoutError))
                    else "Browser operation failed"
                )
            )
            return ErrorDecision(
                status=AttemptStatus.FAILED,
                error_code=code,
                error_message=message,
                retry=attempt_no <= max_retries,
            )
        return ErrorDecision(
            status=AttemptStatus.FAILED,
            error_code="FLOW_UNHANDLED_ERROR",
            error_message="Flow execution failed",
        )
