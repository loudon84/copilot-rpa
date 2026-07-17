from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPOSITORY_ROOT / "scripts" / "build_phase5_package.py"
PUBLISHED_1_0_0_CHECKSUM = (
    "1e3448d05bf47497876c8bca6e9f110dd2462de2c75ead6e761c0114a0206fd7"
)


def build(version: str, output: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--version",
            version,
            "--output",
            str(output),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return hashlib.sha256(output.read_bytes()).hexdigest()


def test_builder_preserves_published_1_0_0_package(tmp_path: Path) -> None:
    checksum = build("1.0.0", tmp_path / "phase5-1.0.0.zip")

    assert checksum == PUBLISHED_1_0_0_CHECKSUM


def test_builder_produces_reproducible_1_1_0_package(tmp_path: Path) -> None:
    first = build("1.1.0", tmp_path / "phase5-1.1.0-first.zip")
    second = build("1.1.0", tmp_path / "phase5-1.1.0-second.zip")

    assert first == second
