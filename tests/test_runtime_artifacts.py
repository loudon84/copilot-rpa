from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nodeskclaw_rpa_engine.runtime.artifacts import (
    ArtifactRecorder,
    ArtifactType,
)
from nodeskclaw_rpa_engine.runtime.errors import RpaFatalError


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
