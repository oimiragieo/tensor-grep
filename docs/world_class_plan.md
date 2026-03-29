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
* the enhanced agent path is more accurate but still slower than the plain agent baseline
* observability is still incomplete at the command-decision level
* there is still no broad accepted end-to-end corpus beyond the current 10-scenario real patch pack

## TDD Execution Policy

The remaining work should be executed in a strict red/green/benchmark loop.

For every new feature or benchmark extension:

1. add or expand a real repo-backed failing fixture first
2. add or update the narrow oracle/unit test that proves the fixture and scorer are valid
3. implement the smallest runner, planner, or ranking change
4. rerun the narrow suite
5. rerun the relevant real benchmark pack
6. keep the change only if the real metric improves or the new fixture coverage is accepted intentionally

This matters because recent patch-runner work proved that some plausible changes regress real patch correctness even when unit tests stay green. The benchmark, not intuition, is the final acceptance gate.

The same rule now applies to skill and prompt changes:

1. add the narrow harness or trace test first
2. land the smallest prompt / skill / wrapper change
3. rerun the user-style A/B benchmark slice
4. reject any change that improves latency while regressing patch-applied or validation-pass rate

## Current Accepted Patch Benchmark State

Current accepted real patch benchmark baseline:

* hard real patch pack: `10` repo-backed scenarios
* oracle: validated by the fixture/oracle unit gate at `mean_patch_applied_rate = 1.0`, `mean_validation_pass_rate = 1.0`
* Claude direct-edit-first runner on the `10`-scenario pack: `mean_patch_applied_rate = 1.0`, `mean_validation_pass_rate = 1.0`
* Copilot comparative baseline: last full rerun remains the older `8`-scenario pack at `0.625 / 0.625`
* Gemini comparative baseline: last full rerun remains the older `8`-scenario pack at `0.0 / 0.0`

Current accepted user-style Claude A/B baseline:

* artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit10_bakeoff.json`
* `claude-baseline`: `mean_patch_applied_rate = 0.8`, `mean_validation_pass_rate = 0.8`, mean wall clock `26.67s`
* `claude-enhanced`: `mean_patch_applied_rate = 1.0`, `mean_validation_pass_rate = 1.0`, mean wall clock `45.65s`
* accepted interpretation: `tensor-grep` materially improves correctness for the current agent workflow, but not speed

Current accepted command-level observability baseline:

* artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit1_trace_with_tg_trace.json`
* traced probe shows `claude-enhanced` taking `24.64s` with `tg_invocation_count = 0`
* accepted interpretation: the first observed latency gap is at least partly Claude deliberation, not local harness overhead or `tg` runtime

Rejected latency shortcut:

* candidate: tell the enhanced path to skip `tg` whenever the task prompt already names the target file
* measured result on a 1-task probe: runtime improved (`37.43s` baseline vs `10.62s` tightened enhanced)
* but correctness collapsed: tightened enhanced returned no patch and effectively reverted to a “what do you want me to do?” response
* accepted decision: reject this shortcut; keep explicit skill guidance intact until a traced multi-task run proves a safer speed win

The next proof step is not another generic patch heuristic. It is expanding the real patch corpus and keeping only runner changes that improve the expanded pack.

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
* **Anthropic Claude Code docs / Skills guide**: project-local `CLAUDE.md` plus skills are the right integration surface for user-style agent experiments

## Milestone 0: Agent Observability And Contract Tightening

Goal:
Turn the current Claude A/B benchmark from an outcome-only score into a diagnostic instrument that shows where enhanced latency is coming from.

Deliverables:

1. trace artifacts for every user-style A/B run:
   * prompt size
   * changed-file count
   * Claude runtime
   * diff derivation time
   * `tg` invocation count
   * `tg` total runtime
2. command-level logging for real `tg` calls in the enhanced workspace
3. narrower agent prompt / skill experiments accepted only when they improve the traced A/B benchmark

Implementation notes:

* Current accepted tracing already proves that local repo-copy/setup overhead is negligible.
* The next step is not more generic prompt trimming; it is making the enhanced path call `tg` only when useful and avoiding the “ready to help” detour.
* Changes in this area should be tested against the user-style A/B harness first, not only the generic patch runner.

Acceptance:

* every A/B run emits a trace artifact by default
* trace data is sufficient to distinguish:
  * Claude deliberation cost
  * `tg` runtime cost
  * local harness cost
* no prompt/skill latency change is accepted if it regresses correctness on the A/B slice

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
* every new fixture must have an oracle-scored proof path before competitor numbers are accepted

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
* system-specific patch contracts are allowed if they win on the real pack

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

1. Milestone 0: Agent Observability And Contract Tightening
2. Milestone 1: Patch-Correctness Bakeoff
3. Milestone 2: Tensor-Grep Patch Driver
4. Milestone 3: Python Precision Program
5. Milestone 4: Rust Test Targeting
6. Milestone 5: Provider-Mode Hard Cases
7. Milestone 6: Agent-Facing Productization
8. Milestone 7: Final Comparative Benchmark

## Definition Of Done

We can call the tooling world-class only when all of the following are true:

1. planning benchmarks stay ahead on real repos
2. patch-correctness benchmarks exist and are reproducible
3. user-style agent A/B benchmarks show `tensor-grep`-enhanced flows beat generic agent baselines on final task outcomes, not just planning
4. Python precision gap is materially reduced
5. Rust test targeting is no longer a known weak point
6. provider-backed semantics are either proven useful or explicitly scoped as optional
7. observability is strong enough to explain any remaining speed/correctness tradeoff in the enhanced path
