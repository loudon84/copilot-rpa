from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SQL = ROOT / "sql" / "0002_rpa_engine_initial_schema.sql"
REVISION_FILE = (
    ROOT
    / "migrations"
    / "versions"
    / "20260713_0001_existing_schema_baseline.py"
)


def _load_revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "existing_schema_baseline",
        REVISION_FILE,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_ddl_has_frozen_object_inventory() -> None:
    ddl = BASELINE_SQL.read_text(encoding="utf-8")

    assert len(re.findall(r"^CREATE TABLE ", ddl, re.MULTILINE)) == 9
    assert len(
        re.findall(r"^CREATE (?:UNIQUE )?INDEX ", ddl, re.MULTILINE)
    ) == 36
    assert len(
        re.findall(
            r"^CREATE OR REPLACE FUNCTION ", ddl, re.MULTILINE
        )
    ) == 4
    assert len(re.findall(r"^CREATE TRIGGER ", ddl, re.MULTILINE)) == 12
    assert "CREATE SCHEMA IF NOT EXISTS rpa_engine AUTHORIZATION task_user" in ddl


def test_baseline_contains_no_data_or_role_mutation() -> None:
    ddl = BASELINE_SQL.read_text(encoding="utf-8")

    assert re.search(r"^\s*INSERT\s", ddl, re.MULTILINE | re.IGNORECASE) is None
    assert re.search(r"^\s*COPY\s", ddl, re.MULTILINE | re.IGNORECASE) is None
    assert re.search(r"^\s*GRANT\s", ddl, re.MULTILINE | re.IGNORECASE) is None
    assert re.search(r"^\s*CREATE ROLE\s", ddl, re.MULTILINE | re.IGNORECASE) is None


def test_revision_is_dormant_baseline_for_the_existing_schema() -> None:
    revision = _load_revision()

    assert revision.revision == "20260713_0001"
    assert revision.down_revision is None
    assert len(revision._baseline_statements()) == 78
    assert "op.execute" in REVISION_FILE.read_text(encoding="utf-8")
