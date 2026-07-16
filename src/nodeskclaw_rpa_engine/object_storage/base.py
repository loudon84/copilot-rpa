from __future__ import annotations

from typing import Protocol


class ObjectStorageClient(Protocol):
    @property
    def bucket_name(self) -> str: ...

    async def check(self) -> None: ...

    async def put_package(
        self,
        object_key: str,
        content: bytes,
        *,
        checksum_sha256: str,
    ) -> None: ...

    async def get_package(self, object_key: str) -> bytes: ...

    async def delete_package(self, object_key: str) -> None: ...

    async def package_exists(self, object_key: str) -> bool: ...

    async def presign_download(
        self,
        object_key: str,
        *,
        expires_seconds: int,
    ) -> str: ...

    async def close(self) -> None: ...
