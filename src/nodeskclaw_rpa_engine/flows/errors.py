from __future__ import annotations

from typing import Any


class FlowRegistryError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class PackageValidationError(FlowRegistryError):
    def __init__(self, issues: list[dict[str, str]]) -> None:
        super().__init__(
            "FLOW_PACKAGE_INVALID",
            "Flow package validation failed",
            status_code=422,
            details=issues,
        )
