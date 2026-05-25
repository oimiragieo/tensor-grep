# Fix Plan: v1.13.16 Dogfood Follow-ups

Date: 2026-05-25

Goal: close the remaining v1.13.15 dogfood regressions without weakening the
agent-safety contracts that were intentionally added in earlier releases.

## Researched Contracts

- Ripgrep `--no-ignore` disables ignore-file filtering, but hidden and binary
  filtering are separate controls; broad generated-root scans remain a
  tensor-grep safety policy, not an rg parity promise for unbounded agent scans.
- MCP `tools/call` results should be stable and predictable for the same tool
  arguments, so MCP `tg_search` must use the same search scope/backend semantics
  as the CLI aggregate search contract.
- Bounded caches should expose scope and counters clearly; Python cache
  documentation also emphasizes measuring hits/misses and bounding retained
  values.

## Thinktank Review

The independent review accepted the narrow plan with one correction: do not
auto-route top-level `tg context-render` through a running daemon in a patch
release. That would silently change freshness and session semantics. Instead,
make daemon status and docs explicit that `response_cache_*` counters apply only
to daemon-routed session `context-render` and `edit-plan` requests.

## Scope

### P0: MCP Search Count Parity

1. Add a failing unit test proving MCP `tg_search` with `RipgrepBackend` calls
   one aggregate `search(root, pattern, config)` and does not enumerate files
   itself.
2. Fix MCP `tg_search` to keep per-file `DirectoryScanner` behavior for
   non-ripgrep backends, but let `RipgrepBackend` own directory walking,
   `.git/info/exclude`, ignore, binary, glob, type, context, and count
   semantics.

### P0: Generated-root No-ignore Diagnostics

1. Preserve the existing exit-2 refusal for unbounded generated-root
   `--no-ignore` scans unless the caller bounds the scan or passes
   `--allow-broad-generated-scan`.
2. Add native front-door regression coverage requiring stderr to say the refusal
   is a safety guard, not a zero-match result.
3. Preserve small non-generated `--no-ignore` behavior through existing search
   coverage.

### P1: Daemon Cache Scope Honesty

1. Add `response_cache_scope` to daemon start/status/stats JSON.
2. Print the same scope in human `tg session daemon status` output.
3. Update README, harness docs, and the tensor-grep skill to state that
   `response_cache_*` counters apply to daemon-routed session
   `context-render` / `edit-plan` requests, not plain top-level
   `tg context-render`.

## Out of Scope

- Making unbounded generated-root `--no-ignore` behave exactly like `rg`.
- Implicit daemon routing for top-level `context-render`, `edit-plan`, or
  `agent`.
- GPU promotion, raw search speed claims, AST fuzzy matching, and LSP provider
  installer changes.

## Local Gates

```powershell
uv run pytest tests/unit/test_mcp_server.py tests/unit/test_session_cli.py tests/unit/test_cli_modes.py -q
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml --test test_public_native_cli_parity
uv run ruff format --check --preview .
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
C:/Users/oimir/.cargo/bin/cargo.exe fmt --manifest-path rust_core/Cargo.toml --check
C:/Users/oimir/.cargo/bin/cargo.exe clippy --manifest-path rust_core/Cargo.toml --all-targets -- -D warnings
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml
git diff --check
```

## Release Process

1. Push branch `codex/v1-13-16-dogfood-followups`.
2. Open a PR with a `fix:` title so semantic-release increments the patch
   version after merge.
3. Do not bypass CI. Fix PR CI failures in the branch.
4. Squash-merge after PR checks pass.
5. Watch main CI through native assets, PyPI publication, and publish success.
6. Verify public install with `uvx --refresh-package tensor-grep --from
   tensor-grep==1.13.16 tg --version`.
