from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.flows.package import FlowPackageValidator, PackageLimits


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Phase 5 demo Flow ZIP")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/rpa_flow_mock_srm_fetch_po-1.0.0.zip"),
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "examples" / "mock-srm-flow"
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "selectors.json", "flow.py"):
            archive.write(source / name, name)

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
    print(f"package={output}")
    print(f"sha256={package.checksum_sha256}")
    print(f"size={package.size_bytes}")


if __name__ == "__main__":
    main()
