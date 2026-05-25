# Fix Plan: v1.13.15 Dogfood Contracts

Date: 2026-05-25

Goal: repair the v1.13.14 dogfood contract failures without broad refactors or
new positioning claims. This plan is intentionally PR-sized but covers the
release blockers that affect agent trust, rg compatibility, and agent-loop
resource bounds.

## Scope

### P0: Search and MCP Count Contracts

1. Add failing tests:
   - Native rg-format no-path files-with-matches output should match direct rg
     (`AGENTS.md`, not `.\AGENTS.md`) for both `tg search --format rg ...`
     and root-option forwarding (`tg --format rg ...`).
   - `RipgrepBackend` should count only ripgrep JSON `match` events as
     `total_matches`; context events may remain rendered rows but must not
     inflate match totals.
   - MCP `tg_search(..., context=1)` should render context rows without
     inflating the header count, and a CLI/MCP fixture should agree on actual
     match count.
   - Native `tg search --json -C 1 PATTERN PATH` should remain tensor-grep
     aggregate JSON, with `total_matches` counting true matches rather than
     context rows; `--format rg --json` remains raw rg JSON Lines.
   - Broad generated-root refusal for `--hidden --no-ignore` should explicitly
     mention `--allow-broad-generated-scan`, exit 2, and make clear this is a
     safety refusal rather than a zero-match result.
2. Fix:
   - Preserve implicit-path vs explicit-`.` through native rg passthrough with
     an explicit `path_was_implicit` / equivalent request field. Use it only
     when building rg passthrough argv; keep `primary_path()` as `.` for native,
     index, and GPU internal routing.
   - Adjust Python `RipgrepBackend.search` to track match-event count and
     matched files separately from context rows.
   - Improve generated-root refusal text.

### P0: LSP Proof Consistency

1. Add failing tests in semantic provider navigation coverage:
   - Hybrid refs/callers duplicate native+LSP rows should preserve the
     marker-backed LSP row.
   - Top-level `lsp_proof` should be computed from final emitted rows, not
     intermediate external rows, after merge, dedupe, and definition-location
     filtering.
   - Fallback/alias-only rows with `lsp-*` provenance but no
     `lsp_provider_response` must not count as proof.
   - Successful workspace-symbol, definition, and references requests should
     mark cached provider status as response-backed, while fallback/alias rows
     must not.
   - Pyright SRE/ABI mismatch stderr should remain visible as bounded provider
     warning/noise even when proof is true.
2. Fix:
   - Prefer `_is_lsp_proof_row()` rows in hybrid merge.
   - Compute `lsp_count`, `native_count`, `provider_agreement`,
     `lsp_evidence_status`, and top-level `lsp_proof` from final `references`
     / `callers`.
   - Mark cached provider status as response-backed after successful usable
     navigation responses. Keep proof true while surfacing bounded SRE/ABI
     stderr as provider warnings rather than suppressing it behind proof.

### P1: Agent Output Bounds and Daemon Observability

1. Add failing tests:
   - `build_repo_map` / `tg map --json` should skip generated local dirs such as
     `.venv_cuda`, `bench_data`, `gpu_bench_data`, `.tmp_*`, `many_files`,
     `group2_many_files`, and `site`, across `files`, `tests`, `symbols`,
     `imports`, `related_paths`, and session snapshots.
   - `tg map --json`, text `tg map`, MCP `tg_repo_map`, CLI
     `tg session open --json`, and MCP `tg_session_open` should default to the
     existing 512-file agent scan cap and emit `scan_limit`; explicit
     `--max-repo-files` / MCP `max_repo_files` overrides it.
   - Session refresh and `--refresh-on-stale` rebuilds should preserve the
     stored effective scan cap unless an explicit override is added.
   - `tg session daemon status --json` should expose response-cache stats when
     the daemon is alive, including direct-root status, discovered-root status,
     and stats-fetch failure fallback.
