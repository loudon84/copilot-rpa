# Phase 5: Mock SRM Validation Flow

Phase 5 provides a deterministic browser target and one versioned RPA Flow for
the three required terminal outcomes. It does not add an Engine production API,
change a database table, or enable Task lease polling.

## Components

- `nodeskclaw_rpa_engine.mock_srm`: standalone local Mock SRM FastAPI service.
- `examples/mock-srm-flow`: publishable Flow package source, version `1.0.0`.
- `MockSrmAdapter`: Flow-local Portal Adapter for login, PO search, contract
  download, evidence, and CAPTCHA/MFA detection.
- `scripts/build_phase5_package.py`: deterministic package builder and standard
  package-policy validation.
- `scripts/run_phase5_demo.py`: local full Runtime + MANAGED browser harness.

The Mock service is separate from the Engine API. Starting the Engine on port
`4610` never starts or exposes the Mock portal.

## Scenario contract

| Input `po_no` | Runtime result | Error code | Evidence |
| --- | --- | --- | --- |
| `PO-20260708-001` | `SUCCESS` | none | PO result screenshot and contract PDF |
| `PO-NOT-FOUND` | `FAILED` | `BUSINESS_NOT_FOUND` | Not-found and Runtime failure screenshots, Trace |
| `PO-MANUAL-001` | `WAITING_HUMAN` | `HUMAN_VERIFICATION_REQUIRED` | Human-check and Runtime failure screenshots, Trace |

WAITING_HUMAN follows the frozen type-A model: the server browser closes after
capturing evidence. An operator handles the task separately; the original
Playwright context is not resumed.

## Run the standalone portal

```powershell
.\.venv\Scripts\python.exe -m nodeskclaw_rpa_engine.mock_srm
```

Default endpoints:

```text
GET http://127.0.0.1:4600/health/live
GET http://127.0.0.1:4600/
GET http://127.0.0.1:4600/contracts/PO-20260708-001.pdf
```

`MOCK_SRM_HOST`, `MOCK_SRM_PORT`, and `MOCK_SRM_LOG_LEVEL` may be supplied as
process environment variables. Keep the service on loopback unless a controlled
test network explicitly requires remote browser access.

The login form accepts any non-empty synthetic credential. It performs no
network authentication and does not store the entered values. The Flow still
requires credentials through Runtime's `credentialRef` resolver so the demo
does not teach an unsafe task-input or package-secret pattern.

For a dedicated development/test integration, configure the scoped resolver in
the process environment (never in a committed file):

```env
CREDENTIAL_RESOLVER_MODE=mock_env
MOCK_SRM_CREDENTIAL_REF=<dedicated-reference>
MOCK_SRM_USERNAME=<synthetic-user>
MOCK_SRM_PASSWORD=<synthetic-password>
MOCK_SRM_ALLOWED_TENANT_ID=<dedicated-tenant>
MOCK_SRM_ALLOWED_PORTAL_ACCOUNT_ID=<dedicated-portal-account>
```

All three scope values must match the Task lease exactly. `mock_env` is rejected
in staging and production. It is not a substitute for the governed production
credential-service adapter.

## Run the complete local demo

The harness starts the Mock portal, builds the package in memory, executes the
real FlowLoader and RpaRuntime, starts a real MANAGED browser, captures local
evidence, verifies all three terminal states, and stops the portal:

```powershell
.\.venv\Scripts\python.exe scripts\run_phase5_demo.py `
  --start-mock-srm `
  --channel chrome
```

Use `--channel chromium` after installing the Playwright browser. Add
`--headful` to show the browser. Limit execution with one of:

```text
--scenario success
--scenario failed
--scenario waiting_human
```

The harness deliberately uses in-memory package delivery, a scoped synthetic
credential resolver, local Artifact copies, and local event capture. It does
not access PostgreSQL, MinIO, or Task APIs. Evidence is written under the
Git-ignored `runtime-cache/phase5-demo/artifacts` directory.

## Build the uploadable package

```powershell
.\.venv\Scripts\python.exe scripts\build_phase5_package.py
```

Output:

```text
dist/rpa_flow_mock_srm_fetch_po-1.0.0.zip
```

The builder prints the SHA256 and validates the ZIP using the same policy as
the Flow upload API. The package contains only `manifest.json`, `selectors.json`,
and `flow.py`; it contains no credentials or environment configuration.

## Central AutoTask integration hold points

Local Phase 5 browser execution is complete. The central Task -> Worker ->
Runtime -> Artifact -> finish demonstration remains gated by controlled
integration work. A read-only test-server OpenAPI inspection on 2026-07-16
confirmed that `WorkerLeaseResponse` includes the required execution snapshot,
including `config.portalUrl`, `config.browserSession`, and `leaseExpiresAt`;
renew also returns `leaseExpiresAt`. The Worker Artifact upload-url operation is
`POST /worker-api/artifacts/upload-url` with `worker_id`, `task_id`, `run_id`,
`name`, and `mime_type`.

The schema check did not request a lease, inspect a dedicated real snapshot, or
execute callbacks. The remaining gates are:

- Dedicated Task binding/run data must be approved for this demonstration.
- The lease must resolve to the exact active published Registry version for
  `rpaFlowId + rpaFlowVersion + tenantId`; latest-version fallback is forbidden.
- The Engine `mock_env` resolver may resolve one dedicated Mock credentialRef in
  development/test. Its tenant and Portal-account scope must match, and Task
  must supply the controlled Mock Portal URL in the lease snapshot. Production
  still requires a governed credential/Portal adapter.
- Real lease, renew, event, Artifact upload/metadata, and finish callbacks must
  pass end to end.
- Durable Callback Outbox delivery is still pending; current callbacks are
  direct best-effort calls.
- Production Worker service-account authentication is still pending.

Keep `WORKER_LEASE_ENABLED=false` until those items are complete and a dedicated
test binding/run is approved.
