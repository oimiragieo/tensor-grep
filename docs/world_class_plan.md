# World-Class AI Tooling Plan

This document turns the current `tensor-grep` position into a concrete implementation program. It is intentionally benchmark-driven. The goal is not more surface area; the goal is better final edit outcomes on real repositories.

## Current Position

Strong already:

* deterministic search and planning substrate
* trust metadata and graph provenance
* edit-plan and blast-radius quality
* external bakeoff harness
* competitor comparison harness
* bounded cross-language wins versus headless Gemini and Copilot
* real semantic-provider feature (`native | lsp | hybrid`)

Weak or unfinished:

* Python dependent-file precision is still the weakest internal metric
* Rust test targeting still trails
* `lsp` / `hybrid` do not currently improve benchmark outcomes
* current benchmarks are mostly planning-oriented, not final-patch-oriented
* no accepted end-to-end "agent uses tensor-grep to edit code and pass tests" harness yet

## External References To Reuse

We should borrow structure aggressively instead of rebuilding from scratch.

### Patch / benchmark harness references

1. **Aider SWE Bench harness**
   Repo: `https://github.com/Aider-AI/aider-swe-bench`
   Local reference clone: `C:\dev\projects\_tg_refs\aider-swe-bench`
   Reuse ideas:
   * `predictions/<run>/<instance_id>.json`
   * `all_preds.jsonl` aggregation
   * patch-centric evaluation flow
   * plausible-solution filtering before acceptance testing

2. **OpenHands Benchmarks**
   Repo: `https://github.com/OpenHands/benchmarks`
   Local reference clone: `C:\dev\projects\_tg_refs\OpenHands-benchmarks`
   Reuse ideas:
   * standardized benchmark layout
   * reproducible environment and logging
   * clean separation between benchmark definition and execution

3. **Agentless Lite**
   Repo: `https://github.com/sorendunn/Agentless-Lite`
   Local reference clone: `C:\dev\projects\_tg_refs\Agentless-Lite`
   Reuse ideas:
   * explicit `retrieve -> repair` split
   * precomputed retrieval contexts
   * simple patch-generation loop that is cheap to benchmark

### Test-selection references

4. **pytest-ekstazi**
   Repo: `https://github.com/Igorxp5/pytest-ekstazi`
   Local reference clone: `C:\dev\projects\_tg_refs\pytest-ekstazi`
   Reuse ideas:
   * dependency-aware regression test selection
   * persisted dependency metadata per test

5. **snob**
   Repo: `https://github.com/alexpasmantier/snob`
   Local reference clone: `C:\dev\projects\_tg_refs\snob`
   Reuse ideas:
   * static import-graph-based impacted-test narrowing
   * explicit "run all tests on change" escape hatches

### Research references guiding the plan

* **RepoGraph**: repository-level graphs improve software engineering retrieval
* **RANGER**: graph-enhanced repository retrieval improves planning
* **ContextBench** / **SWE Context Bench**: retrieval quality is a benchmark axis in its own right
* **Agentless** / **Agentless Lite**: strong SWE performance can come from a retrieval/repair scaffold rather than a heavy interactive loop
* **SWE-bench Goes Live**, **OmniCode**, **FeatureBench**, **GitTaskBench**: world-class claims require end-to-end task completion, not only localized retrieval

## Milestone 1: Patch-Correctness Bakeoff

Goal:
Measure final edit quality, not just planning quality.

Deliverables:

1. `benchmarks/run_patch_bakeoff.py`
2. patch scenario schema with:
   * `instance_id`
   * `repo_fixture`
   * `problem_statement`
   * `expected_files`
   * `expected_spans`
   * `expected_tests`
   * `expected_validation_commands`
   * optional `gold_patch`
3. result artifact with:
   * `patch_applied`
   * `tests_passed`
   * `validation_passed`
   * `expected_file_recall`
   * `unexpected_files_touched`
   * `patch_similarity` or exactness metric
   * `wall_clock_seconds`
   * `turn_count`

Implementation notes:

* Mirror the `prediction -> aggregate -> evaluate` shape from `aider-swe-bench`
* Keep the acceptance/evaluation phase separate from generation
* Reuse current external scenario packs as seed inputs before moving to larger SWE-style datasets

Acceptance:

* deterministic artifact format
* can evaluate `tensor-grep` planning + external patch application loop
* can later ingest competitor-produced patches without changing the schema

## Milestone 2: Tensor-Grep Patch Driver

Goal:
Give the benchmark a repeatable "agent using tensor-grep" execution path.

Deliverables:

