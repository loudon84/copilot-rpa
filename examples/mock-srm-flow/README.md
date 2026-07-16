# Phase 5 Mock SRM Flow

This is the versioned `1.0.0` Flow package for the deterministic Phase 5 demo.
The Runtime owns the browser and injects `ctx.page`, credentials, selectors,
events, and Artifact APIs. The Flow does not launch a browser, read secrets
from disk, call Task APIs, or access a database.

## Scenarios

| `po_no` | Expected result | Evidence |
| --- | --- | --- |
| `PO-20260708-001` | `SUCCESS` | Result screenshot and contract PDF |
| `PO-NOT-FOUND` | `FAILED / BUSINESS_NOT_FOUND` | Not-found and Runtime failure screenshots |
| `PO-MANUAL-001` | `WAITING_HUMAN / HUMAN_VERIFICATION_REQUIRED` | Human-check and Runtime failure screenshots |

The test credential resolver must provide non-empty `username` and `password`
values for a dedicated Mock credential reference. Credentials are never part
of the package, task input, logs, or screenshots.

Build a ZIP accepted by the Flow upload API:

```powershell
.\.venv\Scripts\python.exe scripts\build_phase5_package.py
```

The generated file is `dist/rpa_flow_mock_srm_fetch_po-1.0.0.zip`. The `dist`
directory is ignored by Git.
