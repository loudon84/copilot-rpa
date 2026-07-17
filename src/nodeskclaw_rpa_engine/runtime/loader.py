from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

from packaging.version import Version

from nodeskclaw_rpa_engine import __version__
from nodeskclaw_rpa_engine.core.config import Settings
from nodeskclaw_rpa_engine.flows.manifest import FlowManifest
from nodeskclaw_rpa_engine.flows.package import (
    FlowPackageValidator,
    PackageLimits,
    ValidatedPackage,
)
from nodeskclaw_rpa_engine.object_storage.base import ObjectStorageClient
from nodeskclaw_rpa_engine.runtime.errors import RpaFatalError
from nodeskclaw_rpa_engine.workers.schemas import ResolvedFlowVersion

if TYPE_CHECKING:
    from nodeskclaw_rpa_engine.runtime.context import RunContext


FlowEntrypoint = Callable[["RunContext"], Awaitable[Any]]


class FlowPackageSource(Protocol):
    async def fetch(self, flow: ResolvedFlowVersion) -> bytes: ...


class ObjectStorageFlowPackageSource:
    def __init__(self, storage: ObjectStorageClient) -> None:
        self._storage = storage

    async def fetch(self, flow: ResolvedFlowVersion) -> bytes:
        if not flow.package_object_key:
            raise RpaFatalError(
                "FLOW_PACKAGE_REFERENCE_MISSING",
                "Flow package object reference is missing",
            )
        try:
            return await self._storage.get_package(flow.package_object_key)
        except Exception as exc:
            raise RpaFatalError(
                "FLOW_PACKAGE_DOWNLOAD_FAILED",
                "Flow package could not be downloaded",
            ) from exc


@dataclass(frozen=True, slots=True)
class LoadedFlow:
    root: Path
    manifest: FlowManifest
    selectors: Mapping[str, Any]
    run: FlowEntrypoint