1. a small patch driver that:
   * calls `tensor-grep` planning surfaces
   * materializes a constrained edit prompt or edit contract
   * applies edits through a selected model/backend
   * runs validation and records outcomes
2. machine-readable patch prediction output compatible with Milestone 1

Implementation notes:

* Follow the `retrieve -> repair` split from Agentless Lite
* Keep retrieval/planning deterministic; do not bury the main signal inside an opaque chat loop
* Store the exact context bundle used for each patch attempt

Acceptance:

* same scenario produces reproducible retrieval/planning artifacts
* patch attempts can be replayed and audited

## Milestone 3: Python Precision Program

Goal:
Close the main remaining internal quality gap.

Deliverables:

1. expanded Python external scenario packs
2. miss taxonomy by symbol class:
   * exceptions
   * stream helpers
   * formatting helpers
   * compat layers
   * package entrypoints
3. targeted ranking fixes with failing tests first

Implementation notes:

* Keep using the current `click` miss-analysis approach
* Prefer class-aware penalties and ranking features over blunt graph cutoffs
* Accept only changes that preserve primary hit rate

Acceptance:

* Python external precision improves beyond the current `0.7275`
* no regression in `mean_file_hit_rate`

## Milestone 4: Rust Test Targeting

Goal:
Fix the remaining weak cross-language quality signal.

Deliverables:

1. improved Rust test association logic
2. explicit handling for:
   * `#[test]`
   * `#[tokio::test]`
   * module-scoped test layouts
   * cargo workspace package/test relationships
3. benchmark scenarios that measure Rust test selection directly

Implementation notes:

* Borrow dependency-aware selection ideas from `pytest-ekstazi` and `snob`
* Keep the selection explainable and provenance-tagged

Acceptance:

* Rust test-hit metrics improve from the current zero baseline on the external slice

## Milestone 5: Provider-Mode Hard Cases

Goal:
Only keep pushing `lsp` / `hybrid` where they can earn a measurable win.

Deliverables:

1. dedicated hard-semantic scenario packs where native resolution is plausibly weak:
   * Python import alias ambiguity
   * JS/TS path alias and re-export chains
   * Rust module/use-chain ambiguity
2. provider-mode benchmark report:
   * `native`
   * `lsp`
   * `hybrid`
3. explicit default-mode decision per scenario class

Implementation notes:

* Do not spend more time on provider plumbing unless these cases show a quality gain
* Provider-backed modes stay opt-in until proven

Acceptance:

* at least one hard scenario class where `hybrid` beats `native` enough to justify its cost
* otherwise freeze provider modes as experimental

## Milestone 6: Agent-Facing Productization

Goal:
Make `tensor-grep` the best retrieval/planning backend for agents.

Deliverables:

1. stable end-to-end machine-readable contracts for:
   * plan
   * patch attempt
   * validation run
   * final score
2. concise CLI/MCP recipes for the common agent workflow:
   * defs -> refs -> blast-radius -> plan -> validation
3. explicit failure modes and recommended retries

Implementation notes:

* Do not add more commands unless they simplify the agent workflow materially
* Optimize for low-noise, tool-friendly outputs

Acceptance:

* an external agent can consume the contracts without prompt-specific glue code

## Milestone 7: Final Comparative Benchmark

Goal:
Support a defensible “world-class” claim.

Deliverables:

1. final scorecard combining:
   * planning metrics
   * patch correctness metrics
   * validation success
   * context efficiency
   * wall-clock
2. comparison runs for at least:
   * `tensor-grep`-driven patch flow
   * Gemini
   * Copilot
   * any other stable comparator worth keeping

Acceptance:

* `tensor-grep` is ahead on the majority of real-repo task slices
* caveats remain explicit when a comparator wins a specific class

## Build Order

The right execution order is:

1. Milestone 1: Patch-Correctness Bakeoff
2. Milestone 2: Tensor-Grep Patch Driver
3. Milestone 3: Python Precision Program
4. Milestone 4: Rust Test Targeting
5. Milestone 5: Provider-Mode Hard Cases
6. Milestone 6: Agent-Facing Productization
7. Milestone 7: Final Comparative Benchmark

## Definition Of Done

We can call the tooling world-class only when all of the following are true:

1. planning benchmarks stay ahead on real repos
2. patch-correctness benchmarks exist and are reproducible
3. `tensor-grep`-driven edit flows beat generic agent baselines on final task outcomes, not just planning
4. Python precision gap is materially reduced
5. Rust test targeting is no longer a known weak point
6. provider-backed semantics are either proven useful or explicitly scoped as optional
