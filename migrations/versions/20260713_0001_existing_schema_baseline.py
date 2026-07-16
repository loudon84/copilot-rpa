"""Baseline the existing rpa_engine schema.

Revision ID: 20260713_0001
Revises:
Create Date: 2026-07-13

The test database already contains these objects. Do not run ``upgrade`` there;
after an approved drift review, an administrator must stamp this revision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

_STATEMENT_SEPARATOR: Final = "-- statement-break"
_BASELINE_SQL = (
    Path(__file__).resolve().parents[2]
    / "sql"
    / "0002_rpa_engine_initial_schema.sql"
)


def _baseline_statements() -> list[str]:
    sql = _BASELINE_SQL.read_text(encoding="utf-8")
    return [
        statement.strip()
        for statement in sql.split(_STATEMENT_SEPARATOR)
        if statement.strip()
    ]


def upgrade() -> None:
    """Create the baseline only on a new, explicitly authorized database."""
    for statement in _baseline_statements():
        op.execute(sa.text(statement))


def downgrade() -> None:
    """Remove only objects owned by this baseline; retain the Engine schema."""
    for table_name in (
        "rpa_callback_outbox",
        "rpa_flow_validation_runs",
        "rpa_flow_release_audits",
        "rpa_execution_attempts",
        "rpa_flow_versions",
        "rpa_worker_instances",
        "rpa_flows",
        "rpa_cdp_endpoints",
        "rpa_browser_profiles",
    ):
        op.execute(sa.text(f"DROP TABLE rpa_engine.{table_name}"))

    for function_name in (
        "deny_audit_mutation",
        "guard_flow_version",
        "guard_execution_attempt",
        "set_updated_at",
    ):
        op.execute(
            sa.text(f"DROP FUNCTION rpa_engine.{function_name}()")
        )
