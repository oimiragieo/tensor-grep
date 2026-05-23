# Tensor-Grep v1.13 Dogfood Follow-Ups

Goal: turn the v1.13.0 dogfood failures into verified, PR-sized fixes without promoting unsupported claims.

## Slice Plan

- [x] Session warm/open latency: profile repo-map open, remove unconditional context-file NUL probing, add opt-in `session open --max-repo-files`, persist `scan_limit`, expose timing metadata, and add no-daemon serve response caching.
- [x] MCP wire lifecycle: add a real stdio initialize/tools/list/tools/call protocol smoke and keep in-process payload tests.
- [x] Security ruleset recall: add scoped regex-backed prefixed API key assignment detection while preserving `secrets-basic` preview positioning.
- [x] LSP evidence posture: keep provider initialize timeout realistic, test hybrid fallback labels, and avoid treating provider health/install status as navigation proof.
- [x] Root/native search guardrails: route option-first rg flags through the root shortcut and refuse unbounded no-ignore generated-root normal searches before rg passthrough.
- [x] JSON/schema posture: stamp `schema_version` on the affected JSON/MCP surfaces and update contract tests.

## External Anchors

- ripgrep guide: `https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md`
- MCP lifecycle: `https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle`
- MCP tools: `https://modelcontextprotocol.io/specification/draft/server/tools`
- ast-grep pattern syntax: `https://ast-grep.github.io/guide/pattern-syntax.html`
- GitHub custom secret patterns: `https://github.com/github/docs/blob/main/content/code-security/reference/secret-security/custom-patterns.md`
- pyright initialization behavior context: `https://github.com/microsoft/pyright/pull/10786`
- agent-lsp startup timing context: `https://github.com/blackwell-systems/agent-lsp/blob/c8aa4356cc66c0b5a511e9c2bf445c1ad072380d/docs/ci-notes.md`

## Thinktank Receipts

- `019e54f0-5b55-77b1-ab2f-bd9aa7553ba9`: slice ordering and unsupported-claim boundaries.
- `019e54f0-8f8c-7c91-ae36-f9a1377f49a6`: MCP/security contract review.
- `019e54f0-c8d3-7d53-a227-f096f07a4b2a`: session/LSP contract review.

## Dogfood-Sized Session Probe

- Full `session open C:/dev/projects/agent-studio --json`: wall `28.160s`, `build_seconds=26.812`, `files=3690`, `symbols=16343`.
- Capped `session open --max-repo-files 512`: wall `2.179s`, `build_seconds=1.625`, `files=497`, `symbols=2271`, `scan_limit.possibly_truncated=true`.
- Full-session no-daemon edit-plan: `3.714s`.
- Capped-session no-daemon edit-plan: `2.163s`.
- Capped-session daemon edit-plan: first `2.439s`, repeat calls `0.516s` and `0.528s`.

## Validation

- `uv run pytest ... tests/integration/test_mcp_stdio_protocol.py -q`: `95 passed in 17.82s`.
- Expanded schema/MCP/LSP/security sweep: `173 passed in 38.53s`.
- `uv run ruff check ...`: `All checks passed!`.
- `uv run ruff format --check --preview ...`: `24 files already formatted`.
- `C:/Users/oimir/.cargo/bin/cargo.exe fmt --manifest-path rust_core/Cargo.toml --check`: passed.
- `C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml --test test_public_native_cli_parity -- --nocapture`: `31 passed`.
- `git diff --check`: passed with only CRLF conversion warnings.

## Non-Claims Preserved

- Do not claim faster-than-rg raw cold search.
- Do not claim full ast-grep replacement or fuzzy optional AST syntax.
- Do not claim production GPU search without public managed native proof.
- Do not claim production LSP navigation until rows carry completed provider responses.
