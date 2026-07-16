from __future__ import annotations

from typing import Any


class WorkerError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 500,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class WorkerConfigurationError(WorkerError):
    def __init__(self, message: str) -> None:
        super().__init__(
            "WORKER_CONFIGURATION_INVALID",
            message,
            status_code=500,
        )


class TaskApiError(WorkerError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=503)


class RunCommandRejected(WorkerError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=409)