class FlowLoader:
    def __init__(
        self,
        settings: Settings,
        source: FlowPackageSource,
    ) -> None:
        self._cache_root = settings.runtime_cache_dir.resolve()
        self._source = source
        self._validator = FlowPackageValidator(
            PackageLimits(
                max_bytes=settings.flow_package_max_bytes,
                max_uncompressed_bytes=(
                    settings.flow_package_max_uncompressed_bytes
                ),
                max_files=settings.flow_package_max_files,
                max_compression_ratio=(
                    settings.flow_package_max_compression_ratio
                ),
            )
        )
        self._locks: dict[str, asyncio.Lock] = {}

    async def load(self, flow: ResolvedFlowVersion) -> LoadedFlow:
        cache_key = f"{flow.rpa_flow_id}:{flow.version}:{flow.package_checksum}"
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            root = await self._ensure_cached(flow)
        manifest, selectors, entrypoint = await asyncio.to_thread(
            self._load_from_directory,
            root,
        )
        return LoadedFlow(
            root=root,
            manifest=manifest,
            selectors=selectors,
            run=entrypoint,
        )

    async def _ensure_cached(self, flow: ResolvedFlowVersion) -> Path:
        try:
            target = self._target_directory(flow)
            cache_is_valid = await asyncio.to_thread(
                self._cache_is_valid,
                target,
                flow,
            )
        except OSError as exc:
            raise self._cache_error(exc) from exc
        if cache_is_valid:
            return target
        content = await self._source.fetch(flow)
        actual_checksum = hashlib.sha256(content).hexdigest()
        if actual_checksum != flow.package_checksum:
            raise RpaFatalError(
                "FLOW_PACKAGE_CHECKSUM_MISMATCH",
                "Flow package checksum does not match the Registry snapshot",
            )
        try:
            package = await asyncio.to_thread(
                self._validator.validate,
                "package.zip",
                content,
            )
        except Exception as exc:
            raise RpaFatalError(
                "FLOW_PACKAGE_VALIDATION_FAILED",
                "Flow package failed Runtime validation",
            ) from exc
        self._validate_identity(flow, package)
        try:
            await asyncio.to_thread(self._replace_cache, target, package)
        except OSError as exc:
            raise self._cache_error(exc) from exc
        return target

    def _target_directory(self, flow: ResolvedFlowVersion) -> Path:
        target = (
            self._cache_root
            / flow.rpa_flow_id
            / flow.version
            / flow.package_checksum
        ).resolve()
        try:
            target.relative_to(self._cache_root)
        except ValueError as exc:
            raise RpaFatalError(
                "FLOW_CACHE_PATH_INVALID",
                "Flow cache path is outside the configured cache root",
            ) from exc
        return target

    @staticmethod
    def _cache_is_valid(target: Path, flow: ResolvedFlowVersion) -> bool:
        archive = target / "package.zip"
        marker = target / ".ready"
        try:
            archive_mode = archive.stat().st_mode
            marker_mode = marker.stat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            return False
        if not stat.S_ISREG(archive_mode) or not stat.S_ISREG(marker_mode):
            return False
        if marker.read_text(encoding="utf-8").strip() != flow.package_checksum:
            return False
        return hashlib.sha256(archive.read_bytes()).hexdigest() == (
            flow.package_checksum
        )

    @staticmethod
    def _cache_error(error: OSError) -> RpaFatalError:
        if isinstance(error, PermissionError):
            return RpaFatalError(
                "FLOW_CACHE_ACCESS_DENIED",
                "Flow cache access was denied",
            )
        return RpaFatalError(
            "FLOW_CACHE_WRITE_FAILED",
            "Flow cache could not be written",
        )

    def _replace_cache(
        self,
        target: Path,
        package: ValidatedPackage,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=".flow-", dir=target.parent))
        try:
            archive_path = temp / "package.zip"
            archive_path.write_bytes(package.content)
            with zipfile.ZipFile(archive_path) as archive:
                for entry in archive.infolist():
                    destination = (temp / entry.filename).resolve()
                    destination.relative_to(temp.resolve())
                    if entry.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        archive.open(entry) as source,
                        destination.open("wb") as output,
                    ):
                        shutil.copyfileobj(source, output)
            (temp / ".ready").write_text(
                package.checksum_sha256,
                encoding="utf-8",
            )
            if target.exists():
                shutil.rmtree(target)
            os.replace(temp, target)
        finally:
            if temp.exists():
                shutil.rmtree(temp, ignore_errors=True)

    @staticmethod
    def _validate_identity(
        flow: ResolvedFlowVersion,
        package: ValidatedPackage,
    ) -> None:
        if (
            package.manifest.rpa_flow_id != flow.rpa_flow_id
            or package.manifest.version != flow.version
            or package.manifest.engine_type != flow.engine_type
        ):
            raise RpaFatalError(
                "FLOW_PACKAGE_IDENTITY_MISMATCH",
                "Flow package identity does not match the Registry snapshot",
            )
        minimum = package.manifest.minimum_engine_version
        if minimum is not None and Version(__version__) < Version(minimum):
            raise RpaFatalError(
                "ENGINE_VERSION_INCOMPATIBLE",
                "Flow package requires a newer Engine version",
            )

    @staticmethod
    def _load_from_directory(
        root: Path,
    ) -> tuple[FlowManifest, Mapping[str, Any], FlowEntrypoint]:
        try:
            manifest = FlowManifest.model_validate_json(
                (root / "manifest.json").read_text(encoding="utf-8")
            )
            selectors = FlowLoader._load_selectors(root / "selectors.json")
            module_name = f"_nodeskclaw_flow_{uuid4().hex}"
            spec = importlib.util.spec_from_file_location(
                module_name,
                root / "flow.py",
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Flow module spec is unavailable")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            finally:
                sys.modules.pop(module_name, None)
            entrypoint = getattr(module, "run", None)
            if entrypoint is None or not inspect.iscoroutinefunction(entrypoint):
                raise RuntimeError("Flow entrypoint is not async")
            return manifest, selectors, cast(FlowEntrypoint, entrypoint)
        except RpaFatalError:
            raise
        except OSError as exc:
            raise FlowLoader._cache_error(exc) from exc
        except Exception as exc:
            raise RpaFatalError(
                "FLOW_LOAD_FAILED",
                "Flow package could not be loaded",
            ) from exc

    @staticmethod
    def _load_selectors(path: Path) -> Mapping[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict) or not all(
            isinstance(key, str) for key in data
        ):
            raise ValueError("selectors.json must be an object")
        return data
