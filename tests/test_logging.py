from __future__ import annotations

import io
import json
import logging

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.core.logging import (
    bind_log_context,
    configure_logging,
    redact_sensitive,
)


def test_structured_log_contains_context_and_redacts_sensitive_fields() -> None:
    stream = io.StringIO()
    configure_logging(Settings(_env_file=None, app_env="test"), stream=stream)
    logger = logging.getLogger("test.engine")

    with bind_log_context(
        run_id="run-1",
        worker_id="worker-1",
        flow_version_id="flow-version-1",
    ):
        logger.info(
            "connect postgresql://user:plain-secret@db/nodeskclaw_task",
            extra={
                "password": "plain-secret",
                "details": {"token": "token-value", "attempt": 1},
            },
        )

    payload = json.loads(stream.getvalue().strip())
    assert payload["runId"] == "run-1"
    assert payload["workerId"] == "worker-1"
    assert payload["flowVersionId"] == "flow-version-1"
    assert payload["fields"]["password"] == "***"
    assert payload["fields"]["details"]["token"] == "***"
    assert "plain-secret" not in stream.getvalue()
    assert "token-value" not in stream.getvalue()


def test_log_context_is_reset_after_scope() -> None:
    stream = io.StringIO()
    configure_logging(Settings(_env_file=None, app_env="test"), stream=stream)
    logger = logging.getLogger("test.context")

    with bind_log_context(run_id="run-scoped"):
        logger.info("inside")
    logger.info("outside")

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert lines[0]["runId"] == "run-scoped"
    assert lines[1]["runId"] is None


def test_signed_url_query_credentials_are_redacted() -> None:
    value = redact_sensitive(
        "PUT http://storage.test/file?X-Amz-Credential=user%2Fscope"
        "&X-Amz-Signature=top-secret&other=visible"
    )

    assert "top-secret" not in value
    assert "user%2Fscope" not in value
    assert "X-Amz-Signature=***" in value
    assert "other=visible" in value


def test_inline_secret_assignments_are_redacted() -> None:
    value = redact_sensitive(
        "login failed password=plain-secret token=token-value reason=invalid"
    )

    assert value == "login failed password=*** token=*** reason=invalid"
