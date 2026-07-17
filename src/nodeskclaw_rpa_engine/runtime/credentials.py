from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from nodeskclaw_rpa_engine.core.config import CredentialResolverMode, Settings
from nodeskclaw_rpa_engine.runtime.context import (
    CredentialResolver,
    DisabledCredentialResolver,
)
from nodeskclaw_rpa_engine.runtime.errors import RpaFatalError


class MockEnvironmentCredentialResolver:
    """在精确命令作用域内解析一组仅供开发使用的凭据。"""

    __slots__ = (
        "_allowed_portal_account_id",
        "_allowed_tenant_id",
        "_credential_ref",
        "_password",
        "_username",
    )

    def __init__(
        self,
        *,
        credential_ref: str,
        username: str,
        password: str,
        allowed_tenant_id: str,
        allowed_portal_account_id: str,
    ) -> None:
        configured_values = (
            credential_ref,
            username,
            password,
            allowed_tenant_id,
            allowed_portal_account_id,
        )
        if any(not value or not value.strip() for value in configured_values):
            raise RpaFatalError(
                "CREDENTIAL_CONFIGURATION_INVALID",
                "Mock credential resolver configuration is incomplete",
            )
        self._credential_ref = credential_ref
        self._username = username
        self._password = password
        self._allowed_tenant_id = allowed_tenant_id
        self._allowed_portal_account_id = allowed_portal_account_id

    async def resolve(
        self,
        credential_ref: str | None,
        *,
        tenant_id: str | None,
        portal_account_id: str | None,
    ) -> Mapping[str, Any]:
        if (
            credential_ref != self._credential_ref
            or tenant_id != self._allowed_tenant_id
            or portal_account_id != self._allowed_portal_account_id
        ):
            raise RpaFatalError(
                "CREDENTIAL_SCOPE_MISMATCH",
                "Credential reference or scope is not authorized",
            )
        return MappingProxyType(
            {
                "username": self._username,
                "password": self._password,
            }
        )


def build_credential_resolver(settings: Settings) -> CredentialResolver:
    """构建已配置的解析器，且不暴露秘密值。"""
    if settings.credential_resolver_mode is CredentialResolverMode.DISABLED:
        return DisabledCredentialResolver()

    credential_ref = settings.mock_srm_credential_ref
    username = settings.mock_srm_username
    password = settings.mock_srm_password
    tenant_id = settings.mock_srm_allowed_tenant_id
    portal_account_id = settings.mock_srm_allowed_portal_account_id
    if (
        credential_ref is None
        or username is None
        or password is None
        or tenant_id is None
        or portal_account_id is None
    ):
        raise RpaFatalError(
            "CREDENTIAL_CONFIGURATION_INVALID",
            "Mock credential resolver configuration is incomplete",
        )
    return MockEnvironmentCredentialResolver(
        credential_ref=credential_ref,
        username=username.get_secret_value(),
        password=password.get_secret_value(),
        allowed_tenant_id=tenant_id,
        allowed_portal_account_id=portal_account_id,
    )
