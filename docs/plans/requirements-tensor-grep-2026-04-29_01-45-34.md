# Requirements: MCP Runtime Capability and Native-Fallback Contract Hardening

Project: tensor-grep
Date: 2026-04-29 01:45:34
Status: Proposed next implementation slice
Release intent: `feat:` because this plan adds a new public MCP capability tool.

## Problem Statement

`tensor-grep` now has strong CLI, AST, rewrite, repeated-query, and benchmark-governed GPU surfaces. The next business-critical risk is the public AI-harness surface: the MCP server exposes many tools, but several tools depend on a standalone native `tg` binary while only a small subset has embedded Python/PyO3 fallback support.

Today, users and agents can discover MCP tools but cannot reliably know which tools will work in a PyPI/wheel/no-standalone-binary install. Native-only failures can be opaque or inconsistent. This creates support risk and weakens the "AI-harness-friendly" product promise.

## Business Objective

Make MCP integrations predictable, transparent, and test-backed so agent platforms can adopt `tg` safely:

- Reduce failed AI-harness sessions caused by hidden native binary assumptions.
- Improve enterprise trust by making runtime capabilities explicit and machine-readable.
- Protect current CLI/AST/rewrite wins without broad rewrites or unmeasured performance claims.
- Keep the release small, reversible, and validator-friendly.

## Target Users and Stakeholders

- AI harnesses using the MCP server for repo search, context, symbol, and edit workflows.
- PyPI/wheel users who may not have a standalone native `tg` binary on PATH.
- Maintainers who need a locked contract for MCP fallback behavior.
- Business stakeholders who need the product story to remain reliable: "fast grep + structural search + agent-safe edit tooling."

## User Stories / Jobs To Be Done

- As an AI harness, I need a machine-readable capabilities response so I can route around unavailable native-only tools before a workflow fails.
- As a PyPI user without standalone `tg`, I need simple rewrite plan/apply flows to keep working through embedded fallback.
- As an MCP user, I need native-only tools to return stable structured unavailable errors with remediation instead of surprising subprocess failures.
- As a maintainer, I need unit and integration tests that lock the native-present and native-missing behavior.
- As a release manager, I need docs and tests to state the capability contract before cutting a minor or patch release.

## Functional Requirements

1. MCP runtime capability inventory
   - Define a single source of truth for MCP tool capability modes.
   - Enumerate every public MCP tool exactly once; partial capability registries are not acceptable.
   - Classify tools as one of:
     - `python-local`: implemented fully in Python without standalone native `tg`.
     - `embedded-safe`: has embedded Python/PyO3 fallback when standalone native `tg` is absent.
     - `native-required`: requires standalone native `tg`.
   - Include native binary availability and path when available.
   - Include embedded rewrite availability.
   - Add a parity test that fails when a public MCP tool is registered without a matching capability entry.

2. Public MCP capabilities surface
   - Add an additive machine-readable tool, recommended name: `tg_mcp_capabilities`.
   - Return a stable JSON envelope with:
     - `version`
     - `routing_backend`
     - `routing_reason`
     - `sidecar_used`
     - `native_tg.available`
     - `native_tg.path`
     - `embedded_rewrite.available`
     - `tools[]` with `name`, `mode`, `native_required`, `embedded_fallback`, and `notes`.
   - Do not rename or remove existing MCP tools.

3. Native-unavailable error contract
   - Native-only tools must return structured JSON errors when standalone native `tg` is unavailable.
   - Error envelopes must include:
     - `error.code = "unavailable"`
     - a clear `error.message`
     - a remediation field that mentions installing/exposing native `tg` or setting `TG_NATIVE_TG_BINARY` when applicable.
   - Error responses must preserve the relevant routing/backend metadata used by existing harness contracts.

4. Embedded rewrite fallback contract
   - Preserve embedded fallback for simple `tg_rewrite_plan` and `tg_rewrite_apply`.
   - Native-only rewrite variants such as diff/checkpoint/audit/verify must fail with the structured unavailable contract when native `tg` is absent.
   - Native-present behavior must remain unchanged.

5. Documentation
   - Update `docs/harness_api.md` with the capability matrix and error contract.
   - Update `docs/harness_cookbook.md` with a recommended "capabilities first" MCP workflow.
   - Update `README.md` MCP section with `tg_mcp_capabilities` and concise runtime guidance.
   - Do not add benchmark speed claims unless new benchmark evidence is produced and accepted.

6. Governance
   - Follow `AGENTS.md` and `docs/CI_PIPELINE.md`.
   - Do not edit workflow/release automation unless the implementation uncovers a concrete validator-backed need.

## Non-Functional Requirements

