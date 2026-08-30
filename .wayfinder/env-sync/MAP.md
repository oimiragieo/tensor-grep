> **STALE (2026-08-30 closeout).** Version criterion `1.113.5` is false; live is **1.113.6**. Routing `68 passed` is a dated receipt, not the current governance count. ENV-001 CLOSED. See `backlog.md` Wayfinder Checks.

# ENV-SYNC — Answer key (wayfinder)

**Slice:** Local environment alignment after SEC-001 merge (`a77a150`).

## Done when (all must pass)

| Check | Command | Pass criterion |
|---|---|---|
| Lockfile sync | `uv sync --frozen --extra dev` | Exit 0; `pytest` importable via `uv run` |
| Version parity | `uv run tg --version` | Matches `pyproject.toml` / tag (`1.113.5`) |
| Doctor native | `uv run tg doctor --json` | `rust_binary_version_status` = `matches` |
| Routing parity ×2 | `uv run python -m pytest tests/e2e/test_routing_parity.py -q` | 68 passed both runs; 0 failed |
| No product code | `git diff --name-only` | No `src/` or `rust_core/` changes for this slice |

## Not in scope

- SEC-002+ security implementation
- Full 7445-test pytest matrix (CPU; defer to CI)
- `uv sync --frozen` without `--extra dev` (strips dev/test toolchain — documented trap)

## Evidence artifacts

- `.orchestrator/env-sync-routing-run1.txt`
- `.orchestrator/env-sync-routing-run2.txt`
- `docs/plans/ENV-SYNC.md`

## Next slice (after this MAP green)

**DOCS-RECONCILE** — stamp TASK_BOARD + SESSION_HANDOFF; fix stale citations (`docs:` PR).
