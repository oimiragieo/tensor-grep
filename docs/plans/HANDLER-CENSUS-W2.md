# HANDLER-CENSUS-W2 — Audit + wave-2 build plan

| Field | Value |
|---|---|
| Status | **WAVE 1 SHIPPED** (audit receipt on branch) |
| Date | 2026-08-30 |
| Base SHA | `e6ba187faadd1a3cd5b1f8d5922bc220f0b544f6` |
| Depends on | None |
| Blocks | ARCH-002 ledger extension; SEC-007 MCP sanitize PRs |
| Release class | Wave 1: `docs:` · Wave 2: `fix:` per cluster |

---

## Objective

Close the measured gaps from pass-5 backlog:

- **ARCH-002:** **47** broad handlers in `backends/` with **0** ledger rows (re-derived; was cited as 50).
- **SEC-007:** **58** raw `str(exc)` occurrences on MCP wire in `mcp_server.py`.

---

## Wave 1 — audit-only (this slice)

**Deliverables:**

1. `docs/audits/2026-08-30-handler-census-w2.md` — receipt + verification commands
2. `docs/audits/2026-08-30-handler-census-w2-backends.json` — closed-world handler identity list

**Out of scope:** Editing `2026-08-20-handler-dispositions.json`, product code, ratchet ceiling changes.

**Plan audit (Tier-0 — 2026-08-30):**

| Gate | Verdict |
|---|---|
| Population mechanically re-derived | **PASS** (47 backends, 58 MCP hits) |
| No contradictory acceptance | **PASS** (audit-only) |
| Fable / Codex | **DEFERRED** (A78 quota) — Tier-0 **APPROVED** |

---

## Wave 2 — build (authorized, not started)

### Task A — Backend ledger extension (ARCH-002)

1. **RED:** Add test asserting every handler in `2026-08-30-handler-census-w2-backends.json` has a ledger row (new completeness arm or extend audited-module set).
2. **Read-in-context:** For each of 47 handlers, assign category + evidence paragraph (mirror W1 slice discipline — ~8–12 handlers per PR max).
3. **GREEN:** `test_handler_dispositions.py` + `test_silent_failure_hardening.py` green; bump ceiling only if net new handlers (not relocation).

**Suggested PR slices:**

| PR | Modules | Handlers |
|---|---|---:|
| W2-a | `cpu_backend.py`, `ripgrep_backend.py` | 17 |
| W2-b | `cybert_backend.py`, `cudf_backend.py`, `torch_backend.py` | 22 |
| W2-c | `ast_backend.py`, `ast_wrapper_backend.py`, `rust_backend.py`, `stringzilla_backend.py` | 8 |

### Task B — MCP sanitize (SEC-007)

1. **RED:** Test that tool error payloads never match raw exception message patterns for injected secrets.
2. **GREEN:** Route through `_sanitized_tool_error` (or equivalent) for all 58 sites; preserve fail-closed envelope shape.
3. **A3 gate** before merge.

---

## Verification

```powershell
uv run --no-sync python -m pytest tests/unit/test_handler_dispositions.py -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check --preview .
```

---

## Size limits (wave 2 build)

| Class | Limit | Notes |
|---|---|---|
| Contracts | ≤500 LOC | Ledger JSON append batches |
| Logic | ≤1500 LOC | MCP sanitize cluster |
| Tests | ≤2000 LOC | New disposition + sanitize tests |
