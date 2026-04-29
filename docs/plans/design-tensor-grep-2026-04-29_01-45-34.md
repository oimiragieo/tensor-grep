# Design: MCP Runtime Capability and Native-Fallback Contract Hardening

Project: tensor-grep
Date: 2026-04-29 01:45:34
Status: Proposed design for next implementation slice

## Current System Context

`tensor-grep` exposes an MCP server in `src/tensor_grep/cli/mcp_server.py`. The server provides repo-map, context, symbol, edit-plan, rewrite, index, device, and session-oriented tools for AI harnesses.

Important current behavior:

- Native command execution uses `resolve_native_tg_binary`.
- Simple rewrite plan/apply can fall back to embedded Python/PyO3 functions when standalone native `tg` is missing.
- More advanced rewrite and index flows still require standalone native `tg`.
- `docs/harness_api.md` documents this distinction, but clients do not have a compact runtime capability endpoint.
- Existing integration tests cover happy-path MCP workflows when native `tg` is available, but no-native behavior is not locked as a first-class contract.

## Proposed Solution

Add an additive MCP runtime capability contract and normalize native-unavailable failures.

The core design is:

1. Add a small capability registry in `mcp_server.py`.
2. Add `tg_mcp_capabilities` as a machine-readable MCP tool.
3. Add one shared native-unavailable error helper.
4. Guard native-only MCP tools before native command construction or execution.
5. Preserve embedded fallback behavior for simple rewrite plan/apply.
6. Update harness docs so agents know to call capabilities before choosing a tool chain.

This is intentionally smaller than implementing embedded parity for every native-only tool. The business value is reliability and transparency, not new backend complexity.

## Architecture and Component Changes

### `src/tensor_grep/cli/mcp_server.py`

Add a local capability model:

```python
McpToolMode = Literal["python-local", "embedded-safe", "native-required"]

_MCP_TOOL_CAPABILITIES = {
    "tg_mcp_capabilities": {
        "mode": "python-local",
        "native_required": False,
        "embedded_fallback": False,
        "notes": "Reports MCP runtime capabilities.",
    },
    "tg_rewrite_plan": {
        "mode": "embedded-safe",
        "native_required": False,
        "embedded_fallback": True,
        "notes": "Uses embedded rewrite fallback for simple plan requests when native tg is absent.",
    },
    "tg_rewrite_diff": {
        "mode": "native-required",
        "native_required": True,
        "embedded_fallback": False,
        "notes": "Requires standalone native tg.",
    },
}
```

The actual implementation must include every public MCP tool exactly once. A partial capability registry is not acceptable because omitted tools would still fail unpredictably. Add a parity test that compares registered MCP tool names with `_MCP_TOOL_CAPABILITIES`.

Add runtime helpers:

```python
def _embedded_rewrite_available() -> bool: ...


def _mcp_capabilities_payload() -> dict[str, Any]: ...


def _native_unavailable_error(*, tool: str, backend: str, reason: str) -> dict[str, Any]: ...
```

Recommended unavailable error shape:

```json
{
  "version": 1,
  "routing_backend": "MCPRuntime",
  "routing_reason": "native-tg-unavailable",
  "sidecar_used": false,
  "tool": "tg_rewrite_diff",
  "error": {
    "code": "unavailable",
    "message": "tg_rewrite_diff requires a standalone native tg binary.",
    "remediation": "Install a native tg binary, put it on PATH, or set TG_NATIVE_TG_BINARY."
  }
}
```

### Tests

Update `tests/unit/test_mcp_server.py` first.

Expected new test classes or cases:

- `test_mcp_capabilities_reports_no_native_binary`
- `test_mcp_capabilities_reports_native_binary_path`
- `test_mcp_capabilities_classifies_rewrite_fallbacks`
- `test_rewrite_plan_uses_embedded_fallback_without_native_binary`
- `test_rewrite_apply_uses_embedded_fallback_without_native_binary`
- `test_rewrite_diff_returns_unavailable_without_native_binary`
- `test_index_search_returns_unavailable_without_native_binary`

Keep integration tests in `tests/integration/test_harness_adoption.py` native-present only unless the project already has a reliable way to simulate missing native binary at integration level.

### Documentation

Update:

- `docs/harness_api.md`
  - Add MCP runtime capability tool.
  - Add capability mode matrix.
  - Document native-unavailable error shape.

