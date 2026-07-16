# Phase 4 Runtime Smoke Flow

This package exercises the Phase 4 contract without network access. Runtime
creates the browser and injects `ctx.page`; the Flow renders local HTML, reads a
selector, records a screenshot, and emits one event.

The Flow deliberately does not launch Playwright, connect to CDP, read files,
or call Task APIs.
