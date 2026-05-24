# 1.13.12 fix plan — items deferred from 1.13.11 dogfood

Three remaining items from the 1.13.11 brutal-honest dogfood pass. Items 1, 2, 3 already
landed in source on 2026-05-24 (see git diff for `src/tensor_grep/cli/mcp_server.py`,
`src/tensor_grep/cli/main.py`, `src/tensor_grep/cli/repo_map.py`). Items 5, 6, 7 deferred
because they require multi-file refactors with care for the load-bearing daemon-warm path
and LSP wiring that 1.13.1 and 1.13.8 just stabilized.

## Item 5 — Wire LSP evidence into agent-capsule confidence

**Problem.** `tg agent --query "…"` returns confidence 0.74 unchanged from 1.13.0 through
1.13.11, even though as of 1.13.8 LSP is healthy and providing extra reference evidence
for python/javascript/typescript/rust/php. The capsule ranker does not yet consult that
evidence.

**Files**

- `src/tensor_grep/cli/agent_capsule.py` — `_cap_primary_target_confidence` and
  surrounding helpers (lines 39–230). The capsule confidence cap currently maxes at 0.74
  for tied targets.
- `src/tensor_grep/cli/repo_map.py` — `build_agent_capsule_from_map` (search for
  `agent-context-capsule` routing reason) feeds the primary_target dict.
- `src/tensor_grep/cli/repo_map.py` — `_external_definitions` /
  `_external_references` already emit `lsp_proof: True` rows; that signal is the input.

**Proposed implementation**

1. Reuse the existing `agent_capsule.build_agent_capsule()` ->
   `repo_map.build_context_render()` -> `_build_edit_plan_seed()` path. The fix plan's
   `build_agent_capsule_from_map` name was stale.
2. Use the existing row-level `lsp_proof: True` + `lsp_provider_response: True` fields
   and the existing `lsp-confirmed` evidence label. Do not add a new `lsp_backed`
   schema field.
3. When `TG_CAPSULE_LSP_CONFIDENCE_BOOST=1`, LSP proof is present, and the provider
   language is one of the currently healthy LSP languages, raise the tie confidence cap
   from 0.74 -> 0.85. Cap stays at 0.85 (not 1.0) until we have a provider-agreement
   story, and lower trust caps still win.
4. When ambiguity exists (`tie_count > 1`) AND only the primary target has LSP proof,
   demote the non-LSP tied targets and clear the tie (resolve the
   `requires_confirmation` to False on the LSP-confirmed primary).

**Verification**

- New unit tests in `tests/unit/test_agent_capsule_lsp_confidence.py`:
  - LSP-confirmed primary on healthy language: confidence >= 0.80
  - LSP-confirmed primary on non-allowlisted language (e.g. go): no boost
  - Tie with one LSP-confirmed candidate: tie resolved, requires_confirmation False
  - LSP confidence boost does not override a lower trust cap
- Manual dogfood probe: `tg agent C:\dev\projects\agent-studio --query "where is the
  agent registry generated and how is it invoked" --json` — primary_target.confidence
  should rise above 0.74 once the primary is LSP-backed.

**Risk**

- Capsule confidence is consumed by downstream agents; raising the cap may auto-route
  edits that previously asked for human confirmation. Add a feature gate
  `TG_CAPSULE_LSP_CONFIDENCE_BOOST=1` to opt in for one release, then flip default in
  the release after.

---

## Item 6 — `--discover` checkpoint walk: 14.8 s → sub-2 s

**Problem.** `tg checkpoint list --discover` walks the entire `C:\dev\projects` tree
unbounded; on a real machine that's 14–18 s for 4 checkpoints. Default (non-discover)
mode is 487 ms because it only checks current scope.

**Files**

- `src/tensor_grep/cli/checkpoint_store.py` — discovery walker (search for `--discover`
  / `discover_child_checkpoints` / similar). This is where the unbounded walk lives.
- `rust_core/src/native_search.rs` and `rust_core/src/index.rs` — possibly the Rust
  walker if the Python side delegates. Confirm by reading the Python `--discover`
  implementation first.

**Proposed implementation**

1. **Add an index file** at `<root>/.tensor-grep/checkpoints/index.json` that records
   every active checkpoint's `(id, scope_path, created_at, files_count)`. Atomic write
   on `checkpoint create`; delete entry on `checkpoint undo` and `checkpoint prune`.
2. `tg checkpoint list --discover` reads the index first; if present and not stale,
   return its rows in <100 ms. If absent or older than 24h, fall back to the current
   walker AND write a fresh index.
3. **Bound the walker fallback** to:
   - max-depth 6 by default
   - skip `node_modules`, `.git`, `.venv`, `target`, `dist`, `.tensor-grep` (except for
     reading checkpoint dirs themselves), `.tmp*`, `.mypy_cache`, `.pytest_cache`,
     `.ruff_cache`
   - bail at 10,000 directories enumerated with a clear stderr "walk truncated; use
     --discover-full to override" message and a `truncated: True` field in JSON output
4. Add `--discover-full` for the legacy unbounded behavior.

**Verification**

- New test in `tests/unit/cli/test_checkpoint_discover_index.py`:
  - After 4 `checkpoint create` calls, `--discover` returns in <500 ms and finds all 4
  - After `checkpoint undo`, the restored checkpoint is removed from index
  - Index recreated on first `--discover` if missing
- Manual dogfood: `tg checkpoint list --discover` against current
  `C:\dev\projects` (4 known checkpoints) — expect <2 s wall.

**Risk**

- Index file consistency between concurrent `checkpoint create` calls — use atomic
  rename (write `.tmp` then `os.replace`). The session daemon also creates
  checkpoints; both writers must use the same locking discipline.

---

