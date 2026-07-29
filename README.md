# NoDeskClaw RPA Engine

English | [简体中文](README.zh-CN.md)

AutoTask product branch `v0.1` contains RPA Engine component version `0.6.0`.
The Engine provides configuration, structured logs, health/readiness endpoints,
the PostgreSQL and S3-compatible foundations, Flow Registry and versioned
package management, an internal Worker Pool, exact package loading, MANAGED
Playwright sessions, Artifact recording, and standardized error mapping.

The Engine owns Flow Registry metadata and Flow Packages. `nodeskclaw-task`
owns WorkflowBinding, business tasks, runs, events, Artifact metadata, and
HumanAction. A read-only test-server OpenAPI check on 2026-07-16 confirmed the
required lease/renew schema and Worker Artifact upload-url route. Real Task
lease polling remains disabled until dedicated test data, an exact published
Registry version, scoped test credentials/Portal configuration, and the full callback path
are approved and exercised end to end. Flow `1.0.0` is the deterministic local
Mock SRM baseline covering SUCCESS, FAILED, and WAITING_HUMAN. Flow `1.1.0`
uses a configured supplier portal and performs login, order lookup, detail-page
navigation, and XLSX download. WAITING_HUMAN uses the type-A model and does not
resume the original server browser session.

Starting with `0.6.0`, a successful `flow.py:run(ctx)` may return a JSON object.
The Runtime validates JSON encoding, prohibited sensitive keys, and the
`RUNTIME_OUTPUT_MAX_BYTES` limit before forwarding the value only in the
SUCCESS finish callback through the existing Callback Outbox. Flows returning
`None` remain compatible, and output-validation failures are not retried.

## Requirements

- Python 3.12
- Windows PowerShell for the commands below
- No PostgreSQL or MinIO service is required for the disabled-dependency profile

## Local setup

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m nodeskclaw_rpa_engine
```

If Python is installed elsewhere, use `py -3.12 -m venv .venv` for the first
command.

The service listens on `127.0.0.1:4610` by default:

```text
GET http://127.0.0.1:4610/health/live
GET http://127.0.0.1:4610/health/ready
GET http://127.0.0.1:4610/docs
```

Flow Registry requests and examples are documented in
[`docs/PHASE2_API.md`](docs/PHASE2_API.md). Deployment addresses must be supplied
through environment configuration and are never committed to this repository.

Worker configuration, Task lease contract, and live-smoke boundaries are in
[`docs/PHASE3_WORKER.md`](docs/PHASE3_WORKER.md).

Runtime, browser, Artifact, and error behavior are documented in
[`docs/PHASE4_RUNTIME.md`](docs/PHASE4_RUNTIME.md).

The versioned local Mock and configured supplier-portal Flows are documented in
[`docs/PHASE5_MOCK_SRM.md`](docs/PHASE5_MOCK_SRM.md).

The ordered test-server deployment, Flow publication, Task data setup, and
end-to-end acceptance checklist is in
[`docs/PHASE5_TEST_SERVER_HANDOFF.md`](docs/PHASE5_TEST_SERVER_HANDOFF.md).

Run all three Phase 5 scenarios locally with installed Chrome:

```powershell
.\.venv\Scripts\python.exe scripts\run_phase5_demo.py --start-mock-srm --channel chrome
```

Build the current supplier-portal package (`1.1.0` is the default):

```powershell
.\.venv\Scripts\python.exe scripts\build_phase5_package.py --version 1.1.0
```

Run its local browser smoke test with the portal URL and credentials supplied
only through local environment variables:

```powershell
$env:SUPPLIER_PORTAL_URL = "<supplier-portal-url>"
$env:SUPPLIER_PORTAL_USERNAME = "<username>"
$env:SUPPLIER_PORTAL_PASSWORD = "<password>"
.\.venv\Scripts\python.exe scripts\run_supplier_portal_demo.py `
  --po-no POJS2606030010 `
  --channel chrome
```

The currently observed portal download is a fixed XLSX file, not a PDF and not
a per-order document. The smoke test records the requested order number but
does not claim that the fixed download is unique to that order.

## Quality checks

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pip check
```

## External dependency policy

- PostgreSQL is disabled by default. When enabled, the URL must use
  `postgresql+asyncpg`, and each Engine connection sets its own search path to
  `rpa_engine,public`.
- The application never calls `create_all`, runs Alembic, creates schemas, or
  seeds data automatically.
- MinIO/S3 is disabled by default. When enabled, readiness checks the configured
  bucket; application startup never creates buckets.
- Database passwords, MinIO keys, and service-account secrets must be supplied
  through a local `.env`, deployment environment variables, or managed secret
  injection. They must not be committed or copied into logs.
- The current `TASK_AUTH_MODE=none` is a test-environment compatibility mode.
  Service-account token exchange is reserved for a later phase.
- Phase 2 Flow API uses `X-Actor-Id` and optional `X-Tenant-Id` only as trusted
  test-environment context. These headers are not production authentication.
- The Worker Pool and lease polling are disabled by default. Phase 3 live
  integration may enable registration/heartbeat only; lease polling requires a
  Phase 4 Runtime Handler and explicit approval for dedicated integration data.
- Credential resolution is disabled by default. `mock_env` is restricted to
  development/test, one credential reference, one tenant, and one Portal
  account. It is only for controlled Phase 5 demonstrations; production
  requires a governed credential-service adapter.

## Current integration boundaries

- The 2026-07-16 read-only Task OpenAPI check confirms that
  `WorkerLeaseResponse` contains the immutable execution snapshot consumed by
  the Engine, renew returns `leaseExpiresAt`, and Worker Artifact upload URL is
  `POST /worker-api/artifacts/upload-url`. This is schema validation, not a
  successful register, heartbeat, lease, renew, or callback run.
- Task dispatch uses an HTTP lease/renew compatibility source. Production Queue
  ack, visibility timeout, retry, and dead-letter behavior remain future work.
- For an accepted execution attempt, EVENT and FINISH callbacks are persisted
  in the existing `rpa_callback_outbox` table and dispatched in the background
  with at-least-once retry and a stable `Idempotency-Key`. Artifact upload and
  metadata callbacks remain direct. A rejection that occurs before an attempt
  exists uses a direct best-effort callback as the only fallback.
- Task must persist and deduplicate every callback `Idempotency-Key`. Future
  Task hardening must also use `leaseId` to reject a stale FINISH callback from
  an older attempt.
- Test actor headers and `TASK_AUTH_MODE=none` are not production
  authentication. Worker service-account authentication remains required.
- Python Flow modules currently run in the Engine process; static policy checks
  are not OS-level isolation.
- Lease polling stays off until a dedicated binding/run is approved, its exact
  active published Registry version and scoped test credential/Portal data are
  prepared, and lease, renew, Artifact/event/finish callbacks complete a real
  end-to-end test. Production authentication also remains required.

## Database hold point

The ORM baseline defines nine Engine-owned tables in the `rpa_engine` schema,
with 142 columns, seven internal foreign keys, four trigger functions, and
twelve triggers.

[`sql/0002_rpa_engine_initial_schema.sql`](sql/0002_rpa_engine_initial_schema.sql)
and Alembic revision `20260713_0001` are operator-controlled baseline artifacts.
An existing schema must be checked for drift before an administrator decides
whether to stamp it; an approved empty schema may use the baseline upgrade.
Application startup and tests never stamp, migrate, execute DDL, or seed data.
