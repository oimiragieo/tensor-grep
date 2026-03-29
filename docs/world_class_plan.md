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

Current accepted response-shape observability baseline:

* artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit1_trace_shape_trace.json`
* baseline response shape: `analysis_then_patch`
* enhanced response shape: `meta_question`
* enhanced details: `tg_invocation_count = 0`, `changed_file_count = 0`, `patch_chars = 0`
* accepted interpretation: a primary remaining bottleneck is prompt-level task non-engagement, not search cost

Next observability target:

* record time-to-first-useful-action in the A/B trace:
  * first `tg` call
  * first emitted patch
  * first file change
* accepted reason: response shape alone tells us what failed; first-action timing tells us how long the agent spent before doing something useful

Current accepted first-action timing baseline:

* artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit1_post_edit_final_trace.json`
* baseline:
  * `first_file_change_seconds = 0.094174`
  * `first_patch_seconds = 36.642202`
  * `first_tg_seconds = null`
  * `post_edit_deliberation_seconds = 36.548028`
* enhanced:
  * `response_shape = meta_question`
  * `first_file_change_seconds = null`
  * `first_patch_seconds = null`
  * `first_tg_seconds = null`
* accepted interpretation: once the task is engaged, the remaining latency is mostly post-edit deliberation / patch finalization; when the task is not engaged, the failure is prompt-level non-engagement rather than search cost

Current accepted optimization implication:

* do not spend the next batch on `tg` speed for this agent path
* do not spend the next batch on file-targeting heuristics
* the next candidate should target one of two things only:
  * reduce `meta_question` response shape frequency
  * reduce `post_edit_deliberation_seconds` when the task is already engaged

Rejected output-contract candidate:

* harness now supports `enhanced_output_contract = standard | terse` for controlled experiments
* first terse probe artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit1_terse_trace.json`
* result:
  * enhanced remained correct
  * but `post_edit_deliberation_seconds` increased to `102.634616`
  * total wall clock increased to `102.927492`
* accepted decision: keep the harness support, reject `terse` as the default enhanced contract

Rejected task-contract candidate:

* harness now supports `enhanced_task_contract = standard | engage` for controlled experiments
* first engage probe artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit1_engage_trace.json`
* result:
  * enhanced no longer returned a `meta_question`
  * enhanced produced the correct patch
  * but `post_edit_deliberation_seconds` still increased to `56.017607`
  * total wall clock increased to `56.423431`
* accepted decision: keep the harness support, reject `engage` as the default enhanced contract until a broader slice shows a net win

Accepted comparison-surface upgrade:

* harness now includes `benchmarks/run_claude_skill_ab_matrix.py`
* reporting surface now includes `benchmarks/render_claude_skill_ab_matrix.py`
* matrix runner now checkpoints after each experiment via an explicit helper and supports `--resume`
* it reuses:
  * `run_claude_skill_ab.py`
  * `run_patch_bakeoff.py`
* first real matrix artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit1_matrix.json`
* accepted broader matrix artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit3_matrix.json`
* accepted 3-task matrix read:
  * `standard/standard` is still the losing corner:
    * enhanced `patch_applied = 0.67`
    * enhanced `validation = 0.67`
    * enhanced `meta_question_rate = 0.33`
  * `standard/engage` succeeds:
    * enhanced `patch_applied = 1.0`
    * enhanced `post_edit_deliberation_seconds = 48.56`
  * `terse/standard` succeeds:
    * enhanced `patch_applied = 1.0`
    * enhanced `post_edit_deliberation_seconds = 61.83`
  * `terse/engage` is currently the cheapest successful corner on the broader slice:
    * enhanced `patch_applied = 1.0`
    * enhanced `post_edit_deliberation_seconds = 41.11`
* accepted decision:
  * keep current shipped default unchanged for now
  * stop shipping prompt-default changes from single probes
  * use the matrix harness for broader acceptance slices before changing defaults
  * use checkpointed matrix runs for broader slices so interrupted runs still leave a valid artifact

Rejected latency shortcut:

* candidate: tell the enhanced path to skip `tg` whenever the task prompt already names the target file
* measured result on a 1-task probe: runtime improved (`37.43s` baseline vs `10.62s` tightened enhanced)
* but correctness collapsed: tightened enhanced returned no patch and effectively reverted to a “what do you want me to do?” response
* accepted decision: reject this shortcut; keep explicit skill guidance intact until a traced multi-task run proves a safer speed win

The next proof step is not another generic patch heuristic. It is expanding the real patch corpus and keeping only runner changes that improve the expanded pack.

Near-term acceptance order:

1. run the Claude contract matrix on a 5-task slice
2. reject any candidate that loses correctness anywhere on that slice
3. if `terse/engage` remains the cheapest successful corner, promote it to the next default probe

## Next TDD Finish Plan

The next execution line to finish this codebase should be:

1. default-contract finish line
   * red:
     * run the checkpointed 5-task Claude contract matrix
     * treat any corner that loses a single task as rejected
   * green:
     * if `terse/engage` stays lossless and cheapest, add it as the next explicit default-probe mode
   * benchmark gate:
     * rerun the full 10-task Claude A/B and keep it only if correctness stays at least equal to the accepted enhanced baseline

2. broader corpus finish line
   * red:
     * add at least 5 more real repo-backed patch scenarios, biased toward multi-file and ambiguity cases
   * green:
     * keep only fixture expansions that pass the oracle gate cleanly
   * benchmark gate:
     * rerun Claude baseline vs enhanced on the expanded corpus

3. agent-speed finish line
   * red:
     * trace the promoted default probe on the 10-task pack
   * green:
     * reduce `meta_question_rate` to `0.0`
     * reduce post-edit deliberation without losing correctness
   * benchmark gate:
     * compare against the accepted `claude-enhanced` baseline, not memory

4. comparative finish line
   * rerun Copilot and Gemini on the current accepted real patch pack
   * rerun user-style A/B only after the Claude default probe is accepted
   * update scorecards only from completed, same-pack runs

5. precision and test-targeting finish line
   * use the expanded corpus failures to drive:
     * Python dependent-file precision
     * Rust test targeting when host support is available
   * reject any internal heuristic that does not move an end-to-end benchmark

The implementation rule for the next few batches is simple:

* tests first
* artifact-producing benchmark second
* docs last
* no default flip without a broader matrix win

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
3. explicit response-shape classification:
   * `meta_question`
   * `analysis_then_patch`
   * `direct_patch`
   * `analysis_only`
   * `empty`
4. first-action timing:
   * `first_tg_seconds`
   * `first_patch_seconds`
   * `first_file_change_seconds`
5. narrower agent prompt / skill experiments accepted only when they improve the traced A/B benchmark

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
  * response-shape failure modes
  * time-to-first-useful-action
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