## Item 7 — Positional / flag argument unification

**Problem.** CLI conventions diverge across surfaces:

| Surface | First positional | Symbol pattern |
| --- | --- | --- |
| `tg search` | PATTERN (positional) | n/a |
| `tg defs / refs / callers` | PATH (positional) | `--symbol NAME` (required flag) |
| `tg blast-radius` | PATH | `--symbol NAME` |
| `tg agent` | PATH | `--query "..."` |
| `tg edit-plan` | PATH | `--query "..."` |
| `tg session edit-plan` | **SESSION_ID** | `--query "..."` (PATH is 2nd positional) |
| `tg run` | PATTERN_OR_PATH (positional, ambiguous) | `--pattern` flag alternative |

Agents fail this constantly. `tg defs SYMBOL PATH` (intuitive) errors; `tg session edit-plan PATH ...`
(intuitive after using non-session form) errors.

**Proposed implementation (breaking change requires deprecation cycle)**

**Target convention.** Across all symbol-bearing commands, accept BOTH:

- New form: `tg <verb> SYMBOL [PATH]` — symbol positional, path optional
- Legacy form: `tg <verb> --symbol NAME [PATH]` — emit a deprecation warning to stderr
  in 1.13.12 and 1.13.13, remove the `--symbol` form in 1.14.0

For session-cached commands:

- New form: `tg session <verb> [--session SESSION_ID] [PATH] --query "..."` —
  session-id becomes a flag (optional, auto-discovers most recent session on path)
- Legacy form: `tg session <verb> SESSION_ID PATH --query "..."` — deprecation warning

**Files**

- `src/tensor_grep/cli/commands.py` — every Click/Typer command definition. Search
  for `@command` decorators bound to `defs / refs / callers / blast-radius /
  blast-radius-render / blast-radius-plan / source / impact / session edit-plan /
  session context-render / session blast-radius`.
- `src/tensor_grep/cli/main.py` — the dispatch + help-text wiring.
- `tests/integration/cli/test_*.py` — every test that pins the current positional/flag
  shape.

**Verification**

- Each touched command needs:
  - Test for new form
  - Test for legacy form + assertion that stderr contains deprecation warning
  - Test for the conflict case (both `SYMBOL` positional AND `--symbol` flag → reject
    with a specific error)
- Help text updated for every touched command.
- `tg doctor` adds a `cli_conventions_version: "2"` field next to `schema_version`.

**Risk**

- **High.** Breaks any agent / script that pins the current form. Mitigation: two-release
  deprecation cycle, prominent stderr warning, doctor flag, and CHANGELOG notice.
- Tests will need a sweep — likely 50–100 test file touches. Consider scripting the
  legacy-form test additions via a codemod over the existing test files.

---

## Sequencing

Recommended landing order for 1.13.12:

1. Item 5 (capsule LSP confidence) — pure additive; feature-flagged. Low blast radius.
2. Item 6 (--discover indexing) — additive; new index file, walker bounded behind
   default but `--discover-full` escape hatch preserves existing behavior. Medium blast
   radius.
3. Item 7 (positional/flag) — defer to 1.14.0; needs the two-release deprecation cycle.
   Land the deprecation-warning surface in 1.13.12 (legacy form still works), and the
   removal in 1.14.0.

## Already-landed in source (uncommitted) on 2026-05-24

- `src/tensor_grep/cli/mcp_server.py` — pinned `serverInfo.version` to stable
  `"1.0.0"` contract constant; CLI version still available via
  `tg_mcp_capabilities` → `cli_version`.
- `src/tensor_grep/cli/main.py` — `_doctor_mcp_stdio_launcher_warning` now scans
  full `path_tg_candidates` for `.ps1` siblings, not just first-of-PATH.
- `src/tensor_grep/cli/repo_map.py` — `_definition_dedupe_key` normalizes
  `file://` URIs, resolves the path, and casefolds + slash-normalizes on Windows so
  LSP and native rows collapse correctly in `--provider hybrid`.

## Note

Item 4 from the 1.13.11 report ("LSP wired into defs/callers like refs") was a
false-positive on my end. defs/refs/callers all already consult
`_external_definitions` / `_external_references` symmetrically. The observed
1-def / 3-ref / 1-caller asymmetry on `AgentRegistryGenerator` was correct semantics
(one class declaration, two use sites, one constructor call). Withdrawn.

## Implementation ledger — 2026-05-24

- Verified and hardened already-landed items 1-3:
  - MCP initialize now reports stable `serverInfo.version = "1.0.0"`.
  - `tg doctor --json` reports `mcp_stdio_launcher_warning` when `.ps1` PATH
    candidates can trap MCP stdio clients.
  - Hybrid defs deduplicates native paths and LSP `file://` URIs, including encoded
    spaces.
- Item 5 implemented as feature-gated capsule confidence policy:
  - `TG_CAPSULE_LSP_CONFIDENCE_BOOST=1` makes `tg agent` request hybrid proof.
  - Existing `lsp_proof` + `lsp_provider_response` fields drive confidence; no new
    primary-target schema field was added.
  - LSP-resolved ties cap confidence at `0.85`; lower trust caps still win.
- Item 6 implemented by repairing the existing Python discovery cache/index path:
  - Cache/index writes are atomic.
  - Empty bounded discovery cache entries are valid.
  - Missing per-scope `index.json` files can be rebuilt from checkpoint metadata.
  - Bounded fallback skips additional generated roots and reports `truncated: true`
    when the directory cap is hit.
- Item 7 implemented as warnings only:
  - Legacy `--symbol` / `--query` forms still work and warn on stderr.
  - Positional forms remain quiet.
  - No `cli_conventions_version = "2"` was added because the full v2 CLI convention
    is not implemented yet.