- Cross-platform behavior must be deterministic on Windows, Linux, and macOS.
- The change must be additive and backwards-compatible for existing MCP clients.
- No auth, billing, SaaS, frontend, database, or production deployment work is in scope.
- No GPU routing changes or speed claims are in scope.
- No broad refactor of the MCP server is allowed without test-backed need.
- Error messages must avoid exposing secrets, private paths beyond the explicit binary path, or user content.

## Acceptance Criteria and Test Mapping

| ID | Acceptance Criterion | Required Tests |
| --- | --- | --- |
| AC1 | MCP exposes a stable public `tg_mcp_capabilities` tool with native and embedded availability. | Unit tests in `tests/unit/test_mcp_server.py` monkeypatching `resolve_native_tg_binary` to both `None` and a fake path, plus a public MCP registration/call test. |
| AC2 | Every public MCP tool has exactly one capability entry and capability modes match implementation reality. | Unit test comparing public MCP tool names with the capability registry, plus assertions for `python-local`, `embedded-safe`, and `native-required` classifications. |
| AC3 | `tg_rewrite_plan` and `tg_rewrite_apply` still use embedded fallback when no native binary exists and the embedded Rust extension is importable. | Unit tests monkeypatching native resolution to `None` and embedded rewrite functions to deterministic JSON. |
| AC4 | Native-only tools return structured unavailable errors when no native binary exists. | Unit tests for `tg_rewrite_diff` and at least one index/native search path such as `tg_index_search`. |
| AC5 | Native-present behavior remains compatible with current command execution envelopes. | Existing MCP tests plus one targeted native-present command construction/execution-envelope regression test with a fake native path. |
| AC6 | Capability reporting and errors remain clear when neither native `tg` nor embedded rewrite is available. | Unit tests monkeypatching native resolution to `None` and embedded import/check helpers to unavailable, then asserting `embedded_rewrite.available is False` and native-only errors remain structured. |
| AC7 | Public docs match the implemented MCP capability contract. | Add lightweight doc/schema assertions for `tg_mcp_capabilities`, capability modes, and native-unavailable error fields. |
| AC8 | Release readiness is verifiable. | `uv run ruff check .`, `uv run mypy src/tensor_grep`, focused MCP tests, full `uv run pytest -q`; CI green after PR/squash merge or accepted push workflow. |

## Out Of Scope

- Implementing embedded versions of all native-only rewrite/index/audit tools.
- Reworking the MCP transport protocol.
- Changing AST search semantics or rg parity behavior.
- GPU automatic routing or CUDA kernel work.
- New website, billing, auth, or deployment systems.
- Editing `docs/superpowers` or ignored local planning files.

## Assumptions

- The current public MCP implementation remains in `src/tensor_grep/cli/mcp_server.py`.
- Native binary discovery continues to use `resolve_native_tg_binary`.
- Embedded rewrite fallback remains available through the packaged Rust/PyO3 functions when installed.
- Adding one new MCP status/capability tool is a minor release and should use a `feat:` conventional title.

## Risks and Mitigations

- Risk: Capability registry drifts from actual tools.
  - Mitigation: Unit tests assert every public MCP tool appears exactly once in the capability registry, plus representative mode classifications.

- Risk: Existing clients depend on current error shapes.
  - Mitigation: Make changes additive and preserve existing metadata where possible.

- Risk: Embedded fallback availability varies by installation.
  - Mitigation: Capabilities must report embedded rewrite availability separately from native binary availability.

- Risk: Scope expands into implementing full embedded native parity.
  - Mitigation: Explicitly keep native-only tools native-only for this slice; only normalize discovery and errors.

## Tool and Research Evidence Summary

- Repository evidence:
  - `README.md` describes MCP as a harness-first surface and lists MCP tools.
  - `docs/harness_api.md` documents that simple rewrite plan/apply can work through packaged PyO3 fallback, while diff/checkpoint/audit/verify/native-only flows require standalone native `tg`.
  - `src/tensor_grep/cli/mcp_server.py` already uses `resolve_native_tg_binary` and has embedded fallback only for simple rewrite plan/apply.
  - `tests/integration/test_harness_adoption.py` covers native MCP roundtrips but skips when native `tg` is missing.
  - `tests/unit/test_mcp_server.py` contains broad MCP coverage but lacks a full no-standalone-binary capability contract.

- External validation:
  - ast-grep official CLI docs confirm structural search/rewrite remains an active comparator surface: https://ast-grep.github.io/reference/cli.html
  - Probe and community MCP/code-search tools show practical demand for deterministic, token-aware, agent-facing code search: https://github.com/probelabs/probe
  - OpenCode community demand for AST grep/edit tools supports prioritizing reliable MCP tool contracts for agents: https://github.com/sst/opencode/issues/18822
  - Repo-level retrieval research such as RANGER supports future agent-facing retrieval benchmarks after the MCP contract is hardened: https://arxiv.org/abs/2509.25257
