-- 休眠 DDL——禁止自动执行。
-- 目标：PostgreSQL 数据库 nodeskclaw_task，仅允许在 DBeaver 中手动执行。
-- 此 Phase 1 文件只创建隔离的 Engine Schema，不创建表、迁移记录、扩展、
-- 角色或种子数据。

BEGIN;

CREATE SCHEMA IF NOT EXISTS rpa_engine AUTHORIZATION task_user;

COMMENT ON SCHEMA rpa_engine IS
    'NoDeskClaw RPA Engine-owned objects; public schema remains Task-owned.';

COMMIT;
