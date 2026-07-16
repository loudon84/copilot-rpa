# Postman

Import `RPA_Engine_Phase2.postman_collection.json` alongside the existing Task
collection. The Engine collection targets `http://127.0.0.1:4610` by default;
override its base URL variable when testing another environment.

Before `Upload Flow package`:

1. Build the ZIP from `examples/phase2-demo`.
2. Select the ZIP in Postman's `package` file field. Some Postman versions do
   not resolve a collection variable as a local file path.
3. Increment the manifest version before repeating an upload; versions cannot
   be overwritten.

The upload request stores `flow_id`, `flow_version`, and `flow_version_id` into
collection variables for subsequent requests.

Import `RPA_Engine_Phase3.postman_collection.json` for Worker observability and
the dedicated Task register/heartbeat smoke profile. It deliberately contains
no real lease request.

Phase 4 adds no direct Runtime HTTP endpoint, so it has no separate Postman
collection. Runtime is invoked only through the internal Worker
`RunCommandHandler`; local smoke instructions are in `docs/PHASE4_RUNTIME.md`.

Phase 5 also adds no Engine production endpoint. Its standalone Mock SRM page,
three-scenario browser harness, and uploadable Flow package are documented in
`docs/PHASE5_MOCK_SRM.md`.
