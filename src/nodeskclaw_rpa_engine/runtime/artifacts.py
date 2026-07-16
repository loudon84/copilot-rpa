from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from nodeskclaw_rpa_engine.runtime.errors import (
    RpaFatalError,
    RpaRetryableError,
)
from nodeskclaw_rpa_engine.workers.errors import TaskApiError
from nodeskclaw_rpa_engine.workers.schemas import (
    ArtifactUploadUrlRequest,
    RunArtifactCreate,
)
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient


class ArtifactType(StrEnum):
    SCREENSHOT = "screenshot"
    DOWNLOAD = "download"
    TRACE = "trace"
    LOG = "log"


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    type: ArtifactType
    name: str
    storage_key: str
    size: int
    mime_type: str
    checksum_sha256: str
    step_id: str | None = None


class ArtifactSink(Protocol):
    async def upload(
        self,
        *,
        task_id: str,
        run_id: str,
        artifact_type: ArtifactType,
        name: str,
        path: Path,
        size: int,
        mime_type: str,
    ) -> str: ...


class TaskArtifactSink:
    def __init__(self, client: TaskWorkerApiClient, *, worker_id: str) -> None:
        self._client = client
        self._worker_id = worker_id

    async def upload(
        self,
        *,
        task_id: str,
        run_id: str,
        artifact_type: ArtifactType,
        name: str,
        path: Path,
        size: int,
        mime_type: str,
    ) -> str:
        try:
            target = await self._client.request_artifact_upload_url(
                ArtifactUploadUrlRequest(
                    worker_id=self._worker_id,
                    task_id=task_id,
                    run_id=run_id,
                    name=name,
                    mime_type=mime_type,
                )
            )
            content = await asyncio.to_thread(path.read_bytes)
            await self._client.upload_signed_artifact(
                target.upload_url,
                content,
                content_type=mime_type,
            )
            await self._client.artifact(
                run_id,
                RunArtifactCreate(
                    type=artifact_type.value,
                    name=name,
                    storage_key=target.storage_key,
                    size=size,
                    mime_type=mime_type,
                ),
            )
            return target.storage_key
        except TaskApiError as exc:
            raise RpaRetryableError(
                "ARTIFACT_DELIVERY_FAILED",
                "Artifact could not be delivered",
            ) from exc


class ArtifactRecorder:
    def __init__(
        self,
        *,
        page: Any,
        task_id: str,
        run_id: str,
        run_directory: Path,
        sink: ArtifactSink,
        max_bytes: int,
    ) -> None:
        self._page = page
        self._task_id = task_id
        self._run_id = run_id
        self._root = run_directory.resolve()
        self._sink = sink
        self._max_bytes = max_bytes
        self._root.mkdir(parents=True, exist_ok=True)

    async def screenshot(
        self,
        name: str,
        *,
        full_page: bool = True,
        step_id: str | None = None,
    ) -> ArtifactRecord:
        filename = self._filename(name, ".png")
        path = self._root / "screenshots" / f"{uuid4().hex}-{filename}"
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._page.screenshot(path=str(path), full_page=full_page)
        return await self.record_file(
            path,
            artifact_type=ArtifactType.SCREENSHOT,
            name=filename,
            mime_type="image/png",
            step_id=step_id,
        )

    async def save_download(
        self,
        download: Any,
        name: str | None = None,
        *,
        step_id: str | None = None,
    ) -> ArtifactRecord:
        resolved_name = str(
            name or getattr(download, "suggested_filename", None) or "download"
        )
        filename = self._filename(resolved_name)
        path = self._root / "downloads" / f"{uuid4().hex}-{filename}"
        path.parent.mkdir(parents=True, exist_ok=True)
        await download.save_as(str(path))
        return await self.record_file(
            path,
            artifact_type=ArtifactType.DOWNLOAD,
            name=filename,
            step_id=step_id,
        )

    async def record_file(
        self,
        path: Path,
        *,
        artifact_type: ArtifactType,
        name: str,
        mime_type: str | None = None,
        step_id: str | None = None,
    ) -> ArtifactRecord:
        resolved, size = await asyncio.to_thread(self._inspect_file, path)
        if size > self._max_bytes:
            raise RpaFatalError(
                "ARTIFACT_TOO_LARGE",
                "Artifact exceeds the configured size limit",
            )
        resolved_mime = (
            mime_type
            or mimetypes.guess_type(name)[0]
            or "application/octet-stream"
        )
        checksum = await asyncio.to_thread(self._checksum, resolved)
        storage_key = await self._sink.upload(
            task_id=self._task_id,
            run_id=self._run_id,
            artifact_type=artifact_type,
            name=name,
            path=resolved,
            size=size,
            mime_type=resolved_mime,
        )
        return ArtifactRecord(
            type=artifact_type,
            name=name,
            storage_key=storage_key,
            size=size,
            mime_type=resolved_mime,
            checksum_sha256=checksum,
            step_id=step_id,
        )

    @staticmethod
    def _filename(value: str, default_suffix: str = "") -> str:
        leaf = Path(value).name.strip()
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", leaf).strip("._")
        if not safe:
            safe = "artifact"
        if default_suffix and not safe.lower().endswith(default_suffix):
            safe += default_suffix
        return safe[:200]

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _inspect_file(self, path: Path) -> tuple[Path, int]:
        resolved = path.resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise RpaFatalError(
                "ARTIFACT_PATH_INVALID",
                "Artifact path is outside the run directory",
            ) from exc
        return resolved, resolved.stat().st_size
