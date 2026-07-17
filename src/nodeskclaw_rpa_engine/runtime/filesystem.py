from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4


class RuntimeFilesystemProbe:
    """检查 Runtime 缓存目录和工作目录是否可正常读写。"""

    def __init__(self, cache_dir: Path, work_dir: Path) -> None:
        self._directories = (cache_dir, work_dir)

    async def check(self) -> None:
        await asyncio.to_thread(self._check_directories)

    def _check_directories(self) -> None:
        for directory in self._directories:
            self._check_directory(directory)

    @staticmethod
    def _check_directory(directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        probe_file = directory / f".readiness-{uuid4().hex}.tmp"
        payload = b"nodeskclaw-rpa-engine-runtime-readiness"

        try:
            with probe_file.open("xb") as stream:
                stream.write(payload)
            if probe_file.read_bytes() != payload:
                raise OSError("Runtime readiness probe content mismatch")
        finally:
            probe_file.unlink(missing_ok=True)

        if probe_file.exists():
            raise OSError("Runtime readiness probe cleanup failed")
