# Tasks: MCP Runtime Capability and Native-Fallback Contract Hardening

Project: tensor-grep
Date: 2026-04-29 01:45:34
Status: Implemented; final validation in progress
Primary files likely to change: `src/tensor_grep/cli/mcp_server.py`, `tests/unit/test_mcp_server.py`, `docs/harness_api.md`, `docs/harness_cookbook.md`, `README.md`

## Execution Rules

- Follow `AGENTS.md`: test first, smallest defensible change, run local gates, benchmark hot-path changes only when relevant.
- Follow `docs/CI_PIPELINE.md` before any workflow/release edits.
- Do not edit `.github/workflows/*` or release validators unless a concrete failure requires it.
- Do not edit `docs/superpowers`.
- Do not make GPU, AST, or rg parity behavior changes in this slice.
- Do not claim benchmark speedups.

## Vertical Slice 1: Add MCP Capabilities Contract

### Goal

Expose runtime MCP capabilities so AI harnesses can choose supported tools before executing a workflow.

### Result

Completed. `tg_mcp_capabilities` is registered as a public MCP tool, reports native `tg` availability, reports embedded rewrite availability, and enumerates every public MCP tool exactly once.

### TDD Plan

Write failing tests first in `tests/unit/test_mcp_server.py`:

1. `test_mcp_capabilities_reports_no_native_binary`
   - Monkeypatch `resolve_native_tg_binary` to return `None`.
   - Call the public `tg_mcp_capabilities` MCP tool; helper-only coverage is not sufficient.
   - Assert:
     - `version == 1`
     - `routing_backend == "MCPRuntime"`
     - `routing_reason == "mcp-capabilities"`
     - `native_tg.available is False`
     - `native_tg.path is None`

2. `test_mcp_capabilities_reports_native_binary_path`
   - Monkeypatch `resolve_native_tg_binary` to return a fake path.
   - Assert `native_tg.available is True` and path is surfaced.

3. `test_mcp_capabilities_classifies_rewrite_tools`
   - Assert:
     - `tg_rewrite_plan` is `embedded-safe`.
     - `tg_rewrite_apply` is `embedded-safe`.
     - `tg_rewrite_diff` is `native-required`.
     - `tg_mcp_capabilities` is `python-local`.

4. `test_mcp_capabilities_registry_covers_public_tools`
   - Compare public MCP tool names with `_MCP_TOOL_CAPABILITIES`.
   - Assert every public tool appears exactly once.
   - Assert no capability entry references a non-public tool.

5. `test_mcp_capabilities_reports_embedded_rewrite_unavailable`
   - Monkeypatch embedded rewrite availability to false.
   - Assert `embedded_rewrite.available is False`.

### Implementation

- Add `_MCP_TOOL_CAPABILITIES` to `mcp_server.py`.
- Include every public MCP tool exactly once.
- Add `_embedded_rewrite_available()`.
- Add `_mcp_capabilities_payload()`.
- Register an MCP tool named `tg_mcp_capabilities`.
- Keep the response data-only and do not shell out.

### Validation

Run:

```powershell
uv run pytest tests/unit/test_mcp_server.py -q
```

## Vertical Slice 2: Normalize Native-Unavailable Errors

### Goal

Native-only MCP tools should return stable JSON errors when standalone native `tg` is unavailable.

### Result

Completed. `tg_index_search`, `tg_rewrite_diff`, and native-only rewrite apply options now return `routing_reason = "native-tg-unavailable"` with `error.code = "unavailable"` and `TG_NATIVE_TG_BINARY` remediation.

### TDD Plan

Add failing tests in `tests/unit/test_mcp_server.py`:

1. `test_rewrite_diff_returns_unavailable_without_native_binary`
   - Monkeypatch `resolve_native_tg_binary` to `None`.
   - Call `tg_rewrite_diff`.
   - Assert:
     - `error.code == "unavailable"`
     - `routing_reason == "native-tg-unavailable"`
     - remediation mentions native `tg` or `TG_NATIVE_TG_BINARY`.

2. `test_index_search_returns_unavailable_without_native_binary`
   - Monkeypatch native resolution to `None`.
   - Call `tg_index_search` or its executor helper.
   - Assert the same stable unavailable envelope.

3. `test_native_unavailable_error_preserves_tool_name`
   - Unit-test the helper directly if exposed internally.

4. `test_native_present_rewrite_diff_command_envelope_is_unchanged`
   - Monkeypatch native resolution to a fake path.
   - Monkeypatch command execution to avoid spawning a real process.
   - Assert command construction and returned envelope remain compatible with existing behavior.

### Implementation

- Add `_native_unavailable_error(tool: str, backend: str | None = None)`.
- Guard native-only tool handlers before command construction.
- Keep existing native-present execution paths unchanged.

### Validation

Run:

```powershell
uv run pytest tests/unit/test_mcp_server.py -q
```

## Vertical Slice 3: Lock Embedded Rewrite Fallback Behavior

### Goal

Preserve the documented no-native fallback for simple rewrite plan/apply, while native-only rewrite variants fail predictably.

### Result

