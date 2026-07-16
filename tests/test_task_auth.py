from __future__ import annotations

import pytest
from pydantic import SecretStr

from nodeskclaw_rpa_engine.task_api.auth import (
    NoAuthProvider,
    ServiceAccountAuthProvider,
)


async def test_no_auth_provider_matches_current_test_worker_api() -> None:
    assert await NoAuthProvider().headers() == {}


async def test_service_account_exchange_is_explicitly_deferred() -> None:
    provider = ServiceAccountAuthProvider(
        "worker-client",
        SecretStr("never-log-this"),
    )
    with pytest.raises(NotImplementedError, match="not implemented yet"):
        await provider.headers()
