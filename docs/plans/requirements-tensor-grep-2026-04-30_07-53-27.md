# tensor-grep v1.7.0 Post-Release Audit Requirements

**Objective:** Verify the released `v1.7.0` CLI, MCP, edit, and GPU surfaces against the current repository contracts, then update docs only when fresh evidence changes the accepted story.

## Problem Statement

`tensor-grep` just shipped `v1.7.0` with MCP runtime capabilities and embedded-safe rewrite support. The business risk is not missing another speculative feature; it is overstating stability, GPU acceleration, edit safety, or MCP coverage without fresh operational proof.

## Business Objective

Keep `tensor-grep` credible as a benchmark-governed search/edit substrate for AI harnesses by proving the shipped release works, preserving honest benchmark claims, and identifying only concrete follow-up work.

## Stakeholders

- CEO: needs a short, truthful release-readiness answer.
- Agent/harness integrators: need reliable CLI and MCP capability contracts.
- Maintainers: need reproducible checks, artifacts, and rollback rules.

## Functional Requirements

1. Release smoke must verify `tensor-grep==1.7.0` from PyPI, not only local source.
2. MCP smoke must verify `tg_mcp_capabilities()` reports the expected runtime contract and tool registry.
3. GPU audit must check local discoverability and benchmark behavior without promoting GPU auto-routing unless accepted artifacts prove a crossover.
4. Edit audit must verify rewrite plan/apply coverage through existing tests and, when benchmarked, AST rewrite artifacts.
5. Documentation updates must be evidence-backed and limited to README, CHANGELOG, `docs/PAPER.md`, and benchmark docs when fresh results differ from the accepted story.
6. Any code change must start with a failing test and pass the required local gates before push.

## Non-Functional Requirements

- Avoid speculative implementation.
- Preserve current CI/release contracts.
- Avoid destructive workspace operations.
- Treat benchmark `SKIP` as valid infrastructure evidence when GPU/Triton/CUDA is unavailable.
- Do not claim speedups without accepted benchmark output.

## Acceptance Criteria And Test Mapping

| ID | Acceptance Criterion | Verification |
| --- | --- | --- |
| AC1 | Local `main` is synced to the released `v1.7.0` state without losing local untracked planning docs. | `git status --short --branch`; backup path recorded. |
| AC2 | PyPI release exposes the expected CLI. | `uvx --from tensor-grep==1.7.0 tg --version`; `uvx --from tensor-grep==1.7.0 tg --help`. |
| AC3 | PyPI release exposes MCP capabilities. | `uv run --with tensor-grep==1.7.0 --with fastmcp python -c "...tg_mcp_capabilities..."` outside the repo; assert `version == 1`, `routing_backend == "MCPRuntime"`, 41 tools, and `tg_mcp_capabilities` is listed by tool name. |
| AC4 | Local source exposes GPU/device state without crashing. | `uv run tg doctor --json`; `uv run tg devices --json` or supported equivalent. |
| AC5 | MCP and harness docs remain contract-tested. | `uv run pytest tests/unit/test_harness_api_docs.py tests/unit/test_harness_cookbook.py -q`; direct MCP capability import. |
| AC6 | Edit path remains covered before any docs claim changes. | Existing rewrite tests and optional `benchmarks/run_ast_rewrite_benchmarks.py` artifact. |
| AC7 | Full release gates pass before any code-bearing push. | `uv run ruff check .`; `uv run mypy src/tensor_grep`; `uv run pytest -q`. |

## Out Of Scope

- No production deploy.
- No release tag creation.
- No CI workflow edits unless a concrete CI failure requires it.
- No GPU engine rewrite without a separate SPEC/TDD plan and accepted benchmark target.

## Risks And Mitigations

- **Risk:** Local shell `tg` differs from PyPI or repo binary. **Mitigation:** verify PyPI with `uvx`, local source with `uv run`, and record source explicitly.
- **Risk:** GPU hardware exists but dependency stack is unsupported. **Mitigation:** report device/dependency state; treat `SKIP` as infrastructure evidence, not product failure.
- **Risk:** Docs overclaim GPU speed. **Mitigation:** only update public claims after benchmark gate acceptance.
- **Risk:** MCP clients assume native tools exist in PyPI wheel installs. **Mitigation:** keep `tg_mcp_capabilities()` as the preflight contract.

## Research Evidence

- Exa found RAPIDS cuDF PR #21936, opened 2026-03-25, adding a draft Glushkov-NFA regex path with fallback limits and reported 1.1x-4x simple-regex speedups: https://github.com/rapidsai/cudf/pull/21936
- Exa found RAPIDS cuDF issue #21125, opened 2026-01-21, confirming regex throughput is an active performance area and not a solved dependency surface: https://github.com/rapidsai/cudf/issues/21125
- Exa found BitGen MICRO 2025 and HybridSA 2024 as credible bit-parallel GPU regex directions, but both require integration work and end-to-end `tg` benchmark proof before claims change.