2. Fix:
   - Widen repo-map generated directory exclusions.
   - Default agent-facing CLI and MCP map/session-open surfaces to the existing
     agent-loop scan cap without changing the low-level `build_repo_map()`
     default globally.
   - Persist the effective session `max_repo_files` / `scan_limit` and reuse it
     for refreshes.
   - Merge daemon `stats` fields into daemon `status`.

### P1: Agent JSON Headline Fields

1. Add failing tests:
   - `edit-plan --json` exposes top-level `primary_target`, `edit_order`, and a
     compact `plan` object when existing nested data identifies a target, across
     CLI, MCP, and session surfaces.
   - `blast-radius --json` exposes non-empty `affected_files` and a non-null
     deterministic `blast_radius_score` when radius files exist, across CLI and
     MCP surfaces.
   - Existing nested `edit_plan_seed`, `navigation_pack`, `files`,
     `file_matches`, and `caller_tree` payloads remain unchanged except for
     additive aliases.
2. Fix:
   - Add compatibility aliases derived from `navigation_pack`,
     `edit_plan_seed`, `candidate_edit_targets`, `files`, and `file_matches`.
     Define `primary_target == navigation_pack.primary_target`; derive
     `edit_order` from `edit_plan_seed.edit_ordering`; keep `plan` compact,
     source-free metadata.
   - Define `affected_files` after output limiting from ranked files/file
     matches, and make `blast_radius_score` deterministic, documented, and
     bounded.
   - Keep nested fields unchanged.

### P1: Help and UX Accuracy

1. Add failing tests:
   - Native root help descriptions for passthrough commands are non-empty.
   - Root examples no longer recommend deprecated `--query` / `--symbol`.
   - `context-render --help`, `agent --help`, `edit-plan --help`, and
     navigation help hide deprecated forms while still accepting them.
   - `tg run --help` includes PowerShell single-quote guidance for `$` AST
     captures.
   - `checkpoint undo <existing PATH>` returns an actionable hint to use
     `tg checkpoint undo --last PATH` instead of a generic checkpoint-not-found
     error; no path-first shortcut ships in this release.
2. Fix:
   - Add native passthrough command doc comments.
   - Update Python root examples and command help strings.
   - Set deprecated Typer options to hidden where Typer supports it.
   - Improve checkpoint undo help/error text only.

### P2: Low-Risk Contract Polish

1. Add MCP capabilities latency regression coverage only if local reproduction
   shows a slow path.
2. Keep parser-backed definition confidence, duplicate launcher cleanup, and any
   path-first checkpoint shortcut deferred.

## Out of Scope

- Full JSON schema publication/validator CLI.
- Persistent daemon query cache and implicit daemon routing.
- AST fuzzy matching and pattern-from-file.
- Launcher pruning/repair beyond existing doctor diagnostics.
- GPU production promotion or new GPU performance claims.

## Local Gates

Run before push:

```powershell
uv run ruff format --check --preview .
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
cargo fmt --manifest-path rust_core/Cargo.toml --check
cargo clippy --manifest-path rust_core/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust_core/Cargo.toml
uv run python scripts/agent_readiness.py
git diff --check
```

If the Rust suite is too long for iteration, run targeted Rust tests while
developing, then the full Rust and Python gates before PR push.

## Release Process

1. Push branch `codex/v1-13-15-dogfood-contracts`.
2. Open a PR with a `fix:` title so semantic-release increments to v1.13.15
   after merge.
3. Do not bypass CI. Fix PR CI failures in-branch.
4. Squash-merge only after PR checks pass.
5. Watch main CI through release asset publication, PyPI publication, and
   publish-success-gate. Also check main CodeQL / dependency graph runs.
6. Verify release assets and public install:

```powershell
gh release view v1.13.15 --json tagName,assets
uvx --refresh-package tensor-grep --from tensor-grep==1.13.15 tg --version
tg upgrade
cmd /c tg --version
pwsh -NoProfile -Command "tg --version"
python -c "import subprocess; print(subprocess.run(['tg','--version'], capture_output=True, text=True).stdout.strip())"
tg doctor --json
```
