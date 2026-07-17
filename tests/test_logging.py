from __future__ import annotations

import io
import json
import logging

import pytest

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


@pytest.mark.parametrize(
    ("raw", "secret", "expected"),
    [
        (
            "login failed password: colon-secret reason=visible",
            "colon-secret",
            "password: ***",
        ),
        (
            "login failed password = spaced-secret reason=visible",
            "spaced-secret",
            "password = ***",
        ),
        (
            "{\"password\":\"json-secret\",\"reason\":\"visible\"}",
            "json-secret",
            "\"password\":\"***\"",
        ),
        (
            "{'token': 'dict-secret', 'reason': 'visible'}",
            "dict-secret",
            "'token': '***'",
        ),
        (
            "Authorization: Basic ZGVtbzpkZW1v; reason=visible",
            "ZGVtbzpkZW1v",
            "Authorization: ***",
        ),
        (
            "Authorization: Custom custom-secret; reason=visible",
            "custom-secret",
            "Authorization: ***",
        ),
        (
            "Authorization: Digest username=demo, nonce=digest-secret; "
            "reason=visible",
            "digest-secret",
            "Authorization: ***",
        ),
        (
            "MINIO_SECRET_KEY=minio-secret reason=visible",
            "minio-secret",
            "MINIO_SECRET_KEY=***",
        ),
        (
            "api_key=api-secret reason=visible",
            "api-secret",
            "api_key=***",
        ),
        (
            "Cookie: sessionid=cookie-secret; theme=dark",
            "cookie-secret",
            "Cookie: ***",
        ),
        (
            "session_id=session-secret reason=visible",
            "session-secret",
            "session_id=***",
        ),
    ],
)
def test_extended_sensitive_formats_are_redacted_from_tracebacks(
    raw: str,
    secret: str,
    expected: str,
) -> None:
    assert secret not in redact_sensitive(raw)
    assert expected in redact_sensitive(raw)

    stream = io.StringIO()
    configure_logging(Settings(_env_file=None, app_env="test"), stream=stream)
    logger = logging.getLogger("test.extended-sensitive-traceback")

    try:
        raise RuntimeError(raw)
    except RuntimeError:
        logger.exception("extended sensitive format failed")

    traceback = json.loads(stream.getvalue().strip())["exception"]
    assert "RuntimeError" in traceback
    assert secret not in traceback
    assert expected in traceback


def test_exception_chain_is_redacted_without_losing_traceback() -> None:
    stream = io.StringIO()
    configure_logging(Settings(_env_file=None, app_env="test"), stream=stream)
    logger = logging.getLogger("test.exception-chain")

    try:
        try:
            raise ValueError(
                "inner failure password=inner-secret reason=visible-inner"
            )
        except ValueError as exc:
            raise RuntimeError(
                "outer failure token=outer-secret reason=visible-outer"
            ) from exc
    except RuntimeError:
        logger.exception("exception chain failed")

    payload = json.loads(stream.getvalue().strip())
    traceback = payload["exception"]
    assert "Traceback (most recent call last)" in traceback
    assert "test_exception_chain_is_redacted_without_losing_traceback" in traceback
    assert "ValueError" in traceback
    assert "RuntimeError" in traceback
    assert "The above exception was the direct cause" in traceback
    assert "reason=visible-inner" in traceback
    assert "reason=visible-outer" in traceback
    assert "password=***" in traceback
    assert "token=***" in traceback
    assert "inner-secret" not in traceback
    assert "outer-secret" not in traceback


def test_exception_traceback_redacts_url_bearer_and_signed_query() -> None:
    stream = io.StringIO()
    configure_logging(Settings(_env_file=None, app_env="test"), stream=stream)
    logger = logging.getLogger("test.exception-url")

    try:
        raise ConnectionError(
            "request failed "
            "https://worker:url-secret@storage.test/file?"
            "X-Amz-Credential=scope-secret&X-Amz-Signature=signature-secret "
            "Authorization: Bearer bearer-secret; visible=kept"
        )
    except ConnectionError:
        logger.exception("artifact request failed")

    payload = json.loads(stream.getvalue().strip())
    traceback = payload["exception"]
    assert "ConnectionError" in traceback
    assert "test_exception_traceback_redacts_url_bearer_and_signed_query" in traceback
    assert "https://worker:***@storage.test/file?" in traceback
    assert "X-Amz-Credential=***" in traceback
    assert "X-Amz-Signature=***" in traceback
    assert "Authorization: ***" in traceback
    assert "visible=kept" in traceback
    for secret in (
        "url-secret",
        "scope-secret",
        "signature-secret",
        "bearer-secret",
    ):
        assert secret not in traceback
