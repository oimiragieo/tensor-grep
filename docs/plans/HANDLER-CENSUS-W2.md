# HANDLER-CENSUS-W2 — Audit + wave-2 build plan

| Field | Value |
|---|---|
| Status | **ALL WAVES COMPLETE (ARCH-002 closed)**. |
| Date | 2026-08-30 |
| Base SHA | `ed740d0` (origin/main after Wave 1 / PR #1124) |
| Depends on | Wave 1 census merged |
| Blocks | SEC-007 MCP sanitize PRs |
| Release class | Wave 2-a: `docs:` (ledger+tests; `fix:` only if SILENT-SWALLOW hardened) |

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

## Wave 2 — build (authorized)

### Task A — Backend ledger extension (ARCH-002)

**W2-a status:** SHIPPED — `_EXPLICIT_AUDITED_MODULES` + 17 ledger rows for `cpu_backend.py` / `ripgrep_backend.py`; Sol pass-1 FIX-FIRST harden applied (decode/search separation on cpu search cascade idxs 2/3/6/7).

**W2-c status:** COMPLETE — 8/8 handlers ledgered (`ast_backend.py`×2, `ast_wrapper_backend.py`×3, `rust_backend.py`×2, `stringzilla_backend.py`×1) and enrolled in `_EXPLICIT_AUDITED_MODULES`. Categories: 7 INTENTIONAL-BOUNDARY + 1 LOGGED-DEGRADE (`rust_backend.search` idx 0 passthrough fallback_reason). No product handler bodies changed; `hardened_in: null` on all eight.

**W2-b status:** COMPLETE — 21/21 handlers ledgered (`cudf_backend.py`×7, `torch_backend.py`×5, `cybert_backend.py`×9) and enrolled in `_EXPLICIT_AUDITED_MODULES`. Hardened: `cybert_backend.deobfuscate_payload` narrowed to `(ValueError, binascii.Error)` (ceiling 267→266); cudf RMM/CuPy + cybert telemetry arms disclosed as LOGGED-DEGRADE (`hardened_in: HANDLER-CENSUS-W2-b`).

1. **RED:** Add `_EXPLICIT_AUDITED_MODULES` (backends never lived in `_ORIGINAL_EXCLUDED_MODULES`); union into `_audited_modules_so_far()`.
2. **Read-in-context:** For each of 46 broad handlers (47 census minus 1 narrowed in W2-b), assign category + evidence paragraph (mirror W1 slice discipline — ~8–12 handlers per PR max).
3. **GREEN:** `test_handler_dispositions.py` + `test_silent_failure_hardening.py` green; bump ceiling only if a handler is narrowed (A137).

**Suggested PR slices:**

| PR | Modules | Handlers | Status |
|---|---|---:|---|
| W2-a | `cpu_backend.py`, `ripgrep_backend.py` | 17 | SHIPPED |
| W2-b | `cybert_backend.py`, `cudf_backend.py`, `torch_backend.py` | 21 | COMPLETE |
| W2-c | `ast_backend.py`, `ast_wrapper_backend.py`, `rust_backend.py`, `stringzilla_backend.py` | 8 | SHIPPED |

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
