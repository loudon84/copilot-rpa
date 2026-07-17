from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.exceptions import ClientError


class S3ObjectStorageClient:
    """轻量的 S3 兼容基础实现；包操作在 Phase 2 中提供。"""

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
    ) -> None:
        self._bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    @property
    def bucket_name(self) -> str:
        return self._bucket

    async def check(self) -> None:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)

    async def put_package(
        self,
        object_key: str,
        content: bytes,
        *,
        checksum_sha256: str,
    ) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=object_key,
            Body=content,
            ContentType="application/zip",
            Metadata={"sha256": checksum_sha256},
        )

    async def get_package(self, object_key: str) -> bytes:
        def read_object() -> bytes:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=object_key,
            )
            body = response["Body"]
            try:
                result: bytes = body.read()
                return result
            finally:
                body.close()

        return await asyncio.to_thread(read_object)

    async def delete_package(self, object_key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object,
            Bucket=self._bucket,
            Key=object_key,
        )

    async def package_exists(self, object_key: str) -> bool:
        try:
            await asyncio.to_thread(
                self._client.head_object,
                Bucket=self._bucket,
                Key=object_key,
            )
        except ClientError as exc:
            status_code = exc.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            )
            if status_code == 404:
                return False
            raise
        return True

    async def presign_download(
        self,
        object_key: str,
        *,
        expires_seconds: int,
    ) -> str:
        result: str = await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": object_key},
            ExpiresIn=expires_seconds,
        )
        return result

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)
