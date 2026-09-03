# HANDLER-CENSUS-W2-a — cpu_backend + ripgrep_backend ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Append disposition ledger rows for all **17** broad `except Exception` handlers in `backends/cpu_backend.py` (13) and `backends/ripgrep_backend.py` (4), and extend the disposition completeness gate so those modules are machine-required — without changing product handler bodies unless a SILENT-SWALLOW demands a same-PR harden.

**Architecture:** W1 used `_ORIGINAL_EXCLUDED_MODULES - _EXCLUDED_MODULES` as the audited set. Backends were never excluded, so they never entered that set. W2-a adds an explicit `_EXPLICIT_AUDITED_MODULES` frozenset (starts with the two backend modules) unioned into completeness / same-name-sibling scans. Ledger remains append-only JSON. Ceiling in `test_silent_failure_hardening.py` moves only if a handler is narrowed (A137 relocation vs growth).

**Tech Stack:** Python 3.11+, pytest, existing ledger schema (`category` ∈ SILENT-SWALLOW|LOGGED-DEGRADE|INTENTIONAL-BOUNDARY), AST identity `(module, enclosing_symbol, handler_index_within_symbol)`.

**Depends on:** Wave 1 census merged — PR #1124 / `ed740d0` on `origin/main`.

**Release class:** `docs:` if ledger+tests only; `fix:` only if a SILENT-SWALLOW is hardened in the same PR.

**Size limits:** ledger batch ≤500 LOC net; logic ≤1500 (expect ~0 product LOC); tests ≤2000.

---

## File map

| File | Role |
|---|---|
| `docs/audits/2026-08-20-handler-dispositions.json` | Append 17 records |
| `tests/unit/test_handler_dispositions.py` | Add `_EXPLICIT_AUDITED_MODULES`; union into `_audited_modules_so_far()` |
| `docs/plans/HANDLER-CENSUS-W2.md` | Status stamp wave2-a IN_FLIGHT |
| `docs/audits/2026-08-30-handler-census-w2.md` | Point W2-a at merge SHA when done |

**Do not edit:** `TOTAL_BROAD_HANDLERS_CEILING` unless a handler is narrowed; `backends/*.py` unless SILENT-SWALLOW fix is required.

---

### Task 1: RED — extend audited set without ledger rows

**Files:**
- Modify: `tests/unit/test_handler_dispositions.py`
- Test: same file

- [ ] **Step 1: Write the failing change**

Add near `_ORIGINAL_EXCLUDED_MODULES`:

```python
# Backend modules never lived in _ORIGINAL_EXCLUDED_MODULES (W1 carve-out was CLI-only).
# Completeness for backends is gated by this explicit set, grown only when a slice
# appends matching ledger rows (HANDLER-CENSUS-W2).
_EXPLICIT_AUDITED_MODULES = frozenset({
    "backends/cpu_backend.py",
    "backends/ripgrep_backend.py",
})
```

Change `_audited_modules_so_far` to:

```python
def _audited_modules_so_far() -> frozenset[str]:
    return (_ORIGINAL_EXCLUDED_MODULES - _current_excluded_modules()) | _EXPLICIT_AUDITED_MODULES
```

- [ ] **Step 2: Run RED**

```powershell
uv run --no-sync python -m pytest tests/unit/test_handler_dispositions.py::test_ledger_completeness_scoped_to_audited_modules -q
```

Expect FAIL listing 17 missing identities for cpu/ripgrep.

- [ ] **Step 3: Commit RED only if implementing in isolation** (optional; same PR may batch RED+GREEN)

---

### Task 2: GREEN — disposition all 17 handlers in context

**Files:**
- Modify: `docs/audits/2026-08-20-handler-dispositions.json`
- Read: `src/tensor_grep/backends/cpu_backend.py`, `src/tensor_grep/backends/ripgrep_backend.py`

Identities (from census JSON @ wave1; re-derive linenos via AST before writing — linenos are advisory):

| module | enclosing_symbol | idx | census lineno |
|---|---|---:|---:|
| backends/cpu_backend.py | search | 0–9 | 548…810 |
| backends/cpu_backend.py | _decode_line | 0 | 830 |
| backends/cpu_backend.py | _search_word_line_context_via_rust | 0 | 933 |
| backends/cpu_backend.py | _search_ltl | 0 | 1059 |
| backends/ripgrep_backend.py | supports_pcre2 | 0 | 77 |
| backends/ripgrep_backend.py | search | 0 | 203 |
| backends/ripgrep_backend.py | _search_files_with_matches | 0 | 356 |
| backends/ripgrep_backend.py | _search_counts | 0 | 475 |

- [ ] **Step 1:** For each identity, read the handler body + callers; assign category + distinct non-empty `evidence` and `reason`; set `hardened_in` null unless you harden.
- [ ] **Step 2:** Append records; keep JSON valid array; no duplicates of identity triples.
- [ ] **Step 3: Run GREEN**

```powershell
uv run --no-sync python -m pytest tests/unit/test_handler_dispositions.py -q
uv run --no-sync python -m pytest tests/unit/test_silent_failure_hardening.py -q
```

- [ ] **Step 4:** If any SILENT-SWALLOW requires product fix, harden minimally + RED behavioral test in same PR; else ledger-only.

---

### Task 3: Docs stamp + verify

- [ ] Update `docs/plans/HANDLER-CENSUS-W2.md` status: wave 2-a branch tip.
- [ ] `ruff check` on touched py; `ruff format --check --preview` on touched files.
- [ ] Exact-path commit (no `git add -A`).

---

## Plan audit log

| Seat | Verdict | Notes |
|---|---|---|
| Tier-0 orchestrator | **APPROVED** | Plan SHA256 `8D696321BFAC25227B48F36D48C32371FA7F7A27C485445F9EF2ABDD3A04AFE9` (post audit-table stamp); `_EXPLICIT_AUDITED_MODULES` correct; 17 ledger-only OK |
| Fable | **FAILED A78** | CLI/quota unavailable this session — not pending |
| Thinktank / Task audit seat | **APPROVED** (Tier-0 substitute) | Pairwise criteria OK; backends never in original exclude set |
| Codex Sol QA (pass 1) | **FIX-FIRST** | `cpu_backend.py` search idx 3/7 (`:692`/`:764` pre-harden) silently turned regex engine failures into clean no-match under `except Exception: pass` while ledger said INTENTIONAL-BOUNDARY |
| Product harden | applied | Decode-only handlers; `regex_str.search` outside try/except; RED `test_python_fallback_regex_engine_failure_is_not_clean_no_match`; ledger idxs 2/3/6/7 `hardened_in=HANDLER-CENSUS-W2-a` |
| Codex Sol QA (pass 2) | PENDING after harden | Re-check exact tip bytes for AUDIT_CLEAR |

## Acceptance

1. `test_ledger_completeness_scoped_to_audited_modules` green with both backend modules in `_EXPLICIT_AUDITED_MODULES`.
2. Exactly 17 new ledger rows for those modules; locatability + vocabulary + evidence gates green.
3. No unexplained ceiling bump.
4. Mutation control: deleting one new ledger row fails completeness.
