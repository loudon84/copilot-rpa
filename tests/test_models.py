from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)

import nodeskclaw_rpa_engine.db.models  # noqa: F401
from nodeskclaw_rpa_engine.db.base import Base

EXPECTED_COLUMN_COUNTS = {
    "rpa_browser_profiles": 13,
    "rpa_callback_outbox": 20,
    "rpa_cdp_endpoints": 13,
    "rpa_execution_attempts": 25,
    "rpa_flow_release_audits": 10,
    "rpa_flow_validation_runs": 13,
    "rpa_flow_versions": 20,
    "rpa_flows": 11,
    "rpa_worker_instances": 17,
}


def test_metadata_matches_existing_table_and_column_inventory() -> None:
    tables = {table.name: table for table in Base.metadata.tables.values()}

    assert set(tables) == set(EXPECTED_COLUMN_COUNTS)
    assert all(table.schema == "rpa_engine" for table in tables.values())
    assert {
        name: len(table.columns) for name, table in tables.items()
    } == EXPECTED_COLUMN_COUNTS
    assert sum(len(table.columns) for table in tables.values()) == 142


def test_metadata_matches_existing_constraint_and_index_inventory() -> None:
    tables = list(Base.metadata.tables.values())

    def count_constraints(constraint_type: type[object]) -> int:
        return sum(
            isinstance(constraint, constraint_type)
            for table in tables
            for constraint in table.constraints
        )

    assert count_constraints(PrimaryKeyConstraint) == 9
    assert count_constraints(ForeignKeyConstraint) == 7
    assert count_constraints(UniqueConstraint) == 7
    assert count_constraints(CheckConstraint) == 62
    assert sum(len(table.indexes) for table in tables) == 36

    primary_key_names = {
        table.primary_key.name for table in tables
    }
    assert primary_key_names == {
        f"{table_name}_pkey" for table_name in EXPECTED_COLUMN_COUNTS
    }


def test_all_foreign_keys_remain_inside_engine_schema() -> None:
    foreign_keys = [
        foreign_key.target_fullname
        for table in Base.metadata.tables.values()
        for foreign_key in table.foreign_keys
    ]

    assert len(foreign_keys) == 7
    assert all(target.startswith("rpa_engine.") for target in foreign_keys)
    assert not any(target.startswith("public.") for target in foreign_keys)


def test_selected_storage_and_snapshot_columns_are_frozen() -> None:
    versions = Base.metadata.tables["rpa_engine.rpa_flow_versions"]
    attempts = Base.metadata.tables["rpa_engine.rpa_execution_attempts"]
    outbox = Base.metadata.tables["rpa_engine.rpa_callback_outbox"]

    checksum_type = versions.c.package_checksum_sha256.type
    assert isinstance(checksum_type, String)
    assert checksum_type.length == 64
    assert versions.c.package_object_key.comment == (
        "Stable MinIO/S3 object key; never store a signed URL"
    )
    assert versions.c.package_bucket.nullable is True

    assert attempts.c.task_id.comment == (
        "External Task ID; no cross-Schema foreign key"
    )
    assert attempts.c.flow_version_id.nullable is False
    assert str(attempts.c.status.server_default.arg) == (
        "'RECEIVED'::character varying"
    )

    assert outbox.c.endpoint_path.comment == (
        "Relative Task API path only; never store credentials or signed URLs"
    )
    assert str(outbox.c.max_attempts.server_default.arg) == "10"
