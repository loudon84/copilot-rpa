# NoDeskClaw RPA Engine

AutoTask product branch `v0.1` contains RPA Engine component version `0.5.0`.
The Engine provides configuration, structured logs, health/readiness endpoints,
the PostgreSQL and S3-compatible foundations, Flow Registry and versioned
package management, an internal Worker Pool, exact package loading, MANAGED
Playwright sessions, Artifact recording, and standardized error mapping.

The Engine owns Flow Registry metadata and Flow Packages. `nodeskclaw-task`
owns WorkflowBinding, business tasks, runs, events, Artifact metadata, and
HumanAction. A read-only test-server OpenAPI check on 2026-07-16 confirmed the
required lease/renew schema and Worker Artifact upload-url route. Real Task
lease polling remains disabled until dedicated test data, an exact published
Registry version, Mock scope/Portal configuration, and the full callback path
are approved and exercised end to end. The included deterministic Mock SRM
Flow covers SUCCESS, FAILED, and WAITING_HUMAN outcomes; WAITING_HUMAN uses the
type-A model and does not resume the original server browser session.

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

The Mock SRM service, demo Flow package, and three-scenario browser harness are
documented in [`docs/PHASE5_MOCK_SRM.md`](docs/PHASE5_MOCK_SRM.md).

The ordered test-server deployment, Flow publication, Task data setup, and
end-to-end acceptance checklist is in
[`docs/PHASE5_TEST_SERVER_HANDOFF.md`](docs/PHASE5_TEST_SERVER_HANDOFF.md).

Run all three Phase 5 scenarios locally with installed Chrome:

```powershell
.\.venv\Scripts\python.exe scripts\run_phase5_demo.py --start-mock-srm --channel chrome
```

## Quality checks

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
```

## External dependency policy

- PostgreSQL is disabled by default. When enabled, the URL must use
  `postgresql+asyncpg`, and each Engine connection sets its own search path to
  `rpa_engine,public`.
- The application never calls `create_all`, runs Alembic, creates schemas, or
  seeds data automatically.
- MinIO/S3 is disabled by default. When enabled, readiness checks the configured
  bucket; application startup never creates buckets.
- Database passwords, MinIO keys, and service-account secrets belong only in a
  local or deployed `.env`. They must not be committed or copied into logs.
- The current `TASK_AUTH_MODE=none` is a test-environment compatibility mode.
  Service-account token exchange is reserved for a later phase.
- Phase 2 Flow API uses `X-Actor-Id` and optional `X-Tenant-Id` only as trusted
  test-environment context. These headers are not production authentication.
- The Worker Pool and lease polling are disabled by default. Phase 3 live
  integration may enable registration/heartbeat only; lease polling requires a
  Phase 4 Runtime Handler and explicit approval for dedicated integration data.
- Credential resolution is disabled by default. `mock_env` is restricted to
  development/test, one credential reference, one tenant, and one Portal
  account. It is only for the dedicated Mock SRM demonstration; production
  requires a governed credential-service adapter.

## Current integration boundaries

- The 2026-07-16 read-only Task OpenAPI check confirms that
  `WorkerLeaseResponse` contains the immutable execution snapshot consumed by
  the Engine, renew returns `leaseExpiresAt`, and Worker Artifact upload URL is
  `POST /worker-api/artifacts/upload-url`. This is schema validation, not a
  successful register, heartbeat, lease, renew, or callback run.
- Task dispatch uses an HTTP lease/renew compatibility source. Production Queue
  ack, visibility timeout, retry, and dead-letter behavior remain future work.
- Event, Artifact, and finish callbacks are direct. The Callback Outbox schema
  exists, but durable dispatch is not yet wired into Runtime callbacks.
- Test actor headers and `TASK_AUTH_MODE=none` are not production
  authentication. Worker service-account authentication remains required.
- Python Flow modules currently run in the Engine process; static policy checks
  are not OS-level isolation.
- Lease polling stays off until a dedicated binding/run is approved, its exact
  active published Registry version and Mock credential/Portal scope are
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
