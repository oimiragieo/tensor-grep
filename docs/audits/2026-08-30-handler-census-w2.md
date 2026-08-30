# Handler census W2 — audit receipt (2026-08-30)

**Slice:** HANDLER-CENSUS-W2 (wave 1 — audit-only)  
**Base SHA:** `e6ba187faadd1a3cd5b1f8d5922bc220f0b544f6` (`origin/main` after DOCS-RECONCILE #1123)  
**Release class:** `docs:` (no publish)  
**Spend:** $0 (Tier-0 mechanical census; Fable/Codex deferred per A78)

---

## Executive summary

| Track | Measured | Ledger today | Wave 2 action |
|---|---:|---:|---|
| **ARCH-002** — `backends/` broad handlers | **47** | **0** rows | Append 47 disposition records + read each handler in context |
| **SEC-007** — MCP `str(exc)` on wire | **58** occurrences | Partial (MCP tool boundaries dispositioned in W1) | Replace raw exception strings with `_sanitized_tool_error` taxonomy |

Wave 1 delivers the closed-world population and build authorization only. **No product code** in this PR.

---

## ARCH-002 — backend broad-handler population

**Method:** AST walk of `src/tensor_grep/backends/*.py` for `except Exception` and bare `except`, identity key `(module, enclosing_symbol, handler_index_within_symbol)` matching `tests/unit/test_handler_dispositions.py`.

**Artifact:** `docs/audits/2026-08-30-handler-census-w2-backends.json` (47 handlers, forward-slash module paths).

**Per-file counts:**

| Module | Broad handlers |
|---|---:|
| `backends/cpu_backend.py` | 13 |
| `backends/cybert_backend.py` | 10 |
| `backends/cudf_backend.py` | 7 |
| `backends/torch_backend.py` | 5 |
| `backends/ripgrep_backend.py` | 4 |
| `backends/ast_wrapper_backend.py` | 3 |
| `backends/ast_backend.py` | 2 |
| `backends/rust_backend.py` | 2 |
| `backends/stringzilla_backend.py` | 1 |
| **Total** | **47** |

**Note:** Pass-5 backlog cited **50**; re-derived census is **47** (pass-5 likely counted `except Exception` substring hits including comments or a since-changed tree). **Trust this JSON**, not the earlier round number.

**Ledger gap:** `docs/audits/2026-08-20-handler-dispositions.json` has **130** rows, **0** with `module` under `backends/`. Backends were never in W1's `_ORIGINAL_EXCLUDED_MODULES`, so `test_handler_dispositions.py::test_ledger_completeness_scoped_to_audited_modules` does not yet require backend rows — but `test_silent_failure_hardening.py` **does** count all 47 toward the broad-handler population ceiling.

---

## SEC-007 — MCP raw `str(exc)` inventory

**Method:** `rg -c 'str(exc)' src/tensor_grep/cli/mcp_server.py` → **58** on base SHA.

**Disposition context:** W1 classified MCP **tool boundary** broad handlers as `INTENTIONAL-BOUNDARY` (fail-closed error envelope). SEC-007 is the **hygiene follow-up**: many envelopes still embed **`str(exc)`** verbatim rather than `_sanitized_tool_error` / exception-class-only messages.

**Wave 2 clusters (build order):**

1. **Invalid-input helpers** (~lines 854, 1506, 1582, …) — low path diversity; batch to `{code, message: exc.__class__.__name__}` pattern.
2. **Tool boundary `dumps(... str(exc))` arms** — already fail-closed; swap wire message to sanitized helper without changing exit shape.
3. **Agent capsule / rewrite surfaces** — verify against `tests/unit/test_w1a_mcp_handler_fail_closed.py` + add sanitize regression rows.

**Security gate:** A3 adversarial review required before merge (MCP wire surface).

---

## HYGIENE-FORMAT cross-check (premise retired same session)

Parallel probe falsified HYGIENE-001: all **15** frozen markdown paths pass `ruff format --check --preview --stdin-filename <path>` when fed **`git show origin/main:<path>`** blobs. Fresh Windows worktree checkout fails on **disk CRLF** only (`core.autocrlf=true`, `*.md` not in `.gitattributes eol=lf`). **Do not merge** a hollow format-only PR; optional follow-up is `*.md text eol=lf` (separate slice).

---

## Verification (wave 1)

```powershell
# Re-derive backend count
python -c "import json; print(json.load(open('docs/audits/2026-08-30-handler-census-w2-backends.json'))['backend_broad_handler_count'])"
# Expect: 47

# MCP str(exc) count
rg -c 'str(exc)' src/tensor_grep/cli/mcp_server.py
# Expect: 58

# Existing ledger gates still green
uv run --no-sync python -m pytest tests/unit/test_handler_dispositions.py -q
```

---

## Wave 2 authorization (not in this PR)

See `docs/plans/HANDLER-CENSUS-W2.md` § Wave 2 build. Requires:

- TDD: extend ledger JSON + `test_handler_dispositions` completeness to include `backends/*` modules (or add backends to audited set explicitly).
- Per-handler category assignment (`SILENT-SWALLOW` | `LOGGED-DEGRADE` | `INTENTIONAL-BOUNDARY`) with evidence — **never bulk-classify** (W1.4 ratchet trap).
- MCP sanitize PR(s) behind A3 gate.
- Optional: raise `test_silent_failure_hardening` ceiling only after ledger rows land (relocation pin discipline, A137).
