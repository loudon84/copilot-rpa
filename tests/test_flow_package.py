from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from nodeskclaw_rpa_engine.flows.errors import PackageValidationError
from nodeskclaw_rpa_engine.flows.package import (
    FlowPackageValidator,
    PackageLimits,
)


def package_bytes(
    *,
    manifest: dict[str, object] | None = None,
    flow_source: str = "async def run(ctx):\n    raise RuntimeError('not executed')\n",
    extra_files: dict[str, str] | None = None,
) -> bytes:
    resolved_manifest = manifest or {
        "rpaFlowId": "rpa_flow_test",
        "name": "Test Flow",
        "version": "1.0.0",
        "engineType": "PLAYWRIGHT_CDP",
        "entrypoint": "flow.py:run",
        "supportedWorkflowCodes": ["test_workflow"],
        "supportedPortalTypes": ["MOCK"],
        "inputSchema": [
            {"name": "record_id", "type": "string", "required": True}
        ],
        "capabilities": ["screenshot"],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(resolved_manifest))
        archive.writestr("flow.py", flow_source)
        for name, content in (extra_files or {}).items():
            archive.writestr(name, content)
    return output.getvalue()


@pytest.fixture
def validator() -> FlowPackageValidator:
    return FlowPackageValidator(
        PackageLimits(
            max_bytes=1024 * 1024,
            max_uncompressed_bytes=4 * 1024 * 1024,
            max_files=20,
            max_compression_ratio=100,
        )
    )


def test_valid_package_is_statically_validated_without_execution(
    validator: FlowPackageValidator,
) -> None:
    package = validator.validate("flow.zip", package_bytes())

    assert package.manifest.rpa_flow_id == "rpa_flow_test"
    assert package.manifest.entrypoint == "flow.py:run"
    assert len(package.checksum_sha256) == 64
    assert [item["code"] for item in package.checks] == [
        "ZIP_STRUCTURE",
        "MANIFEST_SCHEMA",
        "ENTRYPOINT_ASYNC",
        "RUNTIME_POLICY",
        "PACKAGE_SHA256",
    ]


def test_invalid_manifest_reports_safe_field_names(
    validator: FlowPackageValidator,
) -> None:
    invalid_manifest = {
        "rpaFlowId": "rpa_flow_test",
        "name": "Test Flow",
        "version": "latest",
        "engineType": "SELENIUM",
        "entrypoint": "main.py:start",
        "supportedWorkflowCodes": [],
    }

    with pytest.raises(PackageValidationError) as captured:
        validator.validate(
            "flow.zip",
            package_bytes(manifest=invalid_manifest),
        )

    assert captured.value.code == "FLOW_PACKAGE_INVALID"
    assert "manifest.json schema errors" in str(captured.value.details)
    assert "latest" not in captured.value.message


def test_entrypoint_must_be_async_and_accept_ctx(
    validator: FlowPackageValidator,
) -> None:
    with pytest.raises(PackageValidationError) as captured:
        validator.validate(
            "flow.zip",
            package_bytes(flow_source="def run(value):\n    return value\n"),
        )

    assert captured.value.details == [
        {
            "code": "ENTRYPOINT_NOT_ASYNC",
            "message": "flow.py must define a top-level async def run(ctx)",
        }
    ]


def test_zip_slip_path_is_rejected(validator: FlowPackageValidator) -> None:
    with pytest.raises(PackageValidationError) as captured:
        validator.validate(
            "flow.zip",
            package_bytes(extra_files={"../outside.txt": "forbidden"}),
        )

    assert any(
        item["code"] == "PACKAGE_PATH_UNSAFE"
        for item in captured.value.details
    )


def test_sensitive_file_names_are_rejected(
    validator: FlowPackageValidator,
) -> None:
    with pytest.raises(PackageValidationError) as captured:
        validator.validate(
            "flow.zip",
            package_bytes(extra_files={"config/.env": "PASSWORD=forbidden"}),
        )

    assert any(
        item["code"] == "PACKAGE_SENSITIVE_FILE"
        for item in captured.value.details
    )


@pytest.mark.parametrize(
    "flow_source",
    [
        "from playwright.async_api import async_playwright\n"
        "async def run(ctx):\n    return await async_playwright().start()\n",
        "async def run(ctx):\n    return await ctx.page.context.browser.launch()\n",
        "async def run(ctx):\n    return open('secret.txt').read()\n",
    ],
)
def test_runtime_policy_rejects_browser_launch_and_direct_file_access(
    validator: FlowPackageValidator,
    flow_source: str,
) -> None:
    with pytest.raises(PackageValidationError) as captured:
        validator.validate("flow.zip", package_bytes(flow_source=flow_source))

    assert any(
        item["code"] == "FLOW_RUNTIME_POLICY_VIOLATION"
        for item in captured.value.details
    )


def test_runtime_policy_scans_python_helper_modules(
    validator: FlowPackageValidator,
) -> None:
    with pytest.raises(PackageValidationError) as captured:
        validator.validate(
            "flow.zip",
            package_bytes(
                flow_source="async def run(ctx):\n    return None\n",
                extra_files={
                    "helper.py": (
                        "from playwright.async_api import async_playwright\n"
                    )
                },
            ),
        )

    assert any(
        item["code"] == "FLOW_RUNTIME_POLICY_VIOLATION"
        for item in captured.value.details
    )


def test_phase4_runtime_smoke_example_is_a_valid_package(
    validator: FlowPackageValidator,
) -> None:
    root = Path("examples/phase4-runtime-smoke")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "flow.py", "selectors.json"):
            archive.writestr(name, (root / name).read_bytes())

    package = validator.validate("phase4-runtime-smoke.zip", output.getvalue())

    assert package.manifest.rpa_flow_id == "rpa_flow_phase4_runtime_smoke"
    assert package.manifest.version == "0.1.0"


def test_compressed_size_limit_is_enforced() -> None:
    validator = FlowPackageValidator(
        PackageLimits(
            max_bytes=10,
            max_uncompressed_bytes=1024,
            max_files=20,
            max_compression_ratio=100,
        )
    )

    with pytest.raises(PackageValidationError) as captured:
        validator.validate("flow.zip", package_bytes())

    assert captured.value.details[0]["code"] == "PACKAGE_TOO_LARGE"
