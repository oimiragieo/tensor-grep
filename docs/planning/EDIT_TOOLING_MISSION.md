# Edit Tooling Mission (2026-03-20)

This mission defines the next material product step for `tensor-grep` after the search-speed and AST/rewrite wins.

## Goal

Make `tensor-grep` a stronger AI editing substrate by adding the missing reliability/context features that the current best editing tools rely on:

- repository-aware context selection
- selective edit acceptance
- validation-driven repair loops
- explicit rollback/checkpoint mechanics
- symbol-aware navigation

The current codebase already has:

- fast text search
- fast AST search
- rewrite plan / diff / apply / verify
- overlap rejection
- stale-file rejection
- JSON / NDJSON / MCP contracts

The next gains are not more search kernels. The next gains are **edit accuracy and edit control**.

## Research Summary

Current external signal points to the following:

1. **Claude Code** is currently the strongest closed product for autonomous multi-file edits.
   - Strength: edit accuracy on larger tasks.
   - Weakness: review / control UX is more divisive.

2. **Aider** is the strongest open-source reference for editing workflow design.
   - Repository map
   - Git-aware safety model
   - Auto-lint / auto-test repair loop
   - Multiple edit application strategies

3. **OpenHands** is the strongest open-source reference for larger agent runtime/orchestration, not the tightest edit loop.

4. Recent research shifts the target from one-shot patch generation to **repository-grounded, validation-backed editing**:
   - SWE-CI: CI-loop maintainability matters more than one-shot patch pass rate.
   - SWE-PolyBench: repository-level evaluation across languages matters.
   - Clean-PR / PR-derived edit-block work: validated search/replace/edit-block representations are strong substrates.

This mission should copy the right things:

- from Aider: repo map, validation loop, safety/undo
- from OpenHands: workflow integration and runtime contracts
- from current benchmarks/papers: CI-style acceptance and repo-level evaluation

## Product Position

The target is not "be a chat IDE."

The target is:

- the fastest search + structural search front-end
- with a deterministic, inspectable, machine-readable edit substrate
- that an external coding agent can trust for plan / review / apply / verify

## Non-Goals

Do not do these in this mission:

- no speculative GPU edit features
- no broad LLM orchestration framework inside `tensor-grep`
- no editor UI work
- no benchmark claims without new measured evidence
- no contract-breaking CLI/MCP changes without validator-backed tests

## Current Relevant Surfaces

These are the current edit surfaces this mission builds on:

- `rust_core/src/backend_ast.rs`
  - rewrite plans
  - edit IDs
  - overlap rejection
  - stale-file detection
  - verify results
  - unified diff generation
- `rust_core/src/main.rs`
  - `tg run`
  - `--rewrite`
  - `--diff`
  - `--apply`
  - `--verify`
  - `--batch-rewrite`
- `src/tensor_grep/cli/mcp_server.py`
  - `tg_index_search`
  - `tg_rewrite_plan`
  - `tg_rewrite_apply`
  - `tg_rewrite_diff`
- `docs/harness_api.md`
  - current public machine-facing contract
- `docs/harness_cookbook.md`
  - current plan -> diff -> apply+verify workflow

## Milestones

### M1. Repo Map and Context Pack

**Goal:** Give agents the minimum repo-grounded context needed to choose edits accurately.

**User-facing additions**

- `tg map --json [PATH]`
- `tg context --query <pattern> --json [PATH]`
- MCP equivalents:
  - `tg_repo_map`
  - `tg_context_pack`

**Output shape**

`tg map --json` should return:

- `version`
- `routing_backend`
- `routing_reason`
- `sidecar_used`
- `files`
- `symbols`
- `imports`
- `tests`
- `related_paths`

`tg context --query ... --json` should return:

- the above, plus ranked files/symbols related to the query
- optional search hits used to justify the pack

**Implementation notes**

- Reuse existing AST / search infrastructure.
- Start with a deterministic symbol extractor, not an LLM planner.
- Use language-specific AST traversal where already available.
- Prefer rankable, machine-readable outputs over prose summaries.

**TDD slices**

1. RED: contract tests for `tg map --json` and MCP repo-map tool payloads.
2. GREEN: minimal symbol/file inventory for one language.
3. RED: multi-language fixture tests.
4. GREEN: add imports/tests/related-path heuristics.
5. REFACTOR: extract a shared repo-map module and cache.

**Acceptance**

- repo-map payloads are deterministic
- outputs are schema-tested
- one real harness fixture can consume them without private APIs

### M2. Selective Apply / Reject by Edit ID

**Goal:** Let agents or humans accept only a subset of planned edits.

**User-facing additions**

- `tg run ... --apply-edit-ids <id1,id2,...>`
- `tg run ... --reject-edit-ids <id1,id2,...>`
- JSON/MCP payloads preserve the original full plan and the filtered applied subset

**Why**

Current plan/diff/apply is all-or-nothing at the command level. Good editing systems allow review and partial acceptance.

**TDD slices**

1. RED: rewrite plan/apply tests for filtered edit subsets.
2. GREEN: filter planned edits before overlap/apply phase.
3. RED: invalid edit-id contract tests.
4. GREEN: clear error payloads for unknown/duplicate/conflicting IDs.
5. REFACTOR: shared selection helper for single and batch rewrite flows.

**Acceptance**

- partial apply works deterministically
- rejected edit IDs never touch disk
- stale-file and overlap protections still hold under filtering

### M3. Post-Edit Validation Loop

**Goal:** Make `tensor-grep` useful as a closed-loop edit executor, not only a patch emitter.

**User-facing additions**

- `tg run ... --apply --verify --lint-cmd <cmd> --test-cmd <cmd> --json`
- MCP apply tool can optionally request lint/test validation

**Output shape additions**

