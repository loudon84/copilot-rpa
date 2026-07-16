# Phase 3 Worker Pool

Phase 3 adds an internal Worker Pool and a compatibility client for the current
`nodeskclaw-task` lease API. Worker registration and heartbeat may be enabled
without enabling lease polling. Runtime execution remains disabled until a
Phase 4 `RunCommandHandler` is injected.

## Safety defaults

```env
WORKER_ENABLED=false
WORKER_LEASE_ENABLED=false
```

- `WORKER_ENABLED=false`: no Task Worker registration, heartbeat, or lease call.
- `WORKER_ENABLED=true` and `WORKER_LEASE_ENABLED=false`: register and heartbeat
  only. This is the supported Phase 3 live-smoke profile.
- `WORKER_LEASE_ENABLED=true`: requires an injected Runtime
  `RunCommandHandler`; otherwise application construction fails before startup.
- The application does not create or migrate tables. Phase 3 reuses
  `rpa_worker_instances` and `rpa_execution_attempts`.
- Redis is not a Phase 3 dependency.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `WORKER_ID` | `server-worker-001` | Stable Task and Engine Worker ID |
| `WORKER_TYPE` | `SERVER_WORKER` | `SERVER_WORKER` or `LOCAL_AGENT` |
| `WORKER_DEVICE_NAME` | `nodeskclaw-rpa-engine` | Observable device name |
| `WORKER_CAPABILITIES` | MANAGED + screenshot/download demo capabilities | JSON string array |
| `WORKER_TAGS` | `[]` | Engine-internal JSON tag array |
| `WORKER_MAX_CONCURRENT_RUNS` | `1` | Local concurrency slots |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `15` | Task and Engine heartbeat period |
| `WORKER_POLL_INTERVAL_SECONDS` | `5` | Lease poll period when enabled |
| `WORKER_LEASE_RENEW_INTERVAL_SECONDS` | `20` | Lease renewal period |
| `WORKER_OFFLINE_THRESHOLD_SECONDS` | `45` | Read API stale-heartbeat threshold |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | `30` | Graceful drain wait |
| `TASK_API_TIMEOUT_SECONDS` | `10` | Per-request Task API timeout |

Worker mode requires `DATABASE_ENABLED=true`. A Worker must advertise
`PLAYWRIGHT_CDP` and `BROWSER_SESSION_MANAGED`; Flow-specific capabilities are
also checked before dispatch. The public demo configuration additionally
advertises uppercase `SCREENSHOT` and `DOWNLOAD` to match the Phase 5 manifest.

## Engine read APIs

All requests require the test-environment `X-Actor-Id` header.

```http
GET /api/v1/workers?status=ONLINE&capability=PLAYWRIGHT_CDP&limit=50&offset=0
GET /api/v1/workers/{workerId}
```

The response status is computed as `OFFLINE` when the stored ONLINE/BUSY
heartbeat is older than the configured threshold. Phase 3 exposes no
drain/resume mutation API.

## Required Task lease contract

### Test-server contract gap recorded on 2026-07-14

The current test-server `WorkerLeaseResponse` exposes only:

```text
taskId
runId
leaseId
workflowBindingId
portalAccountId
rpaFlowId
input
```

It is missing all Phase 3 execution-snapshot fields:

```text
tenantId
workflowTemplateId
workflowCode
rpaEngineType
rpaFlowVersion
credentialRef
config.browserSession.mode
config.browserSession.headless
config.browserSession.channel
config.browserSession.profileRef
config.browserSession.cdpEndpointRef
config.browserSession.closePolicy
leaseExpiresAt
```

Impact: register, heartbeat, readiness, and Worker observation can be integrated
with the test server, but real lease polling and Task-driven Runtime execution
remain blocked. The Engine must not infer the missing version by selecting a
latest Registry version. Phase 4 Runtime development therefore uses typed mock
commands until Task extends this response.

The existing lease fields remain required. Task must additionally return the
following fields before real lease polling can be enabled:

```json
{
  "tenantId": "tenant-1",
  "workflowTemplateId": "template-1",
  "workflowCode": "fetch_po",
  "rpaEngineType": "PLAYWRIGHT_CDP",
  "rpaFlowVersion": "1.0.0",
  "credentialRef": "credential-1",
  "config": {
    "browserSession": {
      "mode": "MANAGED",
      "headless": true,
      "channel": "chromium",
      "profileRef": null,
      "cdpEndpointRef": null,
      "closePolicy": "ALWAYS"
    }
  },
  "leaseExpiresAt": "2026-07-14T12:00:00Z"
}
```

Missing version, expiry, or browser-session fields cause a contract rejection.
The Engine resolves `rpaFlowId + rpaFlowVersion + tenantId` to one exact active,
published Registry version. It never substitutes the latest version.

The current test Task OpenAPI uses snake_case for Worker request bodies and
camelCase for `WorkerLeaseResponse`. The compatibility client preserves that
mixed wire contract; Engine public API responses remain camelCase.

## Attempt and shutdown behavior

- A new accepted lease is recorded as `dispatchMode=LEASE` and `LEASED`.
- Handler entry changes it to `RUNNING`; completion uses a terminal state.
- Duplicate `leaseId` values do not create or dispatch another attempt.
- A new `leaseId` for the same `runId` receives the next `attemptNo` under a
  PostgreSQL transaction advisory lock.
- Failed renewal permits the handler to continue only until the known lease
  expiry, then cancels it and records `ABANDONED`.
- Graceful stop records `DRAINING`, waits for active slots, then records
  `OFFLINE`.

Event and finish callbacks are direct in Phase 3. If a terminal callback fails,
the Engine preserves its terminal attempt but cannot yet guarantee delivery;
reliable Callback Outbox delivery is a Phase 4 responsibility.

## Live-smoke boundary

Use the dedicated ID `server-worker-phase3-smoke`, keep
`WORKER_LEASE_ENABLED=false`, and verify only:

1. Task register returns HTTP 200 with a successful Task envelope.
2. Task heartbeat returns HTTP 200 with a successful Task envelope.
3. Engine `rpa_worker_instances` becomes ONLINE and its heartbeat advances.
4. On shutdown, Engine internal status becomes OFFLINE.

Do not call the real Task lease endpoint during Phase 3 smoke testing.
