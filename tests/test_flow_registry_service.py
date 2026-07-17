from __future__ import annotations

import asyncio
import hashlib
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

import nodeskclaw_rpa_engine.flows.service as service_module
from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.db.models import RpaFlow, RpaFlowVersion
from nodeskclaw_rpa_engine.flows.errors import FlowRegistryError
from nodeskclaw_rpa_engine.flows.manifest import FlowManifest
from nodeskclaw_rpa_engine.flows.package import ValidatedPackage
from nodeskclaw_rpa_engine.flows.schemas import ActorContext, FlowScope
from nodeskclaw_rpa_engine.flows.service import FlowRegistryService


def validated_package() -> ValidatedPackage:
    manifest = FlowManifest.model_validate(
        {
            "rpaFlowId": "rpa_flow_registry_test",
            "name": "Registry Test",
            "version": "1.0.0",
            "engineType": "PLAYWRIGHT_CDP",
            "entrypoint": "flow.py:run",
            "supportedWorkflowCodes": ["registry_test"],
        }
    )
    return ValidatedPackage(
        content=b"validated-package",
        manifest=manifest,
        checksum_sha256=hashlib.sha256(b"validated-package").hexdigest(),
        size_bytes=len(b"validated-package"),
        checks=[],
        warnings=[],
    )


class RecordingValidator:
    def __init__(self, package: ValidatedPackage) -> None:
        self.package = package
        self.thread_ids: list[int] = []

    def validate(
        self,
        _filename: str | None,
        _content: bytes,
    ) -> ValidatedPackage:
        self.thread_ids.append(threading.get_ident())
        return self.package


class TransactionState:
    def __init__(self, *, exit_error: Exception | None = None) -> None:
        self.active = False
        self.exit_error = exit_error


class FakeTransaction:
    def __init__(self, state: TransactionState) -> None:
        self._state = state

    async def __aenter__(self) -> None:
        self._state.active = True

    async def __aexit__(self, exc_type: object, *_args: object) -> None:
        self._state.active = False
        if exc_type is None and self._state.exit_error is not None:
            raise self._state.exit_error


class FakeSession:
    def __init__(self, state: TransactionState) -> None:
        self.state = state

    def begin(self) -> FakeTransaction:
        return FakeTransaction(self.state)


class FakeDatabase:
    def __init__(self, state: TransactionState) -> None:
        self.session_value = FakeSession(state)

    @asynccontextmanager
    async def session(self):
        yield self.session_value


class FakeStorage:
    bucket_name = "flow-packages"

    def __init__(
        self,
        state: TransactionState,
        *,
        objects: dict[str, bytes] | None = None,
    ) -> None:
        self._state = state
        self.objects = dict(objects or {})
        self.put_keys: list[str] = []
        self.delete_keys: list[str] = []
        self.put_transaction_states: list[bool] = []

    async def put_package(
        self,
        object_key: str,
        content: bytes,
        *,
        checksum_sha256: str,
    ) -> None:
        assert checksum_sha256 == hashlib.sha256(content).hexdigest()
        self.put_transaction_states.append(self._state.active)
        self.put_keys.append(object_key)
        self.objects[object_key] = content

    async def get_package(self, object_key: str) -> bytes:
        return self.objects[object_key]

    async def delete_package(self, object_key: str) -> None:
        self.delete_keys.append(object_key)
        self.objects.pop(object_key, None)


def test_package_object_keys_are_unique_and_scope_isolated() -> None:
    package = validated_package()
    tenant_id = "customer/unsafe-looking-tenant"
    tenant_hash = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()

    global_keys = {
        FlowRegistryService._package_object_key(
            package,
            scope=FlowScope.GLOBAL,
            tenant_id=None,
        )
        for _ in range(2)
    }
    tenant_keys = {
        FlowRegistryService._package_object_key(
            package,
            scope=FlowScope.TENANT,
            tenant_id=tenant_id,
        )
        for _ in range(2)
    }
    other_tenant_key = FlowRegistryService._package_object_key(
        package,
        scope=FlowScope.TENANT,
        tenant_id="another-tenant",
    )

    assert len(global_keys) == 2
    assert len(tenant_keys) == 2
    assert all(key.startswith("flows/global/") for key in global_keys)
    assert all(
        key.startswith(f"flows/tenant/{tenant_hash}/") for key in tenant_keys
    )
    assert tenant_id not in " ".join(tenant_keys)
    assert other_tenant_key not in tenant_keys
    assert global_keys.isdisjoint(tenant_keys)