- `lint_result`
- `test_result`
- `diagnostics`
- `exit_code`
- `validated` boolean

**Why**

This is the biggest practical upgrade for AI editing accuracy. SWE-CI makes this non-optional if the tool is meant to operate on real repositories.

**TDD slices**

1. RED: integration tests with fixture repos and fake lint/test runners.
2. GREEN: run validation commands after apply+verify.
3. RED: failure-path contract tests with captured diagnostics.
4. GREEN: machine-readable diagnostic envelopes.
5. REFACTOR: reusable validation runner for CLI and MCP.

**Acceptance**

- validation failures are structured and parseable
- successful validation is explicit
- no ambiguous stdout scraping required by harness consumers

### M4. Checkpoint / Undo Contract

**Goal:** Add an explicit rollback story for edit sessions.

**User-facing additions**

- `tg checkpoint create --json`
- `tg checkpoint list --json`
- `tg checkpoint undo <id> --json`
- optional `--checkpoint` on apply operations

**Why**

Aider’s git-aware safety model is strong because it gives users a clean undo story. `tensor-grep` needs an explicit contract instead of expecting every harness to invent one.

**Implementation options**

- Prefer Git-backed checkpoints when inside a Git repo.
- Fall back to explicit patch snapshots outside Git.
- The contract must make the mode visible in JSON.

**TDD slices**

1. RED: checkpoint lifecycle tests in a temp repo and non-git temp dir.
2. GREEN: create/list/undo checkpoints.
3. RED: rollback-on-stale/partial-failure tests.
4. GREEN: checkpoint metadata in apply payloads.
5. REFACTOR: isolate checkpoint backend abstraction.

**Acceptance**

- every checkpoint has a stable ID
- undo is deterministic
- Git and non-Git behavior are explicit in output

### M5. Symbol-Aware Navigation

**Goal:** Improve edit targeting beyond raw grep hits.

**User-facing additions**

- `tg defs <symbol> --json`
- `tg refs <symbol> --json`
- `tg impact <file-or-symbol> --json`

**Why**

Your own paper already points to stack graphs / symbol-aware navigation as the next meaningful edit-accuracy substrate.

**TDD slices**

1. RED: fixture repo tests for defs/refs on supported languages.
2. GREEN: minimal language support using current AST data.
3. RED: impact payload tests combining defs/refs/import edges.
4. GREEN: rank related files/tests.
5. REFACTOR: share navigation graph with repo-map/context pack.

**Acceptance**

- agents can ask for defs/refs/impact on public surfaces only
- outputs are deterministic enough to drive context selection

### M6. Session / Daemon Mode

**Goal:** Remove repeated startup/context rebuild cost in edit-heavy harness loops.

**User-facing additions**

- `tg serve` or `tg session`
- reuses AST caches, repo map, and validation config
- exposes the same public contract shapes over a long-lived process

**Why**

Once search is fast, repeated process startup and repeated context reconstruction become the next bottlenecks.

**TDD slices**

1. RED: session lifecycle tests.
2. GREEN: in-process command serving with stable envelopes.
3. RED: cache invalidation tests on file changes.
4. GREEN: AST/repo-map cache reuse.
5. REFACTOR: unify CLI/MCP/session backend selection.

**Acceptance**

- repeated edit loops are faster than repeated one-shot CLI calls
- no contract drift between one-shot and session modes

## Recommended Execution Order

1. M1 Repo Map and Context Pack
2. M2 Selective Apply / Reject by Edit ID
3. M3 Post-Edit Validation Loop
4. M4 Checkpoint / Undo Contract
5. M5 Symbol-Aware Navigation
6. M6 Session / Daemon Mode

This order is intentional:

- M1 improves edit selection accuracy
- M2 improves edit control
- M3 improves edit correctness after mutation
- M4 adds recoverability
- M5 deepens repo awareness
- M6 improves latency for real harness loops

## Benchmark and Validation Rules

### For edit-path correctness changes

Run:

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
cargo test --verbose --no-default-features
```

### For edit-path performance changes

Run the relevant benchmark, not all of them blindly.

AST search changes:

```powershell
python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
```

AST workflow orchestration changes:

```powershell
python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
```

Rewrite planning/apply changes:

```powershell
python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite.json
```

Control-plane changes that affect CLI startup:

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
```

### Required contract updates

If a milestone adds or changes public machine-facing behavior, update together:

- `docs/harness_api.md`
- `docs/harness_cookbook.md`
- committed JSON example artifacts in `docs/examples/`
- validator-backed tests

## First Slice To Implement

Start with **M1 Slice 1: repo-map contract tests**.

Why this slice first:

- it is the highest-leverage missing feature
- it improves both autonomous agent editing and human review workflows
- it does not require changing the core rewrite safety contract first
- it creates a foundation that later milestones can reuse

### First RED tests

Add failing tests for:

- `tg map --json` returns a stable envelope
- MCP `tg_repo_map` returns the same envelope shape
- one small fixture repo produces:
  - file inventory
  - symbol inventory
  - related test file inventory

### Smallest GREEN implementation

- Python CLI stub wired to existing AST/search backends
- one-language symbol extraction if necessary
- deterministic JSON output

Do not add ranking, graph scoring, or complex heuristics in the first green step.

## Success Definition

This mission succeeds when:

1. agents can get a repo-grounded context pack from `tensor-grep` directly
2. agents can partially accept or reject planned edits by stable ID
3. agents can run post-edit lint/test validation and receive structured diagnostics
4. agents can checkpoint and undo edit sessions through public contracts
5. agents can query symbol-aware navigation on public surfaces
6. repeated edit loops can reuse process state without breaking current CLI/MCP contracts

## Final Constraint

Do not market any of this as "better editing" until the public contract is stable and at least one real external AI codebase uses the workflow successfully.
