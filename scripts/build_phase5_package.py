from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.flows.package import FlowPackageValidator, PackageLimits

FLOW_ID = "rpa_flow_mock_srm_fetch_po"
FLOW_FILES = ("manifest.json", "selectors.json", "flow.py")
FLOW_VERSIONS = ("1.0.0", "1.1.0")
PACKAGE_TIMESTAMPS = {
    # 按字节保持已发布的 1.0.0 归档不变。
    "1.0.0": (2026, 7, 14, 16, 38, 22),
    # Git 不保留 mtime，因此全新检出后仍需使用稳定的 ZIP 时间戳。
    "1.1.0": (2026, 7, 16, 0, 0, 0),
}
PUBLISHED_CHECKSUMS = {
    "1.0.0": "1e3448d05bf47497876c8bca6e9f110dd2462de2c75ead6e761c0114a0206fd7",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Phase 5 demo Flow ZIP")
    parser.add_argument(
        "--version",
        choices=FLOW_VERSIONS,
        default="1.1.0",
        help="Flow version to package (default: 1.1.0)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Custom ZIP path (default: dist/<flow-id>-<version>.zip)",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "examples" / "mock-srm-flow" / args.version
    output = (
        args.output.resolve()
        if args.output is not None
        else root / "dist" / f"{FLOW_ID}-{args.version}.zip"
    )
    source_files = {name: source / name for name in FLOW_FILES}
    for source_file in source_files.values():
        if not source_file.is_file():
            raise FileNotFoundError(
                f"Flow {args.version} source file is missing: {source_file}"
            )
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, source_file in source_files.items():
            entry = zipfile.ZipInfo(name, date_time=PACKAGE_TIMESTAMPS[args.version])
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 0
            entry.external_attr = 0o100666 << 16
            archive.writestr(entry, source_file.read_bytes())

    settings = Settings(_env_file=None, app_env="test")
    validator = FlowPackageValidator(
        PackageLimits(
            max_bytes=settings.flow_package_max_bytes,
            max_uncompressed_bytes=settings.flow_package_max_uncompressed_bytes,
            max_files=settings.flow_package_max_files,
            max_compression_ratio=settings.flow_package_max_compression_ratio,
        )
    )
    package = validator.validate(output.name, output.read_bytes())
    published_checksum = PUBLISHED_CHECKSUMS.get(args.version)
    if published_checksum and package.checksum_sha256 != published_checksum:
        output.unlink(missing_ok=True)
        raise RuntimeError(
            f"Published Flow {args.version} package checksum changed; "
            "the immutable version cannot be rebuilt or uploaded"
        )
    print(f"package={output}")
    print(f"sha256={package.checksum_sha256}")
    print(f"size={package.size_bytes}")


if __name__ == "__main__":
    main()