async def test_upload_validates_off_loop_and_puts_before_db_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = validated_package()
    validator = RecordingValidator(package)
    state = TransactionState()
    old_key = "flows/global/existing/1.0.0/committed.zip"
    storage = FakeStorage(state, objects={old_key: b"committed"})
    database = FakeDatabase(state)

    class FailingRepository:
        def __init__(self, session: FakeSession) -> None:
            assert session.state.active

        async def get_flow(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated database failure")

    monkeypatch.setattr(
        service_module,
        "SqlAlchemyFlowRepository",
        FailingRepository,
    )
    service = FlowRegistryService(
        Settings(_env_file=None),
        database,  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
    )
    service._validator = validator  # type: ignore[assignment]
    loop_thread_id = threading.get_ident()

    with pytest.raises(FlowRegistryError) as captured:
        await service.upload_package(
            ActorContext(actor_id="registry-test"),
            scope=FlowScope.GLOBAL,
            description=None,
            labels=[],
            filename="flow.zip",
            content=package.content,
        )

    assert captured.value.code == "FLOW_PACKAGE_STORAGE_FAILED"
    assert validator.thread_ids
    assert validator.thread_ids[0] != loop_thread_id
    assert storage.put_transaction_states == [False]
    assert storage.delete_keys == storage.put_keys
    assert len(storage.put_keys) == 1
    assert storage.put_keys[0] not in storage.objects
    assert storage.objects[old_key] == b"committed"


async def test_upload_cancellation_before_db_body_completion_waits_for_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = validated_package()
    state = TransactionState()
    db_body_started = asyncio.Event()

    class BlockingDeleteStorage(FakeStorage):
        def __init__(self, transaction_state: TransactionState) -> None:
            super().__init__(transaction_state)
            self.delete_started = asyncio.Event()
            self.allow_delete = asyncio.Event()

        async def delete_package(self, object_key: str) -> None:
            self.delete_started.set()
            await self.allow_delete.wait()
            await super().delete_package(object_key)

    class BlockingRepository:
        def __init__(self, session: FakeSession) -> None:
            assert session.state.active

        async def get_flow(self, *_args: object, **_kwargs: object) -> None:
            db_body_started.set()
            await asyncio.Event().wait()

    storage = BlockingDeleteStorage(state)
    monkeypatch.setattr(
        service_module,
        "SqlAlchemyFlowRepository",
        BlockingRepository,
    )
    service = FlowRegistryService(
        Settings(_env_file=None),
        FakeDatabase(state),  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
    )
    service._validator = RecordingValidator(package)  # type: ignore[assignment]

    upload = asyncio.create_task(
        service.upload_package(
            ActorContext(actor_id="registry-test"),
            scope=FlowScope.GLOBAL,
            description=None,
            labels=[],
            filename="flow.zip",
            content=package.content,
        )
    )
    await asyncio.wait_for(db_body_started.wait(), timeout=1)
    upload.cancel()
    await asyncio.wait_for(storage.delete_started.wait(), timeout=1)

    assert not upload.done()
    storage.allow_delete.set()
    with pytest.raises(asyncio.CancelledError):
        await upload

    assert storage.delete_keys == storage.put_keys
    assert len(storage.put_keys) == 1
    assert storage.put_keys[0] not in storage.objects


@pytest.mark.parametrize(
    ("exit_error", "expected_code", "expect_cleanup"),
    [
        pytest.param(
            RuntimeError("commit outcome is unknown"),
            "FLOW_PACKAGE_STORAGE_FAILED",
            False,
            id="ambiguous-commit-retains-object",
        ),
        pytest.param(
            IntegrityError(
                "COMMIT",
                {},
                RuntimeError("deferred constraint rejected commit"),
            ),
            "FLOW_VERSION_EXISTS",
            True,
            id="integrity-error-cleans-object",
        ),
    ],
)
async def test_upload_commit_failure_uses_conservative_compensation(
    monkeypatch: pytest.MonkeyPatch,
    exit_error: Exception,
    expected_code: str,
    expect_cleanup: bool,
) -> None:
    package = validated_package()
    state = TransactionState(exit_error=exit_error)
    storage = FakeStorage(state)
    now = datetime.now(UTC)
    flow = RpaFlow(
        id=uuid4(),
        flow_key=package.manifest.rpa_flow_id,
        scope=FlowScope.GLOBAL.value,
        tenant_id=None,
        name=package.manifest.name,
        status="ACTIVE",
        labels=[],
        created_by="registry-test",
        created_at=now,
        updated_at=now,
    )

    class PreparedRepository:
        def __init__(self, session: FakeSession) -> None:
            assert session.state.active

        async def get_flow(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> RpaFlow:
            return flow

        async def get_version_for_flow(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> None:
            return None

        def add_version(self, _version: object) -> None:
            pass

        def add_validation(self, _validation: object) -> None:
            pass

        def add_audit(self, _audit: object) -> None:
            pass

        async def flush(self) -> None:
            pass

        async def refresh(self, _instance: object) -> None:
            pass

    monkeypatch.setattr(
        service_module,
        "SqlAlchemyFlowRepository",
        PreparedRepository,
    )
    service = FlowRegistryService(
        Settings(_env_file=None),
        FakeDatabase(state),  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
    )
    service._validator = RecordingValidator(package)  # type: ignore[assignment]

    with pytest.raises(FlowRegistryError) as captured:
        await service.upload_package(
            ActorContext(actor_id="registry-test"),
            scope=FlowScope.GLOBAL,
            description=None,
            labels=[],
            filename="flow.zip",
            content=package.content,
        )

    assert captured.value.code == expected_code
    assert len(storage.put_keys) == 1
    object_key = storage.put_keys[0]
    if expect_cleanup:
        assert storage.delete_keys == [object_key]
        assert object_key not in storage.objects
    else:
        assert storage.delete_keys == []
        assert storage.objects[object_key] == package.content


async def test_stored_package_revalidation_runs_validator_off_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = validated_package()
    validator = RecordingValidator(package)
    state = TransactionState()
    object_key = "flows/global/registry/1.0.0/upload.zip"
    storage = FakeStorage(state, objects={object_key: package.content})
    flow_id = uuid4()
    now = datetime.now(UTC)
    flow = RpaFlow(
        id=flow_id,
        flow_key=package.manifest.rpa_flow_id,
        scope=FlowScope.GLOBAL.value,
        tenant_id=None,
        name=package.manifest.name,
        status="ACTIVE",
        labels=[],
        created_by="registry-test",
        created_at=now,
        updated_at=now,
    )
    version = RpaFlowVersion(
        id=uuid4(),
        flow_id=flow_id,
        version=package.manifest.version,
        package_object_key=object_key,
        package_checksum_sha256=package.checksum_sha256,
    )

    class ExistingRepository:
        def __init__(self, _session: FakeSession) -> None:
            pass

        async def get_version(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> tuple[RpaFlowVersion, RpaFlow]:
            return version, flow

    monkeypatch.setattr(
        service_module,
        "SqlAlchemyFlowRepository",
        ExistingRepository,
    )
    service = FlowRegistryService(
        Settings(_env_file=None),
        FakeDatabase(state),  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
    )
    service._validator = validator  # type: ignore[assignment]
    loop_thread_id = threading.get_ident()

    loaded_version, loaded_flow, result = (
        await service._load_and_validate_package(
            ActorContext(actor_id="registry-test"),
            version.id,
            raise_on_invalid=False,
        )
    )

    assert loaded_version is version
    assert loaded_flow is flow
    assert result is package
    assert validator.thread_ids
    assert validator.thread_ids[0] != loop_thread_id
