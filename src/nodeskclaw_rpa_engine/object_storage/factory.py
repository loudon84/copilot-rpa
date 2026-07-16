from __future__ import annotations

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.object_storage.base import ObjectStorageClient
from nodeskclaw_rpa_engine.object_storage.disabled import (
    DisabledObjectStorageClient,
)
from nodeskclaw_rpa_engine.object_storage.s3 import S3ObjectStorageClient


def build_object_storage(settings: Settings) -> ObjectStorageClient:
    if not settings.minio_enabled:
        return DisabledObjectStorageClient()
    if (
        settings.minio_endpoint_url is None
        or settings.minio_access_key is None
        or settings.minio_secret_key is None
    ):
        raise ValueError("Object-storage settings are incomplete")
    return S3ObjectStorageClient(
        endpoint_url=settings.minio_endpoint_url,
        access_key=settings.minio_access_key.get_secret_value(),
        secret_key=settings.minio_secret_key.get_secret_value(),
        bucket=settings.minio_bucket,
        region=settings.minio_region,
    )
