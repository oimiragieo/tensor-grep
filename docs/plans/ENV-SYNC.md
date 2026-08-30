> **SUPERSEDED / CLOSED (2026-08-30 closeout).** Live `tg --version` is **1.113.6** (criterion `1.113.5` is stale). Routing "68 passed" is a dated receipt, not the later governance slice count (120). ENV-001 is CLOSED. Do not treat this plan or `.wayfinder/env-sync/MAP.md` as live facts.

# ENV-SYNC — Local environment alignment

| Field | Value |
|---|---|
| Status | **SHIPPED** (verification-only slice) |
| Date | 2026-08-30 |
| Base SHA | `a77a150` (post SEC-001 #1122) |
| Wayfinder | `.wayfinder/env-sync/MAP.md` |

## Objective

Clear P0 environment false-greens (ENV-001..003) by syncing the canonical Windows venv and re-measuring doctor + routing parity. No product code changes.

## Seam claims (verified against tree)

| Claim | Evidence |
|---|---|
| Project version | `pyproject.toml` → `1.113.5` |
| Dev deps are optional extra | `pyproject.toml:640-660` → `[project.optional-dependencies] dev = [...]` |
| Doctor JSON fields | `tg doctor --json` emits `version`, `rust_binary_version_status`, `search_acceleration_backend` |
| Routing parity suite | `tests/e2e/test_routing_parity.py` — 68 tests collected (2026-08-30) |

## Trap (discovered this slice)

**`uv sync --frozen` without `--extra dev` uninstalls pytest/ruff/mypy and tree-sitter grammars.** For this repo, canonical dev sync is:

```powershell
uv sync --frozen --extra dev
```

## Execution log

| Step | Result |
|---|---|
| `uv sync --frozen` (no extra) | Removed 41 dev packages — **wrong command for dev work** |
| `uv sync --frozen --extra dev` | Restored dev toolchain |
| `uv run tg doctor --json` | `version=1.113.5`, `rust_binary_version_status=matches`, `search_acceleration_backend=standalone-native-tg` |
| Routing parity run 1 | **68 passed** in 72.0s → `.orchestrator/env-sync-routing-run1.txt` |
| Routing parity run 2 | **68 passed** in 72.9s → `.orchestrator/env-sync-routing-run2.txt` |

## Disposition

| ID | Before | After |
|---|---|---|
| ENV-001 | venv behind tag | **CLOSED** — 1.113.5 aligned |
| ENV-002 | doctor `mismatch` | **CLOSED** — `matches` |
| ENV-003 | flaky 45/46 under full suite | **MONITOR** — 68/68 ×2 isolated; prior flake not reproduced |

## Next slice

**DOCS-RECONCILE** (ranked #3 in `backlog.md`).
