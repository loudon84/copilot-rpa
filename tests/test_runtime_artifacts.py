from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from nodeskclaw_rpa_engine.runtime.artifacts import (
    ArtifactRecorder,
    ArtifactType,
    TaskArtifactSink,
)
from nodeskclaw_rpa_engine.runtime.errors import RpaFatalError
from nodeskclaw_rpa_engine.workers.schemas import ArtifactUploadTarget
from nodeskclaw_rpa_engine.workers.task_client import TaskWorkerApiClient


class FakePage:
    async def screenshot(self, *, path: str, full_page: bool) -> None:
        assert full_page is True
        await asyncio.to_thread(Path(path).write_bytes, b"png-data")


class FakeDownload:
    suggested_filename = "contract 01.pdf"

    async def save_as(self, path: str) -> None:
        await asyncio.to_thread(Path(path).write_bytes, b"pdf-data")


class RecordingSink:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    async def upload(self, **kwargs) -> str:
        self.items.append(kwargs)
        return f"artifacts/{kwargs['run_id']}/{kwargs['name']}"


async def test_task_artifact_sink_uses_stable_worker_id(tmp_path) -> None:
    client = AsyncMock(spec=TaskWorkerApiClient)
    client.request_artifact_upload_url.return_value = ArtifactUploadTarget(
        upload_url="http://storage.test/upload",
        storage_key="artifacts/run-1/evidence.png",
    )
    sink = TaskArtifactSink(
        cast(TaskWorkerApiClient, client),
        worker_id="worker-stable",
    )
    path = tmp_path / "evidence.png"
    path.write_bytes(b"image")

    storage_key = await sink.upload(
        task_id="task-1",
        run_id="run-1",
        artifact_type=ArtifactType.SCREENSHOT,
        name="evidence.png",
        path=path,
        size=5,
        mime_type="image/png",
    )

    upload_request = client.request_artifact_upload_url.await_args.args[0]
    assert upload_request.model_dump(mode="json", by_alias=False) == {
        "worker_id": "worker-stable",
        "task_id": "task-1",
        "run_id": "run-1",
        "name": "evidence.png",
        "mime_type": "image/png",
    }
    assert storage_key == "artifacts/run-1/evidence.png"


async def test_artifact_recorder_uploads_screenshot_and_download(tmp_path) -> None:
    sink = RecordingSink()
    recorder = ArtifactRecorder(
        page=FakePage(),
        task_id="task-1",
        run_id="run-1",
        run_directory=tmp_path,
        sink=sink,
        max_bytes=1024,
    )

    screenshot = await recorder.screenshot("PO result")
    download = await recorder.save_download(FakeDownload())

    assert screenshot.type is ArtifactType.SCREENSHOT
    assert screenshot.name == "PO_result.png"
    assert screenshot.size == len(b"png-data")
    assert download.type is ArtifactType.DOWNLOAD
    assert download.name == "contract_01.pdf"
    assert len(screenshot.checksum_sha256) == 64
    assert [item["artifact_type"] for item in sink.items] == [
        ArtifactType.SCREENSHOT,
        ArtifactType.DOWNLOAD,
    ]


async def test_artifact_recorder_rejects_outside_path(tmp_path) -> None:
    sink = RecordingSink()
    recorder = ArtifactRecorder(
        page=FakePage(),
        task_id="task-1",
        run_id="run-1",
        run_directory=tmp_path / "run",
        sink=sink,
        max_bytes=1024,
    )
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")

    with pytest.raises(RpaFatalError) as captured:
        await recorder.record_file(
            outside,
            artifact_type=ArtifactType.LOG,
            name="outside.txt",
        )

    assert captured.value.code == "ARTIFACT_PATH_INVALID"


async def test_artifact_recorder_enforces_size_limit(tmp_path) -> None:
    sink = RecordingSink()
    recorder = ArtifactRecorder(
        page=FakePage(),
        task_id="task-1",
        run_id="run-1",
        run_directory=tmp_path,
        sink=sink,
        max_bytes=4,
    )
    path = tmp_path / "large.log"
    path.write_bytes(b"too-large")

    with pytest.raises(RpaFatalError) as captured:
        await recorder.record_file(
            path,
            artifact_type=ArtifactType.LOG,
            name="large.log",
        )

    assert captured.value.code == "ARTIFACT_TOO_LARGE"
