-- DORMANT DDL - DO NOT RUN AUTOMATICALLY.
-- Target: PostgreSQL database nodeskclaw_task, executed manually in DBeaver.
-- This Phase 1 file creates only the isolated Engine schema. It creates no tables,
-- migration rows, extensions, roles, or seed data.

BEGIN;

CREATE SCHEMA IF NOT EXISTS rpa_engine AUTHORIZATION task_user;

COMMENT ON SCHEMA rpa_engine IS
    'NoDeskClaw RPA Engine-owned objects; public schema remains Task-owned.';

COMMIT;
