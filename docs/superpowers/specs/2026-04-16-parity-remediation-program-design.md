# Parity Remediation Program Design

**Date:** 2026-04-16

**Status:** Proposed

**Goal**

Restore `tensor-grep` to an honest, validator-backed direct replacement for the promised common `ripgrep` and `ast-grep` surfaces, while preserving the differentiated AI/intelligence layer and the repo's benchmark-governed discipline.

## Problem Statement

External parity evaluation shows that `tensor-grep` is strong where repository intelligence, planning, and agent-facing workflows matter, but still has material gaps on the direct-replacement surfaces it claims to cover.

Those gaps are not one bug. They span four independent classes of work:

1. `ripgrep`-compat correctness and output-contract bugs
2. `ast-grep`-compat wrapper and workflow gaps
3. platform and terminal compatibility issues
4. benchmark-governed performance regressions

This repo already has strong operating rules for these classes:

- start from a failing test
- make the smallest defensible change
- keep correctness and performance separate
- benchmark hot paths before claiming improvements
- only update public docs when accepted behavior or accepted benchmark lines change

The remediation program therefore must not be executed as one giant branch. It must be run as one umbrella program with narrow, validator-backed slices.

## Strategic Direction

The product direction remains:

- `tensor-grep` should be a direct replacement for the promised common text-search and structural-search surfaces
- `tensor-grep` should keep the AI/intelligence layer as the differentiated value on top
- workload-class honesty still applies, so parity claims and performance claims must stay tied to accepted tests and accepted benchmark artifacts

This is not a repositioning away from `ripgrep` or `ast-grep`. It is a remediation program to make the existing product claim true on the surfaces the repo already promises.

## Program Structure

The program is one umbrella spec with four execution workstreams:

1. Text parity
2. AST parity
3. Platform compatibility
4. Performance remediation

Each workstream will get its own execution-ready TDD plan. Each landed slice must belong to exactly one workstream.

## Workstream 1: Text Parity

**Scope**

This workstream covers the `ripgrep`-compat search surface and its output contracts.

Included backlog:

- `-r` capture-group substitution
- `--files-without-match` false positives
- `-0` / `--null` separator correctness
- `--files` without requiring a pattern
- `--glob-case-insensitive`
- broader output-contract gaps for `--json`, `--stats`, and `--debug`

**Likely code surfaces**

- `src/tensor_grep/cli/main.py`
- `src/tensor_grep/backends/ripgrep_backend.py`
- `src/tensor_grep/io/directory_scanner.py`
- `src/tensor_grep/cli/formatters/ripgrep_fmt.py`
- `rust_core/src/main.rs` where native output or passthrough behavior overlaps

**Primary validation surfaces**

- `tests/unit/test_cli_modes.py`
- `tests/unit/test_cli_bootstrap.py`
- `tests/unit/test_directory_scanner.py`
- `tests/unit/test_ripgrep_backend.py`
- `tests/e2e/test_output_golden_contract.py`
- `tests/e2e/test_output_snapshots.py`
- `tests/e2e/test_routing_parity.py`

**Definition of done**

A text-parity issue is done when:

- there is a failing test proving the broken contract
- the smallest fix lands
- the relevant unit or e2e contract tests pass
- any raw-bytes contract is asserted without newline normalization hiding the behavior
- public docs change only if the user-visible contract statement itself changes

## Workstream 2: AST Parity

**Scope**

This workstream covers the `ast-grep`-compat surface and wrapper-loss issues, including CLI, MCP, and workflow payload contracts.

Included backlog:

- Rust AST parity re-validation and remaining structural mismatches
- rewrite preview diff parity
- richer AST JSON where the wrapper drops useful fields
- inline rules support
- scan/test workflow parity gaps
- broader feature-loss gaps reported in the comparison, where they are inside the promised surface

**Likely code surfaces**

- `rust_core/src/backend_ast.rs`
- `rust_core/src/backend_ast_workflow.rs`
- `rust_core/src/main.rs`
- `src/tensor_grep/backends/ast_backend.py`
- `src/tensor_grep/backends/ast_wrapper_backend.py`
- `src/tensor_grep/cli/ast_workflows.py`
- `src/tensor_grep/cli/main.py`
- `src/tensor_grep/cli/mcp_server.py`
- `src/tensor_grep/cli/apply_policy.py`

**Primary validation surfaces**

- `tests/unit/test_ast_backend.py`
- `tests/unit/test_ast_wrapper_backend.py`
- `tests/unit/test_ast_workflows.py`
- `tests/unit/test_cli_modes.py`
- `tests/unit/test_mcp_server.py`
- `tests/unit/test_apply_policy.py`
- `tests/integration/test_cross_backend.py`
- `tests/integration/test_harness_adoption.py`
- `rust_core/tests/test_ast_backend.rs`
- `rust_core/tests/test_schema_compat.rs`
- `rust_core/tests/test_smart_routing.rs`
- `benchmarks/run_ast_parity_check.py`

**Special rule**

This workstream starts with re-validation before redesign. Some reported AST gaps may already be partially fixed in the current line, especially where native diff preview, schema envelopes, or Rust typed-signature routing have already moved.

**Definition of done**

An AST-parity issue is done when:

- the exact parity shape is restated as a contract test
- the current line is re-validated before architecture changes are proposed
- wrapper, native, or MCP payload changes are isolated to the narrow contract under test
- AST performance is measured only after correctness is locked

## Workstream 3: Platform Compatibility

**Scope**

This workstream isolates platform-boundary issues that block parity even when the underlying search logic is otherwise correct.

