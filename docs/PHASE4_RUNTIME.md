# Phase 4 Runtime Baseline

Phase 4 implements the internal `RunCommandHandler` used by the Worker Pool. It
does not add a direct run/debug HTTP endpoint and does not enable real lease
polling by default.

## Delivered modules

- `FlowLoader`: downloads the exact Registry package object, verifies SHA-256,
  reruns package validation, extracts atomically, and caches by
  `rpaFlowId/version/checksum`.
- `RunContext`: injects immutable copies of input, credentials, selectors, safe
  runtime configuration, managed Page, Artifact Recorder, logs, and events.
- `ManagedBrowserSessionManager`: owns Playwright, Chromium/Chrome/Edge,
  BrowserContext, Page, download directory, Trace, and deterministic cleanup.
- `ArtifactRecorder`: records screenshots, downloads, Trace, and logs under the
  run directory; checks path and size; hashes the file; then uses Task
  `/worker-api/artifacts/upload-url`, signed PUT, and run Artifact metadata
  callback.
- `ErrorHandler`: maps retryable, business, human-required, fatal, timeout, and
  unknown errors to retry, FAILED, or WAITING_HUMAN.
- `RpaRuntime`: composes loading, credentials, browser, context, retry, failure
  screenshot, Trace, events, artifacts, and terminal `RunResult`.

Worker Pool continues to own execution-attempt state and Task finish. Runtime
owns Flow execution and returns `SUCCESS`, `FAILED`, or `WAITING_HUMAN`.

## Configuration

```env
RUNTIME_ENABLED=false
RUNTIME_CACHE_DIR=runtime-cache/flows
RUNTIME_WORK_DIR=runtime-cache/runs
RUNTIME_TIMEOUT_SECONDS=900
RUNTIME_MAX_RETRIES=2
RUNTIME_RETRY_BACKOFF_SECONDS=1
RUNTIME_CLEANUP_ON_FINISH=true
RUNTIME_TRACE_MODE=ON_FAILURE
ARTIFACT_MAX_BYTES=209715200
```

`RUNTIME_ENABLED=true` requires the existing MinIO package storage settings.
It does not implicitly set `WORKER_LEASE_ENABLED=true`.

`RUNTIME_TRACE_MODE` supports:

- `OFF`: do not start tracing.
- `ON_FAILURE`: start tracing and upload only for a terminal failure or
  WAITING_HUMAN.
- `ALWAYS`: upload Trace for success and failure.

## Browser contract

Only `browserSession.mode=MANAGED` is enabled. Supported channels are
`chromium`, `chrome`, and `msedge`. MANAGED commands must use `ALWAYS` or
`CLOSE_ON_FINISH`; `profileRef` and `cdpEndpointRef` must be null.

Flow code receives only `ctx.page`. The safe `ctx.config.browserSession` omits
Profile and CDP references. Package validation rejects Playwright/database
imports, direct browser launch/CDP calls, and direct `open()` calls in every
Python file in the package.

Install the preferred bundled browser in a deployment environment:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

The development machine may also use installed Chrome with `channel=chrome`.

## Artifact delivery

```text
Flow -> ArtifactRecorder -> local run file
     -> Task POST /worker-api/artifacts/upload-url
     -> signed object-storage PUT
     -> Task worker-api/runs/{runId}/artifacts metadata
```

The upload-url request uses `worker_id`, `task_id`, `run_id`, `name`, and
`mime_type`. This route and request shape were confirmed by read-only
test-server OpenAPI inspection on 2026-07-16; the inspection did not upload an
Artifact or invoke the metadata callback.

Signed URLs are never persisted. Structured logging redacts common signed-query
credentials. Run files are deleted after browser cleanup when
`RUNTIME_CLEANUP_ON_FINISH=true`.

## Error mapping

| Exception | Result |
| --- | --- |
| `RpaRetryableError`, Playwright/Python timeout | Retry up to configured limit, then FAILED |
| `RpaBusinessError` | FAILED |
| `RpaHumanRequiredError` | WAITING_HUMAN |
| `RpaFatalError` | FAILED |
| Unknown exception | FAILED with safe `FLOW_UNHANDLED_ERROR` |

Terminal failure and WAITING_HUMAN attempt a best-effort failure screenshot.
Flow log/event payloads pass through the standard sensitive-field redactor.

## Current integration gates

- The 2026-07-16 read-only test-server OpenAPI inspection confirms the complete
  lease snapshot shape and `leaseExpiresAt` in both lease and renew contracts.
  No lease was requested, no dedicated real snapshot was inspected, and the
  real lease/renew/callback sequence has not yet run end to end.
- Real Task lease remains disabled until dedicated Task data is approved and
  the lease resolves to the exact active published Registry version. Selecting
  a latest version as a fallback is forbidden.
- The default credential resolver rejects non-null `credentialRef`. A strictly
  scoped `mock_env` resolver is available only for the development/test Mock SRM
  demonstration. Its credential reference, tenant, Portal account, and
  controlled Portal URL must match the dedicated lease; a governed credential
  service adapter is still required for real portal credentials.
- `config.portalUrl` is accepted only for controlled Mock Runtime commands.
  Production portal resolution from `portalAccountId` still needs a governed
  Task/Portal configuration adapter.
- Event and Artifact metadata callbacks are direct. Durable Callback Outbox
  dispatch is not yet wired into the Runtime callback path.
- Lease, renew, event, Artifact upload/metadata, and finish must pass a dedicated
  real end-to-end test before lease polling is enabled. Production
  service-account authentication remains a separate release gate.
- Python Flow modules execute in the Engine process. Static policy checks reduce
  accidental violations but are not an OS-level sandbox; process/container
  isolation remains a production-hardening decision.
- Whole-Flow retry may repeat external side effects. Phase 5 flows must keep
  actions idempotent; step-level retry is future work.
