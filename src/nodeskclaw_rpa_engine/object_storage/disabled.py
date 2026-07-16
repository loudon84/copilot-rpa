from __future__ import annotations


class DisabledObjectStorageClient:
    @property
    def bucket_name(self) -> str:
        return ""

    async def check(self) -> None:
        return None

    async def put_package(
        self,
        object_key: str,
        content: bytes,
        *,
        checksum_sha256: str,
    ) -> None:
        raise RuntimeError("Object storage is disabled")

    async def get_package(self, object_key: str) -> bytes:
        raise RuntimeError("Object storage is disabled")

    async def delete_package(self, object_key: str) -> None:
        raise RuntimeError("Object storage is disabled")

    async def package_exists(self, object_key: str) -> bool:
        return False

    async def presign_download(
        self,
        object_key: str,
        *,
        expires_seconds: int,
    ) -> str:
        raise RuntimeError("Object storage is disabled")

    async def close(self) -> None:
        return None
