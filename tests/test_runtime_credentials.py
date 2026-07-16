from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.runtime.context import DisabledCredentialResolver
from nodeskclaw_rpa_engine.runtime.credentials import (
    MockEnvironmentCredentialResolver,
    build_credential_resolver,
)
from nodeskclaw_rpa_engine.runtime.errors import RpaFatalError


def resolver() -> MockEnvironmentCredentialResolver:
    return MockEnvironmentCredentialResolver(
        credential_ref="mock-srm-credential",
        username="mock-user",
        password="mock-password",
        allowed_tenant_id="tenant-demo",
        allowed_portal_account_id="portal-demo",
    )


async def test_resolver_returns_a_fresh_read_only_mapping_for_exact_scope() -> None:
    subject = resolver()

    first = await subject.resolve(
        "mock-srm-credential",
        tenant_id="tenant-demo",
        portal_account_id="portal-demo",
    )
    second = await subject.resolve(
        "mock-srm-credential",
        tenant_id="tenant-demo",
        portal_account_id="portal-demo",
    )

    assert first == {"username": "mock-user", "password": "mock-password"}
    assert second == first
    assert second is not first
    with pytest.raises(TypeError):
        first["password"] = "replacement"  # type: ignore[index]


@pytest.mark.parametrize(
    ("credential_ref", "tenant_id", "portal_account_id"),
    [
        (None, "tenant-demo", "portal-demo"),
        ("other-credential", "tenant-demo", "portal-demo"),
        ("mock-srm-credential", None, "portal-demo"),
        ("mock-srm-credential", "other-tenant", "portal-demo"),
        ("mock-srm-credential", "tenant-demo", None),
        ("mock-srm-credential", "tenant-demo", "other-portal"),
    ],
)
async def test_resolver_rejects_any_reference_or_scope_mismatch(
    credential_ref: str | None,
    tenant_id: str | None,
    portal_account_id: str | None,
) -> None:
    with pytest.raises(RpaFatalError) as captured:
        await resolver().resolve(
            credential_ref,
            tenant_id=tenant_id,
            portal_account_id=portal_account_id,
        )

    assert captured.value.code == "CREDENTIAL_SCOPE_MISMATCH"
    assert captured.value.safe_message == (
        "Credential reference or scope is not authorized"
    )
    assert captured.value.details == {}
    assert "mock-password" not in str(captured.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"credential_ref": ""},
        {"username": " "},
        {"password": ""},
        {"allowed_tenant_id": "\t"},
        {"allowed_portal_account_id": ""},
    ],
)
def test_resolver_rejects_incomplete_configuration_without_leaking_values(
    overrides: dict[str, str],
) -> None:
    arguments = {
        "credential_ref": "mock-srm-credential",
        "username": "mock-user",
        "password": "mock-password",
        "allowed_tenant_id": "tenant-demo",
        "allowed_portal_account_id": "portal-demo",
    }
    arguments.update(overrides)

    with pytest.raises(RpaFatalError) as captured:
        MockEnvironmentCredentialResolver(**arguments)

    assert captured.value.code == "CREDENTIAL_CONFIGURATION_INVALID"
    assert captured.value.safe_message == (
        "Mock credential resolver configuration is incomplete"
    )
    message = str(captured.value)
    for value in arguments.values():
        if value.strip():
            assert value not in message


async def test_resolver_does_not_emit_logs(caplog: pytest.LogCaptureFixture) -> None:
    credentials: Mapping[str, Any] = await resolver().resolve(
        "mock-srm-credential",
        tenant_id="tenant-demo",
        portal_account_id="portal-demo",
    )

    assert credentials["username"] == "mock-user"
    assert caplog.records == []


def test_factory_preserves_disabled_default() -> None:
    subject = build_credential_resolver(Settings(_env_file=None))

    assert isinstance(subject, DisabledCredentialResolver)


async def test_factory_uses_validated_secret_settings() -> None:
    subject = build_credential_resolver(
        Settings(
            _env_file=None,
            app_env="test",
            credential_resolver_mode="mock_env",
            mock_srm_credential_ref="mock-srm-credential",
            mock_srm_username="mock-user",
            mock_srm_password="mock-password",
            mock_srm_allowed_tenant_id="tenant-demo",
            mock_srm_allowed_portal_account_id="portal-demo",
        )
    )

    credentials = await subject.resolve(
        "mock-srm-credential",
        tenant_id="tenant-demo",
        portal_account_id="portal-demo",
    )

    assert credentials == {
        "username": "mock-user",
        "password": "mock-password",
    }
