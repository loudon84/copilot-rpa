from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.db.models import RpaFlow, RpaFlowVersion
from nodeskclaw_rpa_engine.workers.errors import RunCommandRejected
from nodeskclaw_rpa_engine.workers.resolver import FlowVersionResolver
from tests.test_worker_pool import lease


class FakeResult:
    def __init__(self, rows: list[tuple[RpaFlowVersion, RpaFlow]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[RpaFlowVersion, RpaFlow]]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[tuple[RpaFlowVersion, RpaFlow]]) -> None:
        self._rows = rows

    async def execute(self, _statement) -> FakeResult:
        return FakeResult(self._rows)


class FakeDatabase:
    def __init__(self, rows: list[tuple[RpaFlowVersion, RpaFlow]]) -> None:
        self._session = FakeSession(rows)

    @asynccontextmanager
    async def session(self):
        yield self._session


def published_records() -> tuple[RpaFlowVersion, RpaFlow]:
    now = datetime.now(UTC)
    flow_id = uuid4()
    flow = RpaFlow(
        id=flow_id,
        flow_key="flow-1",
        scope="TENANT",
        tenant_id="tenant-1",
        name="Flow 1",
        status="ACTIVE",
        labels=[],
        created_by="tester",
        created_at=now,
        updated_at=now,
    )
    version = RpaFlowVersion(
        id=uuid4(),
        flow_id=flow_id,
        version="1.0.0",
        status="PUBLISHED",
        engine_type="PLAYWRIGHT_CDP",
        entrypoint="flow.py:run",
        manifest={},
        supported_workflow_codes=["fetch_po"],
        supported_portal_types=[],
        input_schema=[],
        capabilities=["download"],
        package_bucket="rpa-flow-packages",
        package_object_key="flows/flow-1/1.0.0/package.zip",
        package_size_bytes=100,
        package_checksum_sha256="a" * 64,
        created_by="tester",
        created_at=now,
        published_at=now,
        updated_at=now,
    )
    return version, flow


async def test_resolver_returns_only_the_exact_published_version() -> None:
    version, flow = published_records()
    settings = Settings(
        _env_file=None,
        rpa_engine_public_base_url="http://engine.test:4610",
    )
    resolver = FlowVersionResolver(
        settings,
        FakeDatabase([(version, flow)]),  # type: ignore[arg-type]
    )

    resolved = await resolver.resolve(lease())

    assert resolved.flow_version_id == version.id
    assert resolved.version == "1.0.0"
    assert resolved.package_checksum == "a" * 64
    assert resolved.package_uri.endswith(f"/{version.id}/package")


async def test_resolver_rejects_non_published_exact_version() -> None:
    version, flow = published_records()
    version.status = "DISABLED"
    resolver = FlowVersionResolver(
        Settings(_env_file=None),
        FakeDatabase([(version, flow)]),  # type: ignore[arg-type]
    )

    with pytest.raises(RunCommandRejected) as captured:
        await resolver.resolve(lease())

    assert captured.value.code == "FLOW_VERSION_NOT_EXECUTABLE"
