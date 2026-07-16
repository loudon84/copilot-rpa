from __future__ import annotations

import pytest

import nodeskclaw_rpa_engine.object_storage.s3 as s3_module
from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.object_storage.disabled import (
    DisabledObjectStorageClient,
)
from nodeskclaw_rpa_engine.object_storage.factory import build_object_storage


async def test_object_storage_is_disabled_by_default() -> None:
    client = build_object_storage(Settings(_env_file=None))

    assert isinstance(client, DisabledObjectStorageClient)
    await client.check()
    await client.close()


async def test_s3_health_check_uses_existing_bucket_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeS3Client:
        def head_bucket(self, *, Bucket: str) -> None:
            calls.append(("head_bucket", Bucket))

        def close(self) -> None:
            calls.append(("close", None))

    def fake_boto3_client(service_name: str, **kwargs):
        calls.append(("client", {"service": service_name, **kwargs}))
        return FakeS3Client()

    monkeypatch.setattr(s3_module.boto3, "client", fake_boto3_client)
    settings = Settings(
        _env_file=None,
        minio_enabled=True,
        minio_endpoint_url="http://minio.test:9000",
        minio_access_key="access-key",
        minio_secret_key="secret-key",
        minio_bucket="rpa-flow-packages",
    )
    client = build_object_storage(settings)

    await client.check()
    await client.close()

    assert ("head_bucket", "rpa-flow-packages") in calls
    assert not any(call[0] == "create_bucket" for call in calls)


async def test_s3_package_operations_use_configured_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeBody:
        def read(self) -> bytes:
            calls.append(("read", None))
            return b"package"

        def close(self) -> None:
            calls.append(("body_close", None))

    class FakeS3Client:
        def put_object(self, **kwargs) -> None:
            calls.append(("put_object", kwargs))

        def get_object(self, **kwargs):
            calls.append(("get_object", kwargs))
            return {"Body": FakeBody()}

        def delete_object(self, **kwargs) -> None:
            calls.append(("delete_object", kwargs))

        def generate_presigned_url(self, operation: str, **kwargs) -> str:
            calls.append(("presign", {"operation": operation, **kwargs}))
            return "http://minio.test/signed"

        def close(self) -> None:
            calls.append(("close", None))

    monkeypatch.setattr(
        s3_module.boto3,
        "client",
        lambda *args, **kwargs: FakeS3Client(),
    )
    settings = Settings(
        _env_file=None,
        minio_enabled=True,
        minio_endpoint_url="http://minio.test:9000",
        minio_access_key="access-key",
        minio_secret_key="secret-key",
        minio_bucket="rpa-flow-packages",
    )
    client = build_object_storage(settings)

    await client.put_package(
        "flows/test/1.0.0/checksum.zip",
        b"package",
        checksum_sha256="a" * 64,
    )
    content = await client.get_package("flows/test/1.0.0/checksum.zip")
    signed_url = await client.presign_download(
        "flows/test/1.0.0/checksum.zip",
        expires_seconds=900,
    )
    await client.delete_package("flows/test/1.0.0/checksum.zip")
    await client.close()

    assert content == b"package"
    assert signed_url == "http://minio.test/signed"
    assert any(call[0] == "put_object" for call in calls)
    assert any(call[0] == "get_object" for call in calls)
    assert any(call[0] == "delete_object" for call in calls)
    assert ("body_close", None) in calls