Included backlog:

- Windows `cp1252` / terminal output safety for AST and CLI emission
- shell completion gaps
- any platform-specific CLI transport or encoding issue that is not itself a search-logic bug

**Likely code surfaces**

- `src/tensor_grep/cli/ast_workflows.py`
- `src/tensor_grep/cli/main.py`
- `rust_core/src/main.rs`
- completion-generation or packaging surfaces if they exist in the current CLI front door

**Primary validation surfaces**

- new targeted encoding regressions adjacent to CLI and AST workflow tests
- existing CLI-mode tests where output transport is exercised
- completion tests or snapshot tests, if the repo already has them, otherwise new narrow coverage

**Definition of done**

A platform issue is done when:

- the failing environment behavior is simulated in a targeted regression test
- the fix is limited to the output or platform boundary
- search semantics and performance are not bundled into the same slice

## Workstream 4: Performance Remediation

**Scope**

This workstream covers measured regressions and measured overhead only.

Included backlog:

- `-c` count-mode regression
- `--cpu` overhead relative to default mode and `rg`
- AST workflow overhead relative to `ast-grep`

**Likely benchmark surfaces**

- `benchmarks/run_benchmarks.py`
- `benchmarks/check_regression.py`
- `benchmarks/run_tool_comparison_benchmarks.py`
- `benchmarks/run_native_cpu_benchmarks.py`
- `benchmarks/run_ast_benchmarks.py`
- `benchmarks/run_ast_workflow_benchmarks.py`
- `benchmarks/run_ast_rewrite_benchmarks.py`

**Primary rules**

- no performance work without the governing artifact
- no correctness bugfix bundled into a performance slice unless the performance path is required to make the feature correct
- rejected candidates must be recorded in `docs/PAPER.md` if they matter to future decisions

**Definition of done**

A performance topic is done when one of these is true:

- an accepted benchmark improvement lands with the relevant artifact and any accepted docs updates
- the candidate is rejected and recorded as rejected history so the repo does not retry it blindly

## Backlog Mapping Rules

Every reported issue must map to exactly one workstream.

Each issue must end in one of three states:

1. fixed with validator-backed tests
2. intentionally documented as out of current product scope
3. rejected as a performance candidate with recorded benchmark evidence

No issue should remain in a vague “known problem” state once this program is complete.

## Slice Discipline

The program must follow the repo's slice rules.

**Good slices**

- one text-search correctness fix
- one AST wrapper parity fix
- one Windows encoding fix
- one benchmark-governed performance investigation
- one docs/governance update after an accepted result

**Bad slices**

- correctness plus performance in the same patch
- CLI parsing changes mixed with unrelated AST wrapper work
- platform encoding fixes mixed with output-contract rewrites
- benchmark-doc refreshes mixed with search-logic changes that are not yet accepted

When in doubt, split the change.

## Execution Model

The program should be executed with one coordinating thread and subagent-driven implementation at the slice level.

**Safe subagent usage**

- parallel context gathering across independent workstreams
- one fresh worker per narrow slice during implementation
- review between slices before the next slice lands

**Unsafe subagent usage**

- parallel edits to the same CLI contract surface
- simultaneous correctness and performance changes on the same route
- overlapping edits in `src/tensor_grep/cli/main.py` or equivalent front-door code without explicit serialization

The intended implementation pattern is:

1. coordinator selects one narrow slice
2. worker executes the TDD plan in isolation
3. coordinator reviews the result against the slice contract
4. narrow validation runs
5. full gates run if the slice is intended to land
6. benchmark runs only for performance slices

## Documentation and Release Discipline

**README**

Update only when the public contract changes or a previously broken promised shape is now intentionally restored and should be stated clearly.

**docs/PAPER.md**

Record accepted and rejected optimization history, and parity-remediation outcomes where they matter to future technical decisions.

**docs/benchmarks.md**

Update only if benchmark procedures, acceptance rules, or governed interpretation actually change.

**CHANGELOG.md**

Do not hand-edit for normal release flow under semantic-release.

**Validator-backed docs**

If public docs or release behavior change, move the validators and their tests together:

- `scripts/validate_release_assets.py`
- `tests/unit/test_release_assets_validation.py`

## Validation Model

Every execution plan produced from this program must include:

- the exact failing test entry point
- the exact file set expected to change
- narrow validation commands
- full-gate commands for landing slices
- the exact benchmark command for performance work
- whether docs must stay untouched, may need updates, or are blocked pending accepted artifacts

Normal code-change full gates remain:

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
```

Performance slices must use the benchmark harness that matches the changed path, not a convenient substitute.

## Success Criteria

The remediation program is complete when all of the following are true:

1. every item in the expanded parity backlog is assigned to exactly one workstream
2. every issue has a named test anchor and a named code surface
3. every landed slice is validator-backed and scope-correct
4. performance claims are tied to accepted artifacts only
5. public docs tell the truth about the resulting product line

## Non-Goals

This program does not authorize:

- broad architectural rewrites without a failing contract and a narrow slice
- changing the product claim to avoid fixing broken promised behavior
- relabeling performance regressions as acceptable without accepted artifacts
- hand-waving parity with snapshots or normalizers that mask real output differences

## Recommended Next Step

Write one execution-ready TDD plan per workstream:

1. text parity remediation
2. AST parity remediation
3. platform compatibility remediation
4. performance investigation and optimization remediation

Each plan should be implementation-ready for zero-context workers and should assume isolated worktrees, frequent small commits, and mandatory review between slices.
