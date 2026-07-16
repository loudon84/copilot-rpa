from __future__ import annotations

import ast
import hashlib
import io
import json
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError

from nodeskclaw_rpa_engine.flows.errors import PackageValidationError
from nodeskclaw_rpa_engine.flows.manifest import FlowManifest


@dataclass(frozen=True, slots=True)
class PackageLimits:
    max_bytes: int
    max_uncompressed_bytes: int
    max_files: int
    max_compression_ratio: float


@dataclass(frozen=True, slots=True)
class ValidatedPackage:
    content: bytes
    manifest: FlowManifest
    checksum_sha256: str
    size_bytes: int
    checks: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


class FlowPackageValidator:
    def __init__(self, limits: PackageLimits) -> None:
        self._limits = limits

    def validate(self, filename: str | None, content: bytes) -> ValidatedPackage:
        issues: list[dict[str, str]] = []
        if not filename or not filename.lower().endswith(".zip"):
            issues.append(
                self._issue("PACKAGE_EXTENSION", "Package must be a .zip file")
            )
        if len(content) > self._limits.max_bytes:
            issues.append(
                self._issue(
                    "PACKAGE_TOO_LARGE",
                    f"Compressed package exceeds {self._limits.max_bytes} bytes",
                )
            )
        if issues:
            raise PackageValidationError(issues)

        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except (zipfile.BadZipFile, OSError):
            raise PackageValidationError(
                [self._issue("PACKAGE_NOT_ZIP", "Package is not a valid ZIP archive")]
            ) from None

        with archive:
            entries = archive.infolist()
            files = [item for item in entries if not item.is_dir()]
            self._validate_entries(entries, issues)
            names = {item.filename for item in files}
            for required in ("manifest.json", "flow.py"):
                if required not in names:
                    issues.append(
                        self._issue(
                            "PACKAGE_FILE_MISSING",
                            f"Required root file is missing: {required}",
                        )
                    )
            if issues:
                raise PackageValidationError(issues)

            manifest = self._read_manifest(archive, issues)
            self._validate_entrypoint(archive, issues)
            self._validate_runtime_policy(archive, issues)
            if issues or manifest is None:
                raise PackageValidationError(issues)

        return ValidatedPackage(
            content=content,
            manifest=manifest,
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            checks=[
                {"code": "ZIP_STRUCTURE", "status": "PASSED"},
                {"code": "MANIFEST_SCHEMA", "status": "PASSED"},
                {"code": "ENTRYPOINT_ASYNC", "status": "PASSED"},
                {"code": "RUNTIME_POLICY", "status": "PASSED"},
                {"code": "PACKAGE_SHA256", "status": "PASSED"},
            ],
            warnings=[],
        )

    def _validate_entries(
        self,
        entries: list[zipfile.ZipInfo],
        issues: list[dict[str, str]],
    ) -> None:
        files = [item for item in entries if not item.is_dir()]
        if len(files) > self._limits.max_files:
            issues.append(
                self._issue(
                    "PACKAGE_TOO_MANY_FILES",
                    f"Package contains more than {self._limits.max_files} files",
                )
            )

        total_size = 0
        total_compressed = 0
        normalized_names: set[str] = set()
        for item in entries:
            if not item.is_dir():
                total_size += item.file_size
                total_compressed += item.compress_size
            path = PurePosixPath(item.filename)
            invalid_path = (
                "\\" in item.filename
                or path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or ":" in path.parts[0]
            )
            if invalid_path:
                issues.append(
                    self._issue(
                        "PACKAGE_PATH_UNSAFE",
                        f"Unsafe archive path: {item.filename}",
                    )
                )
            if path.name.casefold() in {
                ".env",
                "credentials.json",
                "secrets.json",
            }:
                issues.append(
                    self._issue(
                        "PACKAGE_SENSITIVE_FILE",
                        f"Sensitive file name is forbidden: {item.filename}",
                    )
                )
            normalized = item.filename.casefold()
            if normalized in normalized_names:
                issues.append(
                    self._issue(
                        "PACKAGE_PATH_DUPLICATE",
                        f"Duplicate archive path: {item.filename}",
                    )
                )
            normalized_names.add(normalized)
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                issues.append(
                    self._issue(
                        "PACKAGE_SYMLINK_FORBIDDEN",
                        f"Symbolic links are forbidden: {item.filename}",
                    )
                )
            if item.flag_bits & 0x1:
                issues.append(
                    self._issue(
                        "PACKAGE_ENCRYPTED_FILE",
                        f"Encrypted archive entries are forbidden: {item.filename}",
                    )
                )

        if total_size > self._limits.max_uncompressed_bytes:
            issues.append(
                self._issue(
                    "PACKAGE_UNCOMPRESSED_TOO_LARGE",
                    "Uncompressed package size exceeds the configured limit",
                )
            )
        ratio = total_size / max(total_compressed, 1)
        if ratio > self._limits.max_compression_ratio:
            issues.append(
                self._issue(
                    "PACKAGE_COMPRESSION_RATIO",
                    "Package compression ratio exceeds the configured limit",
                )
            )

    def _read_manifest(
        self,
        archive: zipfile.ZipFile,
        issues: list[dict[str, str]],
    ) -> FlowManifest | None:
        try:
            raw = archive.read("manifest.json")
            if len(raw) > 1024 * 1024:
                issues.append(
                    self._issue(
                        "MANIFEST_TOO_LARGE",
                        "manifest.json exceeds 1 MiB",
                    )
                )
                return None
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("manifest root must be an object")
            return FlowManifest.model_validate(data)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            issues.append(
                self._issue("MANIFEST_INVALID", self._safe_validation_message(exc))
            )
            return None

    def _validate_entrypoint(
        self,
        archive: zipfile.ZipFile,
        issues: list[dict[str, str]],
    ) -> None:
        try:
            source = archive.read("flow.py").decode("utf-8")
            tree = ast.parse(source, filename="flow.py")
        except (UnicodeDecodeError, SyntaxError) as exc:
            issues.append(
                self._issue(
                    "ENTRYPOINT_INVALID",
                    f"flow.py cannot be parsed: {type(exc).__name__}",
                )
            )
            return

        run_functions = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "run"
        ]
        if not run_functions or not isinstance(run_functions[0], ast.AsyncFunctionDef):
            issues.append(
                self._issue(
                    "ENTRYPOINT_NOT_ASYNC",
                    "flow.py must define a top-level async def run(ctx)",
                )
            )
            return
        function = run_functions[0]
        positional = [*function.args.posonlyargs, *function.args.args]
        if not positional or positional[0].arg != "ctx":
            issues.append(
                self._issue(
                    "ENTRYPOINT_SIGNATURE",
                    "flow.py run entrypoint must accept ctx as its first argument",
                )
            )

    def _validate_runtime_policy(
        self,
        archive: zipfile.ZipFile,
        issues: list[dict[str, str]],
    ) -> None:
        forbidden_imports = {"asyncpg", "playwright", "psycopg", "sqlalchemy"}
        forbidden_calls = {
            "async_playwright",
            "connect_over_cdp",
            "launch",
            "launch_persistent_context",
            "open",
            "sync_playwright",
        }
        violations: set[str] = set()
        python_files = [
            entry
            for entry in archive.infolist()
            if not entry.is_dir() and entry.filename.endswith(".py")
        ]
        for entry in python_files:
            try:
                source = archive.read(entry).decode("utf-8")
                tree = ast.parse(source, filename=entry.filename)
            except (UnicodeDecodeError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", maxsplit=1)[0]
                        if root in forbidden_imports:
                            violations.add(f"{entry.filename}:import:{root}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", maxsplit=1)[0]
                    if root in forbidden_imports:
                        violations.add(f"{entry.filename}:import:{root}")
                elif isinstance(node, ast.Call):
                    name = None
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name in forbidden_calls:
                        violations.add(f"{entry.filename}:call:{name}")
        if violations:
            issues.append(
                self._issue(
                    "FLOW_RUNTIME_POLICY_VIOLATION",
                    "flow.py uses forbidden runtime operations: "
                    + ", ".join(sorted(violations)),
                )
            )

    @staticmethod
    def _safe_validation_message(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            fields = [
                ".".join(str(part) for part in item["loc"])
                for item in exc.errors()
            ]
            return "manifest.json schema errors: " + ", ".join(fields)
        return str(exc)

    @staticmethod
    def _issue(code: str, message: str) -> dict[str, str]:
        return {"code": code, "message": message}
