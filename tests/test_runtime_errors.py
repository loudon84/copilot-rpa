from __future__ import annotations

from nodeskclaw_rpa_engine.runtime.errors import (
    ErrorHandler,
    RpaBusinessError,
    RpaFatalError,
    RpaHumanRequiredError,
    RpaRetryableError,
)
from nodeskclaw_rpa_engine.workers.schemas import AttemptStatus


def test_retryable_error_retries_only_within_budget() -> None:
    handler = ErrorHandler()
    error = RpaRetryableError("TEMPORARY", "Temporary portal error")

    first = handler.classify(error, attempt_no=1, max_retries=2)
    final = handler.classify(error, attempt_no=3, max_retries=2)

    assert first.retry is True
    assert final.retry is False
    assert final.status is AttemptStatus.FAILED
    assert final.error_code == "TEMPORARY"


def test_standard_runtime_errors_map_to_safe_terminal_states() -> None:
    handler = ErrorHandler()

    business = handler.classify(
        RpaBusinessError("NOT_FOUND", "Record was not found"),
        attempt_no=1,
        max_retries=2,
    )
    human = handler.classify(
        RpaHumanRequiredError("MFA_REQUIRED", "Manual verification is required"),
        attempt_no=1,
        max_retries=2,
    )
    fatal = handler.classify(
        RpaFatalError("CONFIG_INVALID", "Runtime configuration is invalid"),
        attempt_no=1,
        max_retries=2,
    )

    assert business.status is AttemptStatus.FAILED
    assert human.status is AttemptStatus.WAITING_HUMAN
    assert fatal.status is AttemptStatus.FAILED


def test_unknown_error_does_not_expose_exception_message() -> None:
    decision = ErrorHandler().classify(
        RuntimeError("password=must-not-leak"),
        attempt_no=1,
        max_retries=2,
    )

    assert decision.error_code == "FLOW_UNHANDLED_ERROR"
    assert "must-not-leak" not in decision.error_message
