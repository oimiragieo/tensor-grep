# Cache Management Runbook

tensor-grep maintains internal caches to accelerate searches. This runbook explains how to inspect and clear them.

## Cache Location
Caches are typically stored in `.tg_cache/` at the project root.
- **AST Cache:** `.tg_cache/ast/project_data_v6.json`
- **Trigram Index:** `.tg_cache/trigrams/`

## Diagnosing Stale Caches
Run `tg doctor` to see the size, modification time, and staleness of the AST cache. If the cache is unusually large or out of sync with recent file changes, it may need to be rebuilt.

## Safely Invalidating Caches
To clear all caches and force a full rebuild on the next search:
```bash
rm -rf .tg_cache/
```

Windows PowerShell:
```powershell
Remove-Item -LiteralPath .tg_cache -Recurse -Force
```

## Rebuilding Indexes
After clearing the cache, you can pre-warm it by running a dummy search or using `tg calibrate`.
```bash
tg calibrate
```
