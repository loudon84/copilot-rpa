# Phase 2 upload example

Create the ZIP with the three package files at the archive root:

```powershell
Compress-Archive `
  -Path manifest.json,flow.py,selectors.json `
  -DestinationPath phase2-demo-0.1.0.zip
```

Choose the resulting ZIP in the Phase 2 Postman collection. Published versions
are immutable; increment `version` before uploading the example again.