- `docs/harness_cookbook.md`
  - Add a "capabilities first" workflow:
    1. Call `tg_mcp_capabilities`.
    2. Use embedded-safe tools when native `tg` is unavailable.
    3. Use native-required tools only when native `tg` is available.
    4. Show fallback behavior for rewrite plan/apply.

- `README.md`
  - Update MCP tool list with `tg_mcp_capabilities`.

No benchmark docs should be changed unless measured performance data is produced.

## Data Model / API Contract

### Capability Response

```json
{
  "version": 1,
  "routing_backend": "MCPRuntime",
  "routing_reason": "mcp-capabilities",
  "sidecar_used": false,
  "native_tg": {
    "available": true,
    "path": "C:/path/to/tg.exe"
  },
  "embedded_rewrite": {
    "available": true
  },
  "tools": [
    {
      "name": "tg_rewrite_plan",
      "mode": "embedded-safe",
      "native_required": false,
      "embedded_fallback": true,
      "notes": "Simple plan requests can use embedded fallback."
    }
  ]
}
```

### Error Response

Use a stable envelope for native-only unavailable cases:

```json
{
  "version": 1,
  "routing_backend": "MCPRuntime",
  "routing_reason": "native-tg-unavailable",
  "sidecar_used": false,
  "tool": "tg_index_search",
  "error": {
    "code": "unavailable",
    "message": "tg_index_search requires a standalone native tg binary.",
    "remediation": "Install a native tg binary, put it on PATH, or set TG_NATIVE_TG_BINARY."
  }
}
```

## UI / UX Changes

No website or frontend application changes are required. The user-facing change is the MCP JSON contract and docs.

## Auth, Billing, Deployment, and Integration Impact

- Auth: none.
- Billing: none.
- Database: none.
- Deployment: no production deployment required.
- Integration: MCP clients gain a capability discovery step and more stable unavailable responses.

## Security and Privacy Considerations

- Do not expose environment variable values.
- Reporting the resolved native binary path is acceptable because it is local operational state, but do not include command arguments, file contents, or secrets.
- Do not shell out while building the capabilities response.
- Native-unavailable errors must be data-only responses; they must not attempt remediation automatically.
- Keep subprocess command construction unchanged except where guarded by explicit availability checks.

## Performance and Reliability Considerations

- The capability tool should be cheap: native path lookup plus import availability checks only.
- No hot-path CLI search behavior should change.
- No benchmark claims are allowed for this slice.
- If implementation touches shared native resolution or search routing, run the relevant CLI benchmark and regression checker before release.

## Alternatives Considered

1. Documentation-only capability matrix
   - Rejected because agents need runtime state, not static docs.

2. Implement embedded fallback for every native-required MCP tool
   - Rejected for this slice because it is larger, riskier, and duplicates native behavior without current evidence of demand.

3. Add additive `tg_mcp_capabilities` plus structured native-unavailable errors
   - Recommended because it is small, testable, backwards-compatible, and directly improves AI-harness reliability.

4. Defer MCP work and optimize GPU routing next
   - Rejected for now. Current GPU evidence still shows no automatic routing crossover and hardware/toolchain constraints remain. MCP reliability has clearer immediate user value.

## Research Findings

These findings validate prioritization only; they are not implementation scope for this MCP slice.

- ast-grep remains an important structural search/rewrite comparator, so `tg` should preserve clear structural tooling contracts: https://ast-grep.github.io/reference/cli.html
- Agent-facing code search tools such as Probe emphasize MCP, tree-sitter, and token-aware context delivery, validating the product direction for deterministic MCP search/edit tools: https://github.com/probelabs/probe
- Community tool requests for `ast_grep` and `ast_edit` in agent CLIs show demand for deterministic AST/edit tooling in AI workflows: https://github.com/sst/opencode/issues/18822
- Repo-level retrieval research such as RANGER supports a later benchmark suite for change-request-driven retrieval and recall, but that should follow MCP contract hardening: https://arxiv.org/abs/2509.25257
- GPU regex research such as RAPIDS cuDF Glushkov NFA work and BitGen supports future GPU experiments for multi-pattern/high-arithmetic-intensity workloads, but not automatic routing in this release slice:
  - https://github.com/rapidsai/cudf/pull/21936
  - https://github.com/getianao/BitGen

## Release And Scope Decisions

- Adding `tg_mcp_capabilities` is a minor release and should use `feat:` because it is a new public MCP capability.

- The capability registry must enumerate every public MCP tool exactly once.

- The resolved native binary path should be included by default because it is useful local diagnostics, but environment variable values and command arguments must not be exposed.