Completed. MCP `tg_rewrite_plan` now uses the same embedded fallback path as `execute_rewrite_plan_json`; simple `tg_rewrite_apply` fallback remains covered; no-native/no-embedded behavior returns a structured unavailable response.

### TDD Plan

Add or strengthen tests:

1. `test_rewrite_plan_uses_embedded_fallback_without_native_binary`
   - Monkeypatch native resolution to `None`.
   - Monkeypatch embedded rewrite plan function to return deterministic JSON.
   - Assert the embedded result is returned.

2. `test_rewrite_apply_uses_embedded_fallback_without_native_binary`
   - Same structure for apply.

3. `test_rewrite_verify_without_native_binary_returns_unavailable_if_native_required`
   - If verify/audit/checkpoint paths are exposed in MCP, assert unavailable contract.

4. `test_rewrite_plan_without_native_or_embedded_returns_unavailable`
   - Monkeypatch native resolution to `None`.
   - Monkeypatch embedded rewrite availability/imports to fail.
   - Assert a structured unavailable error with remediation.

### Implementation

- If needed, centralize the embedded fallback eligibility check.
- Do not implement new embedded rewrite features.
- Do not change AST rewrite semantics.

### Validation

Run:

```powershell
uv run pytest tests/unit/test_mcp_server.py -q
uv run pytest tests/integration/test_harness_adoption.py -q
```

If the integration suite skips due missing native binary, record that fact rather than treating it as failure.

## Vertical Slice 4: Documentation Update

### Goal

Make the public harness contract match implementation.

### Result

Completed. README, harness API docs, harness cookbook, changelog, and paper notes were updated. Lightweight documentation assertions cover the capability tool, modes, and native-unavailable schema.

### Tasks

- Update `docs/harness_api.md`:
  - Add `tg_mcp_capabilities`.
  - Add capability modes.
  - Add native-unavailable error schema.

- Update `docs/harness_cookbook.md`:
  - Add "capabilities first" MCP workflow.
  - Show how an agent should avoid native-only tools when native `tg` is absent.

- Update `README.md`:
  - Add `tg_mcp_capabilities` to the MCP list.
  - Keep messaging concise; do not add new benchmark claims.

- Add lightweight documentation/schema assertions:
  - `tg_mcp_capabilities` appears in README and harness docs.
  - Capability modes are documented.
  - Native-unavailable error fields are documented.

### Validation

Run docs-related tests if present. At minimum run:

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
```

## Vertical Slice 5: Final Verification and Release Readiness

### Required Local Gates

Run:

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
```

`uv run pytest -q` needs at least a 120 second timeout on this Windows machine.

### Result

In progress. Focused MCP and documentation tests pass; full local gates remain required before branch completion.

### Optional Focused Checks

Run if implementation touches release/docs validators or native packaging:

```powershell
uv run python scripts/validate_release_assets.py
uv run pytest tests/unit/test_release_assets_validation.py -q
```

### Benchmarks

No benchmarks are required if the implementation only touches MCP capability/error paths.

Run CLI benchmarks only if implementation touches search routing, native binary resolution in CLI hot paths, or backend search behavior:

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
```

## Release Steps

1. Confirm worktree is clean except intentional changes.
2. Use conventional branch and PR flow unless the maintainer explicitly approves direct main push:
   - Branch: `feat/mcp-runtime-capabilities`.
   - PR title / squash commit: `feat: expose mcp runtime capabilities`.
3. If direct push is explicitly approved later, first confirm `origin/main` has not moved and the worktree contains only intentional changes.
4. Monitor GitHub Actions with `gh run list` and `gh run view`.
5. Confirm semantic-release publishes the expected minor version after the release-bearing merge.

## Rollback Plan

- Revert the single implementation commit.
- No migrations, secrets, external service changes, billing changes, or production deployments are involved.
- Existing MCP clients remain safe because the recommended change is additive.

## Definition of Done

- New MCP capabilities contract exists and is documented.
- Native-missing and native-present behavior are tested.
- Embedded rewrite fallback remains covered by tests.
- Native-only unavailable errors are stable and actionable.
- README/harness docs match implementation.
- Required local gates pass.
- CI is green after PR/squash merge or accepted direct push if implementation proceeds.

## Superpowers and Tools For Prompt 2

- Use `superpowers:test-driven-development` before editing implementation code.
- Use `superpowers:systematic-debugging` for any failing test or CI failure.
- Use `superpowers:verification-before-completion` before claiming completion.
- Use `github:gh-fix-ci` only if GitHub Actions fails.
- Use Exa/ref/Context7 only for implementation questions that repo evidence cannot answer.

## Separate Follow-Up Candidates (Not In Scope)

After this MCP contract hardening slice, consider a separate agent-facing structural retrieval benchmark:

- Query shape: issue/change-request text.
- Candidate outputs: files, symbols, ranges, AST chunks, and MCP context packets.
- Metrics: recall@k, MRR, latency, token budget, and patch-plan usefulness.
- Research basis: RANGER, CoIR, RepoGraph, Probe, and current agent-code-search community signals.

GPU routing remains deferred until benchmark evidence shows a reliable crossover on supported hardware/toolchains.
