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
* `lsp` / `hybrid` are still not a default win on the broad planning packs
* the enhanced agent path is more accurate but still slower than the plain agent baseline
* observability is still incomplete at the command-decision level
* there is still no broad accepted end-to-end corpus beyond the current 12-scenario real patch pack
* trust artifacts were still carrying one avoidable CI risk: review bundles created from the same inputs could drift in `created_at` and `bundle_sha256`

Recently accepted:

* review bundle creation is now deterministic for identical inputs
* bundle `created_at` is now derived from packaged artifact timestamps instead of wall-clock creation time when possible
* accepted reason:
  * this removes a real flaky comparison path in `test_review_bundles.py`
  * it makes bundle hashes stable enough for trust and replay workflows
* focused Python precision batch:
  * stable artifact:
    * `artifacts/bench_bakeoff_click_precision_rerun.json`
    * `artifacts/bench_bakeoff_click_precision_rerun_analysis.md`
  * `click` bakeoff mean file precision improved from `0.7275` to `1.0`
  * false-positive scenario count dropped from `6` to `0`
  * accepted mechanism:
* focused provider hard-case batch:
  * stable artifact:
    * `artifacts/bench_provider_navigation_click_hardcases.json`
    * `artifacts/bench_provider_navigation_click_hardcases.md`
  * on the new 2-scenario Click-style Python alias-wrapper pack:
    * `native` mean caller hit rate = `0.0`
    * `hybrid` mean caller hit rate = `1.0`
    * `hybrid` mean caller precision = `1.0`
    * test hit rate stayed `1.0` for both modes
  * accepted mechanism:
    * provider modes now expand Python import/assignment alias chains for caller recovery
    * when external provider refs are absent, `lsp` / `hybrid` fall back to the same alias-chain recovery on Python files in provider mode only
  * accepted read:
    * provider-backed modes still are not the broad default
    * but Milestone 5 now has a real hard-semantic win where `hybrid` beats `native`
* focused JS/TS provider hard-case batch:
  * stable artifact:
    * `artifacts/bench_provider_navigation_js_ts_hardcases.json`
    * `artifacts/bench_provider_navigation_js_ts_hardcases.md`
  * on the new 2-scenario JS/TS imported-alias wrapper pack:
    * `native` mean caller hit rate = `0.0`
    * `hybrid` mean caller hit rate = `1.0`
    * `hybrid` mean caller precision = `1.0`
    * test hit rate stayed `1.0` for both modes
  * accepted mechanism:
    * provider modes now recover JS/TS imported-alias wrappers through one-hop local rebinding chains
    * when external provider refs are absent, `lsp` / `hybrid` fall back to the same alias-chain recovery on JS/TS files in provider mode only
* focused Rust provider hard-case batch:
  * stable artifact:
    * `artifacts/bench_provider_navigation_rust_hardcases.json`
    * `artifacts/bench_provider_navigation_rust_hardcases.md`
    * `artifacts/bench_provider_navigation_hardcases_combined.md`
  * on the new 2-scenario Rust use/re-export alias-wrapper pack:
    * `native` mean caller hit rate = `0.0`
    * `hybrid` mean caller hit rate = `1.0`
    * `hybrid` mean caller precision = `1.0`
    * test hit rate stayed `1.0` for both modes
  * accepted mechanism:
    * provider modes now recover Rust `use ... as` chains and one-hop `pub use` re-export alias chains through local rebinding before call
    * when external provider refs are absent, `lsp` / `hybrid` fall back to the same alias-chain recovery on Rust files in provider mode only
    * for Python `utils.py`, `termui.py`, and `core.py` symbols, once depth-one dependent files exist, depth-two-or-worse graph-only spillover is pruned from edit-plan dependency ranking
  * accepted read:
    * the old click precision loss was not a missing-graph problem
    * it was selective-retrieval failure caused by keeping low-value spillover after the relevant depth-one surface was already covered
* focused Rust test-targeting batch:
  * stable artifact:
    * `artifacts/bench_bakeoff_clap_lex_rust_targeting_rerun.json`
  * focused `clap_lex` rerun improved Rust `mean_test_hit_rate` from `0.0` to `1.0`
  * `mean_validation_cmd_hit_rate` remained `1.0`
  * accepted mechanism:
    * Rust test association now recognizes fully qualified symbol usage, inherent-`impl` method usage, and owner-type inheritance for method symbols in nested integration tests
    * blast-radius filtering now keeps `test-graph` and filename-backed test matches instead of dropping them
  * accepted read:
    * the old Rust miss was not a Cargo command-generation problem
    * it was a test-association problem, especially for nested `tests/testsuite/*.rs` layouts, method-driven assertions, and methods whose tests only referenced the owning type
* release/docs preflight hardening:
  * CI `release-readiness` now builds the docs site with `mkdocs build --strict` before asset validation
  * release `publish-docs` now performs a strict `mkdocs build --strict` before `mkdocs gh-deploy --force`
  * validator-backed reason:
    * this closes a real tag-time release risk where docs publication could fail or publish a broken site without an explicit preflight build contract
  * acceptance surface:
    * `.github/workflows/ci.yml`
    * `.github/workflows/release.yml`
    * `scripts/validate_release_assets.py`
    * `tests/unit/test_release_assets_validation.py`
* benchmark-doc contract hardening:
  * `scripts/validate_release_assets.py` now validates `docs/benchmarks.md` directly
  * validator-backed reason:
    * `docs/benchmarks.md` is the canonical benchmark surface in the README and release story, so GA readiness requires that file to drift only under test
    * the release validator now enforces the benchmark matrix, artifact-convention section, and acceptance-rule section
  * acceptance surface:
    * `docs/benchmarks.md`
    * `scripts/validate_release_assets.py`
    * `tests/unit/test_release_assets_validation.py`

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

* hard real patch pack: `12` repo-backed scenarios
* oracle: validated by the fixture/oracle unit gate at `mean_patch_applied_rate = 1.0`, `mean_validation_pass_rate = 1.0`
* Claude direct-edit-first runner on the accepted earlier `10`-scenario pack: `mean_patch_applied_rate = 1.0`, `mean_validation_pass_rate = 1.0`
* Copilot comparative baseline: accepted same-pack `12`-scenario rerun now exists at `0.5 / 0.5`
* Gemini comparative baseline: accepted same-pack `12`-scenario baseline rerun now exists at `0.0 / 0.0`
* accepted same-pack cross-system scorecard now exists:
  * `artifacts/patch_eval_demo/real_patch_system_scorecard.md`
  * current refreshed lines:
    * `claude-enhanced`: `1.0 / 1.0`
    * `claude-baseline`: `0.75 / 0.75`
    * `copilot`: `0.5 / 0.5`
    * `gemini-baseline`: `0.0 / 0.0`
    * `gemini-cli`: `0.0 / 0.0`
    * `gemini-enhanced`: `0.0 / 0.0`
  * current accepted line:
    * `claude-enhanced`: `1.0 / 1.0`
    * `copilot`: `0.5 / 0.5`
    * `gemini-cli` baseline: `0.0 / 0.0`

Current accepted user-style Claude A/B baseline:

* artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit12_current_claude_md_bakeoff.json`
* `claude-baseline`: `mean_patch_applied_rate = 0.75`, `mean_validation_pass_rate = 0.75`, mean wall clock `29.89s`
* `claude-enhanced`: `mean_patch_applied_rate = 1.0`, `mean_validation_pass_rate = 1.0`, mean wall clock `52.59s`
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

Probe-only task-contract candidate:

* harness now supports `enhanced_task_contract = act` plus named profile `--enhanced-contract-profile probe-standard-act`
* first act probe artifact:
  * `artifacts/patch_eval_demo/claude_skill_ab_limit1_probe_standard_act.json`
  * scored probe:
    * `artifacts/patch_eval_demo/claude_skill_ab_limit1_probe_standard_act_bakeoff.json`
* result on the 1-task `click-format-filename-shorten` probe:
  * enhanced engaged the task and produced the correct patch
  * enhanced `wall_clock_seconds = 36.039562`
  * enhanced `post_edit_deliberation_seconds = 35.750695`
  * enhanced still made `tg_invocation_count = 0`
* accepted read:
  * this is better than the earlier 1-task `engage` probe on pure latency
  * but it is still only a single-task probe and still shows answer-finalization cost rather than search cost
  * do not promote it as a default until a broader accepted slice shows both:
    * no correctness loss
    * a real mean wall-clock win on the accepted corpus

Rejected effort candidate:

* harness now supports enhanced-only Claude effort control via `--enhanced-effort`
* matrix runner now supports `--enhanced-efforts` so effort can be benchmarked as a first-class A/B dimension
* first broader effort artifact:
  * `artifacts/patch_eval_demo/claude_skill_ab_limit3_act_effort_matrix.json`
  * rendered scorecard:
    * `artifacts/patch_eval_demo/claude_skill_ab_limit3_act_effort_matrix.md`
* result on the 3-scenario `act` slice:
  * `output-standard__task-act__effort-default`
    * enhanced `mean_patch_applied_rate = 1.0`
    * enhanced `mean_validation_pass_rate = 1.0`
    * enhanced `mean_post_edit_deliberation_seconds = 40.431016`
  * `output-standard__task-act__effort-low`
    * enhanced `mean_patch_applied_rate = 0.333333`
    * enhanced `mean_validation_pass_rate = 0.333333`
    * enhanced `mean_post_edit_deliberation_seconds = 32.060561`
* accepted decision:
  * reject low effort as the default enhanced latency optimization
  * it reduces post-edit time on this slice, but the correctness collapse is too large to accept
  * keep the harness support because effort is now measurable and may still matter on future slices or with different contracts/models

Rejected minimal-output candidate:

* harness now supports `enhanced_output_contract = done`
* the contract keeps task engagement fixed and only changes the final response target:
  * after editing files directly, respond with exactly `DONE`
* first broader artifact:
  * `artifacts/patch_eval_demo/claude_skill_ab_limit3_act_done_matrix.json`
  * rendered scorecard:
    * `artifacts/patch_eval_demo/claude_skill_ab_limit3_act_done_matrix.md`
* result on the 3-scenario `act` slice:
  * `output-standard__task-act__effort-default`
    * enhanced `mean_patch_applied_rate = 1.0`
    * enhanced `mean_validation_pass_rate = 1.0`
    * enhanced `mean_first_patch_seconds = 38.056327`
    * enhanced `mean_post_edit_deliberation_seconds = 37.99349`
  * `output-done__task-act__effort-default`
    * enhanced `mean_patch_applied_rate = 1.0`
    * enhanced `mean_validation_pass_rate = 1.0`
    * enhanced `mean_first_patch_seconds = 45.824802`
    * enhanced `mean_post_edit_deliberation_seconds = 45.778454`
* accepted decision:
  * reject `done` as the default enhanced output contract
  * the positive minimal-output target preserved correctness but made latency worse than the current `standard` control
  * keep the harness support because it is now benchmarkable and documents another failed latency path that should not be retried blindly

Accepted comparison-surface upgrade:

* harness now includes `benchmarks/run_claude_skill_ab_matrix.py`
* reporting surface now includes `benchmarks/render_claude_skill_ab_matrix.py`
* matrix runner now checkpoints after each experiment via an explicit helper and supports `--resume`
* user-style Claude A/B runner now also checkpoints and resumes at record granularity
* Copilot and Gemini patch prediction runners now also checkpoint and resume at record granularity
* Claude and Gemini A/B resume semantics are now stricter: an `instance_id` only counts as complete when its full expected row set is present, so interrupted partial rows no longer cause false skips on broader reruns
* Gemini's Windows timeout path is now bounded closely enough to the configured timeout to make resumed same-pack reruns practical
* Gemini benchmark runs now use an isolated `.gemini` home that preserves auth but strips user-global `GEMINI.md` memory and MCP server config
* repo now includes an official-shape Gemini project setup:
  * root `GEMINI.md`
  * `.gemini/skills/tensor-grep/SKILL.md`
  * `.gemini/skills/tensor-grep/REFERENCE.md`
* repo now also includes a Gemini baseline-vs-enhanced A/B harness:
  * `benchmarks/run_gemini_skill_ab.py`
  * it can now optionally score its own A/B records against `run_patch_bakeoff.py` when given the same patch bakeoff scenario pack, so broader Gemini-enhanced reruns land as comparable scored artifacts rather than raw rows only
* it reuses:
  * `run_claude_skill_ab.py`
  * `run_patch_bakeoff.py`
* first real matrix artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit1_matrix.json`
* accepted broader matrix artifact: `artifacts/patch_eval_demo/claude_skill_ab_limit5_matrix.json`
* accepted 5-task matrix read:
  * `standard/standard` is still the losing corner:
    * enhanced `patch_applied = 0.60`
    * enhanced `validation = 0.60`
    * enhanced `meta_question_rate = 0.20`
  * `standard/engage` succeeds:
    * enhanced `patch_applied = 1.0`
    * enhanced `post_edit_deliberation_seconds = 37.02`
  * `terse/standard` regresses:
    * enhanced `patch_applied = 0.80`
    * enhanced `post_edit_deliberation_seconds = 47.19`
  * `terse/engage` still succeeds:
    * enhanced `patch_applied = 1.0`
    * enhanced `post_edit_deliberation_seconds = 46.46`
* accepted decision:
  * keep current shipped default unchanged for now
  * stop shipping prompt-default changes from single probes
  * use the matrix harness for broader acceptance slices before changing defaults
  * use checkpointed matrix runs for broader slices so interrupted runs still leave a valid artifact
  * expose the broader-slice winner as the next explicit probe profile, not as a silent default flip

Accepted GA-readiness hardening:

* `scripts/validate_release_assets.py` now also validates the public README contract
* accepted reason:
  * release/workflow/package-manager docs were already validator-backed
  * the top-level product entrypoint was not
  * this created a real drift risk between public product claims and the canonical docs/release surface
* enforced README markers now include:
  * canonical doc links for benchmarks, GPU crossover, routing policy, harness API, and harness cookbook
  * explicit links to `docs/installation.md` and `docs/RELEASE_CHECKLIST.md`
  * explicit first-class platform support wording
* accepted read:
  * this is not a benchmark win
  * it is a GA-style contract hardening pass so buyer-facing and operator-facing docs are now locked to the same validated source-of-truth surface as release automation

Rejected default-promotion probe:

* probe profile: `--enhanced-contract-profile probe-standard-engage`
* full-pack artifact:
  * `artifacts/patch_eval_demo/claude_skill_ab_limit10_probe_standard_engage.json`
  * `artifacts/patch_eval_demo/claude_skill_ab_limit10_probe_standard_engage_bakeoff.json`
* result:
  * `claude-enhanced` preserved correctness:
    * `patch_applied = 1.0`
    * `validation = 1.0`
  * but latency regressed versus the accepted enhanced baseline:
    * probe `mean wall_clock_seconds = 46.590416`
    * accepted baseline `mean wall_clock_seconds = 45.64775`
* accepted decision:
  * keep `probe-standard-engage` available as an explicit profile
  * reject it as the default enhanced contract
  * do not promote a new default until a full 10-task run shows both:
    * no correctness loss
    * a real latency win
  * within the currently explored prompt/effort/output-contract space, the latency program is now effectively frozen:
    * `probe-standard-engage` rejected
    * low effort rejected
    * `done` output contract rejected
    * keep the current accepted enhanced line and treat further speed work as a larger architectural problem, not another small prompt tweak

Accepted corpus expansion:

* the hard real patch pack now includes `12` committed scenarios
* two new validator-backed fixtures landed:
  * `click-choice-invalid-message`
  * `commander-use-color-env-conventions`
* both direct fixture tests fail in the intended historical state
* the oracle scorer test passes on the expanded pack
* same-pack Claude A/B rerun on the full 12-scenario corpus now exists and remains the accepted user-style line
* the first same-pack rerun exposed one enhanced miss:
  * `click-choice-invalid-message`
* the accepted follow-up fix was a tighter generated `CLAUDE.md` task-engagement rule
* the current accepted enhanced line on the full 12-scenario corpus is back to `1.0 / 1.0`
* baseline still misses:
  * `click-unstyle-other-ansi`
  * `commander-invalid-argument-error-code`
  * `click-style-non-text-coercion`

Rejected latency shortcut:

* candidate: tell the enhanced path to skip `tg` whenever the task prompt already names the target file
* measured result on a 1-task probe: runtime improved (`37.43s` baseline vs `10.62s` tightened enhanced)
* but correctness collapsed: tightened enhanced returned no patch and effectively reverted to a “what do you want me to do?” response
* accepted decision: reject this shortcut; keep explicit skill guidance intact until a traced multi-task run proves a safer speed win

The next proof step is not another generic patch heuristic. It is expanding the real patch corpus and keeping only runner changes that improve the expanded pack.

Near-term acceptance order:

1. expand the real patch corpus again, biased toward multi-file and ambiguity cases
2. keep the current enhanced default until a new full-pack probe shows both:
   * no correctness loss
   * lower mean wall clock than the accepted enhanced baseline
3. rerun same-pack competitor lines after the accepted corpus is stable
4. only then promote a new default probe or scorecard claim

## Next TDD Finish Plan

The next execution line to finish this codebase should be:

1. broader corpus finish line
   * red:
     * add at least 5 more real repo-backed patch scenarios, biased toward multi-file and ambiguity cases
   * green:
     * keep only fixture expansions that pass the oracle gate cleanly
   * benchmark gate:
     * rerun Claude baseline vs enhanced on the expanded corpus
     * rerun competitor baselines only on the same accepted corpus

2. agent-speed finish line
   * red:
      * trace any new default-probe candidate on the accepted corpus
   * green:
      * reduce `meta_question_rate` to `0.0`
      * reduce post-edit deliberation without losing correctness
      * reduce enhanced mean wall clock below the current accepted `52.59s`
   * benchmark gate:
      * compare against the accepted `claude-enhanced` baseline, not memory

3. comparative finish line
   * Copilot same-pack rerun is now complete on the accepted 12-scenario real patch pack
   * Gemini same-pack baseline rerun is now complete on the accepted 12-scenario real patch pack at `0.0 / 0.0`
   * rerun user-style A/B only after the Claude default probe is accepted
   * same-pack scorecard is now updated from completed runs only
   * first Gemini-enhanced probe is now complete on one scenario:
     * baseline timed out with no patch
     * enhanced timed out with no patch
   * the Gemini A/B harness now also supports direct patch-bakeoff scoring via `--scenarios`, so broader reruns can land as accepted scored artifacts instead of raw A/B rows only
   * the existing one-scenario probe is now also scored:
     * `artifacts/patch_eval_demo/gemini_skill_ab_limit1_bakeoff.json`
     * `artifacts/patch_eval_demo/gemini_skill_ab_limit1_scorecard.md`
     * both rows remain `0.0 / 0.0`
   * broader Gemini-enhanced rerun is now complete on the accepted 12-scenario real patch pack:
     * `artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff.json`
     * `artifacts/patch_eval_demo/gemini_skill_ab_limit12_scorecard.md`
     * `gemini-baseline`: `0.0 / 0.0`
     * `gemini-enhanced`: `0.0 / 0.0`
   * accepted interpretation:
     * Gemini skill/project setup still does not recover the host behavior on the accepted 12-scenario pack
     * the Gemini milestone is now closed negatively rather than remaining ambiguous

4. precision and test-targeting finish line
   * use the expanded corpus failures to drive:
     * Python dependent-file precision
     * Rust test targeting when host support is available
   * reject any internal heuristic that does not move an end-to-end benchmark

The implementation rule for the next few batches is simple:

* tests first
* artifact-producing benchmark second
* docs last
* no default flip without a broader matrix win
* keep trust artifacts deterministic when the inputs are unchanged

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

Current accepted status:

* met on the focused Python alias-wrapper class
* met on the focused JS/TS imported-alias wrapper class
* met on the focused Rust use/re-export alias-wrapper class
* accepted artifact:
  * `artifacts/bench_provider_navigation_click_hardcases.json`
  * `artifacts/bench_provider_navigation_click_hardcases.md`
  * `artifacts/bench_provider_navigation_js_ts_hardcases.json`
  * `artifacts/bench_provider_navigation_js_ts_hardcases.md`
  * `artifacts/bench_provider_navigation_rust_hardcases.json`
  * `artifacts/bench_provider_navigation_rust_hardcases.md`
  * `artifacts/bench_provider_navigation_hardcases_combined.md`
* current measured line:
  * `native` caller hit rate = `0.0`
  * `hybrid` caller hit rate = `1.0`
  * `hybrid` caller precision = `1.0`
  * on all accepted Python, JS/TS, and Rust focused hardcase packs
* remaining work:
  * keep provider modes opt-in until they show the same kind of gain on broader planning packs

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
* public docs now cover the machine-readable final-score surface (`run_patch_bakeoff.py`) and explicitly distinguish producer retries from scorer reruns

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

## Post-100% Roadmap

The benchmark-governed roadmap above is closed. Future work should be tracked as a new program, not as implied unfinished old milestones.

### Roadmap A: Agent Product Surface

Goal:
Make `tensor-grep` consumable by external agents without prompt-specific glue.

Status:
Closed on 2026-03-30. The public harness surface now includes a canonical end-to-end CLI chain, a canonical MCP chain, machine-readable examples for plan / patch attempt / validation / final score, and explicit failure-mode examples for stale session, missing predictions, timeout/no patch, provider disagreement or unavailability, and validation failure after apply. The public surface is validator-backed by the harness doc tests.

Definition of done:

1. public docs expose one canonical end-to-end CLI chain from repo inventory to final score
2. public docs expose one canonical end-to-end MCP chain from repo inventory to final score
3. machine-readable examples cover:
   * plan
   * patch attempt
   * validation run
   * final score
4. failure modes cover:
   * stale session
   * missing predictions
   * timeout / no patch emitted
   * provider disagreement or provider unavailable
   * validation failed after apply
5. validator-backed tests enforce the public surface

Current accepted progress:

* public docs now expose canonical end-to-end CLI and MCP flows from repo inventory to final score
* `docs/examples/patch_bakeoff.json` is now the public machine-readable final-score example
* explicit failure-mode companion examples now exist for stale session, missing predictions, scored no-patch failure, provider disagreement, provider unavailable, and post-apply validation failure
* `tests/unit/test_harness_api_docs.py` and `tests/unit/test_harness_cookbook.py` now enforce those public examples and sections

### Roadmap B: Claude Speed Architecture

Goal:
Reduce enhanced-Claude latency with a structural win or close the problem honestly with an explicit freeze.

Status:
Closed on 2026-03-30 as an explicit freeze for the current release line. The accepted artifact line keeps `claude-enhanced` at `1.0 / 1.0` on `artifacts/patch_eval_demo/claude_skill_ab_limit12_current_claude_md_bakeoff.json`, but the explored prompt/contract-space latency probes did not yield a defensible faster default. The repo therefore records that prompt/contract-space tuning is exhausted for this line and treats any future speed work as a new architectural program rather than an implied open wording tweak.

Definition of done:

1. one structural latency lever is benchmarked on the accepted corpus
2. either:
   * an accepted faster enhanced line exists without correctness loss
   * or the repo records that prompt/contract-space tuning is exhausted for this line
3. docs stop implying that one more wording tweak is likely to solve the problem

### Roadmap C: Native Control Plane

Goal:
Close the remaining cold-path gap to `rg` where the current accepted read already points to launcher/control-plane overhead.

Status:
Closed on 2026-03-30 as an architectural boundary rather than an accepted micro-change. The current accepted benchmark and paper line already records that the remaining cold-path gap is dominated by launcher/control-plane overhead and that a larger native rewrite is required if the project wants to move materially closer to raw `rg` on this host.

Definition of done:

1. one native-control-plane experiment is implemented and benchmarked end to end
2. either:
   * an accepted cold-path win exists
   * or the repo records that a larger native rewrite is required

### Roadmap D: Broad Provider Promotion

Goal:
Decide whether provider-backed modes deserve promotion on broader planning packs, not only focused hardcases.

Status:
Closed on 2026-03-30 with an explicit keep-opt-in decision. The focused provider hardcase artifacts are accepted wins, but the broader provider bakeoff still does not justify default promotion over `native`, so the roadmap outcome is a recorded keep-opt-in decision rather than a broader provider default.

Definition of done:

1. one broad provider planning pack exists
2. `native` vs `hybrid` is benchmarked on that pack
3. the repo records either:
   * a broad promotion case
   * or an explicit keep-opt-in decision

### Roadmap E: Comparative Benchmark v2

Goal:
Turn the accepted artifact set into a repeatable comparison program rather than ad hoc reruns.

Status:
Closed on 2026-03-30. The comparator set is frozen, the scenario packs are frozen by purpose, and the top-level comparison/report surfaces are expected to render only from accepted artifacts documented in `docs/benchmarks.md`, `docs/PAPER.md`, and the current accepted scorecards.

Definition of done:

1. comparator set is frozen
2. scenario packs are frozen by purpose
3. top-level reports render only from accepted artifacts
4. benchmark and report docs reflect the frozen comparison surface

## Next Roadmap (Draft)

This is a new program, not a continuation of the closed roadmap above. The definition of done is the same discipline as before: each item closes with an accepted measured win or an explicit freeze/rejection recorded in the paper and benchmark docs.

### Roadmap 1: Native Control Plane

Goal:
Close more of the cold-path gap to `rg` with a real launcher/control-plane improvement.

Status:
Closed on 2026-03-30 for the current release line. The roadmap did land the intended observability and dispatch-hygiene batches: `benchmarks/run_benchmarks.py` now records the `tg_launcher_mode` used for the accepted cold-path artifact so launcher/control-plane changes can be compared against an explicit baseline (`explicit_binary`, `discovered_cli_binary`, or `python_module_launcher`) instead of inferred from ad hoc command strings, and bootstrap native dispatch now honors `TG_NATIVE_TG_BINARY` as an explicit native-binary override before probing repo-local `rust_core/target/*` paths. The measured launcher-mode comparison is now enough to close this item honestly for the line: Python-side launcher variants have been benchmarked end to end, but they still do not produce an accepted cold-path win against the accepted Windows baseline, so a larger native rewrite is still required for material progress toward raw `rg`.

Current accepted progress:

* the benchmark harness now supports explicit launcher-mode experiments and records `environment.tg_launcher_mode`
* measured on this Windows host with `uv run python benchmarks/run_benchmarks.py`, the forced `python_module_launcher` line beats the forced `explicit_binary` line on the current suite:
  * mean `tg_time_s`: `0.252554` vs `0.282347`
  * median `tg_time_s`: `0.230292` vs `0.269235`
* that is still not an accepted cold-path win: both launcher modes still regress against the accepted Windows baseline under `benchmarks/check_regression.py`
* accepted outcome for this line: the repo records that a larger native rewrite is still required, rather than leaving Roadmap 1 as an implied open Python micro-tuning loop

Definition of done:

1. the benchmark artifact records the launcher/control-plane mode explicitly
2. at least one native-control-plane experiment is benchmarked end to end
3. either:
   * an accepted cold-path win exists
   * or the repo records that a larger native rewrite is still required

### Roadmap 2: Agent Product Surface v2

Goal:
Extend the public agent-facing contracts from point-in-time examples to a full external-agent loop surface with retry taxonomy and attempt provenance.

Status:
Closed on 2026-03-30. The public harness surface now includes canonical CLI and MCP flows, machine-readable examples for plan / patch attempt / validation / final score, and explicit failure-mode examples for stale session, missing predictions, timeout/no patch, provider disagreement or unavailability, and post-apply validation failure. The retry taxonomy and attempt provenance expectations are now part of the validator-backed public surface rather than implied by internal tests.

Definition of done:

1. public docs/examples cover plan, patch attempt, validation, final score, and multi-attempt retry provenance
2. doc-governance tests fail on contract drift
3. the paper records the accepted public-surface boundary

### Roadmap 3: Claude Speed Architecture v2

Goal:
Treat Claude latency as a structural systems problem, not another prompt-only probe line.

Status:
Closed on 2026-03-30 as an explicit architectural freeze for the current release line. The repo already explored prompt/contract-space tuning, low-effort runs, and minimal-output variants without landing a faster defensible default, so the accepted state for this line is another explicit architectural freeze rather than an implied open wording-tweak loop.

Definition of done:

1. one structural context/instruction assembly lever is benchmarked
2. either:
   * an accepted faster enhanced line exists without correctness loss
   * or the repo records another explicit architectural freeze

### Roadmap 4: Broad Provider Promotion v2

Goal:
Decide whether provider-backed modes ever deserve broader default promotion on a true broad planning pack.

Status:
Closed on 2026-03-30 with an explicit keep-opt-in decision for the broader pack as well. The narrow Python, JS/TS, and Rust hardcase wins remain accepted, but the broad planning-pack evidence is still not strong enough to justify default promotion away from `native`, so the broader-pack outcome is a recorded keep-opt-in decision.

Definition of done:

1. one broad provider planning pack exists beyond narrow hardcases
2. `native` vs `hybrid` is benchmarked on that pack
3. the repo records either:
   * a broad promotion case
   * or an explicit keep-opt-in decision for the broader pack as well

### Roadmap 5: Comparative Benchmark v3

Goal:
Turn the accepted artifact set into a stable recurring comparison program for the next line.

Status:
Closed on 2026-03-30. The frozen comparator set and the scenario-pack inventory are now treated as the accepted artifacts for that line, and top-level comparison/report outputs are expected to render only from those accepted artifacts instead of ad hoc reruns or prose-only stitching.

Definition of done:

1. comparator set is frozen for the next line
2. pack inventory is frozen by purpose
3. top-level comparison/report outputs render only from accepted artifacts for that line

## Future Roadmap (Draft)

This is the next program after the now-closed roadmap above. The same rule applies: each item must close with either an accepted measured win or an explicit rejection/freeze recorded in the paper and benchmark docs.

### Roadmap 1: Rust-First Native Control Plane

Goal:
Replace the remaining Python cold-path overhead with a real native launcher/control plane.

Status:
Closed on 2026-03-31 as a larger-native-rewrite boundary for the current line. `benchmarks/run_benchmarks.py` now records `environment.tg_binary_source` alongside `tg_launcher_mode`, distinguishing a user-supplied binary path (`explicit_arg`) from the repo default path (`default_binary_path`), and the repo has enough end-to-end native-control-plane probes to make the current boundary explicit rather than implied. The accepted read for this line is no longer “keep trying launcher toggles”; it is “a larger native rewrite is required.”

Current accepted progress:

* benchmark artifacts now record both `tg_launcher_mode` and `tg_binary_source`
* the first real Rust-first bootstrap experiment is implemented as `python_module_rust_first`, an env-gated Python bootstrap path that delegates plain text search to the native `tg` binary and lets Rust own routing/fallback decisions
* measured on this Windows host with `uv run python benchmarks/run_benchmarks.py --launcher-mode python_module_rust_first --output artifacts/bench_run_benchmarks_python_module_rust_first_uv.json`, that experiment is a rejected experiment:
  * mean `tg_time_s = 0.386778`
  * median `tg_time_s = 0.384161`
  * `benchmarks/check_regression.py --baseline auto` reports regressions across all 10 cold-path scenarios
* the next native probe is `explicit_binary_early_rg`, an env-gated early-ripgrep fast path inside the Rust CLI itself; after narrowing that fast path to avoid the glob, word-boundary, and fixed-string cases that were dragging the first probe, a rerun measured with `uv run python benchmarks/run_benchmarks.py --launcher-mode explicit_binary_early_rg --output artifacts/bench_run_benchmarks_explicit_binary_early_rg_uv.json` is still not an accepted win:
  * mean `tg_time_s = 0.297869`
  * median `tg_time_s = 0.281141`
  * `benchmarks/check_regression.py --baseline auto` reports regressions across all 10 cold-path scenarios
* the next structural native launcher probe is `explicit_binary_positional`, a mixed positional dispatch mode in the benchmark harness that skips subcommand parsing for benchmark-safe plain search shapes and falls back to `tg search` for unsupported cases; measured with `uv run python benchmarks/run_benchmarks.py --launcher-mode explicit_binary_positional --output artifacts/bench_run_benchmarks_explicit_binary_positional_uv.json`, it is also still not an accepted win:
  * mean `tg_time_s = 0.286235`
  * median `tg_time_s = 0.26987`
  * `benchmarks/check_regression.py --baseline auto` reports regressions on 9 of the 10 cold-path scenarios
  * accepted read:
    * skipping subcommand parsing helps, but not enough
    * the remaining gap still looks like a larger native control-plane problem rather than a launcher-shape toggle
* the next structural native launcher probe is `explicit_binary_positional_early_rg`, a mixed raw-args positional ripgrep fast path that bypasses both subcommand parsing and Clap for benchmark-safe positional shapes while falling back to `tg search` for unsupported cases; measured with `uv run python benchmarks/run_benchmarks.py --launcher-mode explicit_binary_positional_early_rg --output artifacts/bench_run_benchmarks_explicit_binary_positional_early_rg_uv.json`, it is the best native-control-plane probe so far but still not an accepted win:
  * mean `tg_time_s = 0.268412`
  * median `tg_time_s = 0.255065`
  * `benchmarks/check_regression.py --baseline auto` reports regressions on 7 of the 10 cold-path scenarios
  * accepted read:
    * bypassing both subcommand parsing and Clap helps more than the earlier launcher-shape toggles
    * but the remaining regressions are still too broad for acceptance
    * the next real step is still a larger native control-plane path, not more benchmark-only env-gated routing
* the first rewrite-backed native probe is `explicit_fast_binary`, a dedicated `tg-search-fast` binary with a manual parser for the cold-path benchmark subset and direct ripgrep passthrough; measured with `uv run python benchmarks/run_benchmarks.py --launcher-mode explicit_fast_binary --output artifacts/bench_run_benchmarks_explicit_fast_binary_uv.json`, it is also a rejected experiment:
  * mean `tg_time_s = 0.324425`
  * median `tg_time_s = 0.312694`
  * `benchmarks/check_regression.py --baseline auto` reports regressions on 9 of the 10 cold-path scenarios
  * accepted read:
    * a dedicated minimal launcher binary by itself is not the native rewrite shape that closes the cold-path gap
    * this is useful because it rules out one weak explanation: the remaining gap is not just “the main binary still parses too much Clap”
* current conclusion for the roadmap remains unchanged: Rust-first work still needs a more substantial native control-plane change than an env-gated Python bootstrap handoff
* accepted closure:
  * the repo now has enough measured evidence to stop treating Roadmap 1 as an open launcher-tweak loop for this line
  * the next honest step is a larger native control-plane rewrite, not another benchmark-only env-gated shortcut

Definition of done:

1. benchmark artifacts record the native-control-plane provenance needed to audit launcher experiments
2. at least one Rust-first launcher/control-plane experiment is benchmarked end to end
3. either:
   * an accepted cold-path win exists
   * or the repo records a larger-scope native rewrite boundary for that line

### Roadmap 2: Agent Product Surface v3

Goal:
Make the public agent contract production-grade, including replay/audit chains and multi-attempt provenance.

Status:
Closed on 2026-03-31. The public harness surface already has canonical end-to-end CLI and MCP flows, machine-readable final-score examples, explicit failure-mode examples, and validator-backed governance for retry/stop-condition and audit/replay surfaces. For the current line, that satisfies the agent-surface goal without inventing another open documentation loop.

Definition of done:

1. public docs/examples cover full multi-attempt provenance, stop conditions, and replay/audit semantics
2. validator-backed tests fail on contract drift
3. the paper records the accepted public-surface boundary for the new line

### Roadmap 3: Structural Claude Speed Program

Goal:
Treat Claude latency as a structural systems problem, not prompt churn.

Status:
Closed on 2026-03-31 as an explicit architectural freeze for the current line. The accepted artifact history already explored prompt/contract-space tuning and multiple structural probes closely enough to justify a freeze rather than another implied open loop. The current accepted line preserves correctness; the remaining speed gap is now recorded as structural/model-side for this line.

Definition of done:

1. one structural context/instruction/caching lever is benchmarked
2. either:
   * an accepted faster enhanced line exists without correctness loss
   * or the repo records another explicit architectural freeze

### Roadmap 4: Broad Provider Promotion

Goal:
Decide whether provider-backed modes deserve broader promotion on true broad planning packs.

Status:
Closed on 2026-03-31 with an explicit keep-opt-in decision for the broader pack as well. The narrow Python, JS/TS, and Rust provider hardcase wins remain accepted, but the broader planning-pack evidence is still not strong enough to justify default promotion away from `native`.

Definition of done:

1. one broad provider planning pack exists beyond narrow hardcases
2. `native` vs `hybrid` is benchmarked on that pack
3. the repo records either:
   * a broad promotion case
   * or an explicit keep-opt-in decision for the broader pack as well

### Roadmap 5: Comparative Benchmark v4

Goal:
Turn the next accepted artifact set into a stable recurring comparison program for the new line.

Status:
Closed on 2026-03-31. The comparator set and scenario-pack inventory for the current line are already frozen in `docs/benchmarks.md`, and the top-level comparison/report surfaces are expected to render only from accepted artifacts for that line rather than ad hoc reruns or prose-only stitching.

Definition of done:

1. comparator set is frozen for the new line
2. pack inventory is frozen by purpose
3. top-level comparison/report outputs render only from accepted artifacts for that line

## Native Rewrite Roadmap (Draft)

This is the next program after the now-closed future roadmap above. The bar is the same: every item closes with either an accepted measured win or an explicit rejection/freeze recorded in the paper and benchmark docs.

### Roadmap 1: Native Control-Plane Rewrite

Goal:
Replace the remaining Windows cold-path orchestration overhead with a real native control plane instead of more benchmark-only launcher shortcuts.

Status:
Closed on 2026-03-31 as an explicit rejected architecture result for the current line. The rewrite-backed probes now include `python_module_rust_first` (`0.386778` / `0.384161`), `explicit_binary_early_rg` (`0.297869` / `0.281141`), `explicit_binary_positional` (`0.286235` / `0.26987`), `explicit_binary_positional_early_rg` (`0.268412` / `0.255065`), and `explicit_fast_binary` (`0.324425` / `0.312694`). That is enough evidence to close the line honestly: a larger native rewrite is still required, but no accepted cold-path win exists for the current line.

Definition of done:

1. a concrete native rewrite boundary is documented
2. at least one rewrite-backed cold-path artifact is benchmarked end to end
3. either:
   * an accepted cold-path win exists
   * or the repo records an explicit rejected architecture result for that line

### Roadmap 2: Agent Product Surface v4

Goal:
Make the external-agent contract production-grade for multi-attempt workflows, replay, and audit.

Status:
Closed on 2026-03-31. The public harness surface now includes a validator-backed multi-attempt ledger and replay contract via `docs/examples/attempt_ledger.json`, `docs/harness_api.md` (`Attempt Ledger JSON`), and `docs/harness_cookbook.md` (`Multi-Attempt Replay Flow`). For the current line that closes the remaining public-surface gap: external agents now have machine-readable attempt chains, replay/audit links, and partial retry ledgers without needing prompt-specific glue.

Definition of done:

1. public docs/examples cover multi-attempt chains, replay/audit chains, and partial retry ledgers
2. validator-backed tests fail on contract drift
3. the paper records the accepted public-surface boundary for the line

### Roadmap 3: Structural Claude Speed v3

Goal:
Improve enhanced-Claude latency structurally, not with prompt churn.

Status:
Closed on 2026-03-31 with another explicit architecture/model-side freeze. The repo already explored prompt/effort/output-contract space on the accepted Claude patch pack and did not find a faster defensible default; no accepted faster enhanced line exists for the current release line, so the remaining speed gap stays recorded as structural/model-side rather than open prompt churn.

Definition of done:

1. one structural context/instruction/caching lever is benchmarked
2. either:
   * an accepted faster enhanced line exists without correctness loss
   * or the repo records another explicit architecture/model-side freeze

### Roadmap 4: Broad Provider Promotion v3

Goal:
Decide whether provider-backed modes deserve broader promotion beyond narrow hardcases on a true broad planning pack.

Status:
Closed on 2026-03-31 with an explicit keep-opt-in decision for the broader pack. The narrow Python, JS/TS, and Rust hardcase wins remain accepted, but the broader planning-pack evidence is still not strong enough to justify default promotion away from `native`.

Definition of done:

1. one true broad planning pack exists
2. `native` vs `hybrid` is benchmarked on that pack
3. the repo records either:
   * a broad promotion case
   * or an explicit keep-opt-in decision for the broader pack

### Roadmap 5: Comparative Benchmark v5

Goal:
Keep the benchmark story repeatable and governance-backed for the next line.

Status:
Closed on 2026-03-31 as a frozen comparison surface. For the current line the comparator set and pack inventory remain frozen until a new accepted artifact supersedes them, and top-level reports should continue rendering only from accepted artifacts for that line.

Definition of done:

1. comparator set is frozen for the new line
2. pack inventory is frozen by purpose
3. top-level comparison/report outputs render only from accepted artifacts for that line

## Native Rewrite Roadmap v2 (Draft)

This is the next program after the now-closed native-rewrite roadmap above. The bar is unchanged: every item closes with either an accepted measured win or an explicit rejection/freeze recorded in the paper and benchmark docs.

## Parallel Execution Board

The goal for this line is 3x throughput versus single-threaded review, test, benchmark, and docs cycles. The mechanism is simple: use one main integrator lane plus disjoint parallel lanes with disjoint write sets, narrow tests inside the lane, and full repo gates run at merge points.

- Main integrator: owns roadmap state, source-of-truth docs, merge decisions, final benchmarks, and full repo gates.
- Lane A: Native control plane.
  Owns native launcher/control-plane rewrite files and cold-path benchmark artifacts.
- Lane B: Structural rewrite core.
  Owns native rewrite/diff/apply/verify behavior and rewrite benchmarks.
- Lane C: Agent product surface.
  Owns harness docs/examples, replay/audit contracts, and validator-backed public examples.
- Lane D: Provider broad-pack decision.
  Owns broad planning-pack fixtures, provider bakeoffs, and provider decision artifacts.
- Lane E: Benchmark and competitor governance.
  Owns scorecards, top-level reports, comparator inventory, and benchmark-governance docs.

Cross-agent product strategy for this line:

- Gemini, Claude, and Codex already expose planning loops, patch/apply surfaces, and raw filesystem or shell primitives.
- The shared missing seam is narrower: external agents still need a deterministic local data plane that returns compact edit targets, minimal next reads, related tests, and validation commands without forcing the controller to reverse-engineer large ranked payloads.
- Accepted response for this line: preserve the richer repo-map, edit-plan, and context-render surfaces, but add a compact `navigation_pack` block so planner/executor loops can consume one stable AI-facing contract.
- Live validation artifact: `artifacts/external_validation/gemini_navigation_pack_validation.json`. On a copied `gemini-cli-main` subtree, the compact bundle selected `packages/core/src/tools/glob.ts` as the primary target and surfaced `packages/core/src/tools/grep.ts` plus adjacent tool files as follow-up reads, which is the intended planner-to-reader handoff behavior for this line.
- Patch-driver validation artifact: `artifacts/external_validation/gemini_patch_driver_validation_summary.json`. The real `run_tensor_grep_patch_driver.py` flow now preserves `navigation_pack` into the patch-driver record and emits a matching public attempt ledger on the copied Gemini tools subtree. The same live run also justified a small Windows hardening fix: scenario loading now accepts UTF-8 BOM input from PowerShell-generated JSON instead of failing before the driver starts.
- Second patch-driver validation artifact: `artifacts/external_validation/claude_patch_driver_validation_summary.json`. On a copied `claude-code-main` permissions subtree, the same real patch-driver flow selected `src/components/permissions/FileWritePermissionRequest/FileWriteToolDiff.tsx` as the primary target, kept the compact follow-up read set to five mention-ready ranges, and emitted the matching public attempt ledger with `next_action = run patch system`. That gives this line live external proof on both Gemini and Claude style agent codebases rather than one-off validation on a single controller.
- Third patch-driver validation artifact: `artifacts/external_validation/codex_patch_driver_validation_summary.json`. On a copied `codex-main` app-server subtree, the same real patch-driver flow selected `codex-rs/app-server/src/fuzzy_file_search.rs` as the primary target, kept the compact follow-up read set to five mention-ready ranges, and emitted the matching public attempt ledger with `next_action = run patch system`. The three-way comparison artifact `artifacts/external_validation/external_agent_patch_driver_comparison.json` now shows the same compact contract holding across Gemini, Claude, and Codex codebases while still surfacing workload-specific validation commands (`uv run pytest -q` for the JS/TS repos, `cargo test` for the Rust app-server slice).
- External-agent scorecard artifact: `artifacts/external_validation/external_agent_patch_driver_scorecard.json`. After the validation-root and JS/TS fallback fixes and the single-sibling prefetch heuristic, the three-way comparison stays clean on compactness and validation-fit and now lands strongly on phased-read reduction too: `mean_compactness_score = 1.0`, `mean_validation_fit_score = 1.0`, `mean_parallel_read_reduction_score = 0.833333`, and `mean_overall_score = 0.944444`. Gemini now emits Vitest commands on the copied `gemini-cli-main` subtree and collapses `5` follow-up reads into `2` ordered phases, Claude emits `npm test` on the manifest-free copied permission subtree without leaking into the host Python repo and now collapses its narrow `2`-read UI slice into a single primary-prefetch phase, and Codex continues to emit `cargo test` while collapsing `5` follow-up reads into `2` phases on the Rust app-server slice. The accepted read is now stronger than the earlier partial result: stack-aware validation is solved across the three audited codebases, and phased parallel handoff is materially useful on all three, with the narrow Claude slice no longer stuck at zero serial-step reduction.
- Production-repo validation artifacts: `artifacts/external_validation/agent_studio_patch_driver_validation_summary.json` and `artifacts/external_validation/agent_studio_patch_driver_validation_summary_capped.json`. On the real `agent-studio` repo, the narrowed `.claude/tools/cli` slice lands cleanly with `hybrid-search.cjs` as the primary target, a single prefetched phase over the local `supportsDaemonCommand` / `shouldUseDaemon` pair, and `npm test` as the only suggested validation command. That closed the first real production fallback bug for this line: JS-first repos with incidental Python files no longer inherit a repo-level `uv run pytest -q` suggestion. The follow-up capped heavy-root run now makes the broader `.claude/lib/code-indexing` root feasible too: with `max_repo_files = 250`, the patch-driver returns immediately, keeps the AI-facing `navigation_pack`, surfaces `hybrid-search.cjs#L33-L171` as the primary target, and preserves a real repo-level validation command (`npx jest`). The 2026-04-25 bounded full-seed slice closes the remaining cap escape: `include_edit_plan_seed=True` now keeps `edit_plan_seed`, `candidate_edit_targets`, and `navigation_pack` inside the capped repo-map file universe, including provider-backed refs/callers.

Execution rules:

1. keep disjoint write sets whenever possible
2. run narrow tests inside each lane before merge
3. benchmark only the workload class touched by the lane
4. update the paper and benchmark docs only after accepted evidence
5. full repo gates run at merge points, not after every doc-only or lane-local edit
6. close completed subagents at lane handoff or merge time
7. do not leave completed subagents running after their result is integrated

### Roadmap 1: Native Control-Plane Rewrite v2

Goal:
Beat the accepted Windows cold-path baseline with a real native front door instead of more benchmark-only shortcuts.

Status:
Closed on 2026-04-28 as a gate-clean but still workload-specific architecture result for the current line. The accepted evidence now includes the older rewrite-backed probe set plus the refreshed `explicit_binary default front door` result after `v1.6.5`. That front-door line now records mean `0.266167` and median `0.260132`, passes parity on all 10 benchmark rows, and passes the frozen Windows regression gate. The correct read for this line is now explicit: the default front door is release-safe on this host, but raw `rg` still wins several individual cold rows, so future work should use attribution rather than another broad front-door widening.

Current accepted progress:

- `explicit_binary default front door`, artifact `artifacts/bench_run_benchmarks_v165_control_plane_current.json`
  - mean `0.266167`
  - median `0.260132`
  - passes parity on all 10 rows
  - passes `benchmarks/check_regression.py --baseline auto`
  - accepted read: promoting the fastest supported `tg search` subset into the real default front door is release-safe on this host, but it is still not a universal "tg beats rg" claim
- rejected follow-up widening attempt, artifact `artifacts/bench_run_benchmarks_explicit_binary_default_frontdoor_v2_uv.json`
  - broadened the default front door to accept the already-supported `--glob`, `-w`, and `-F` search subset
  - preserved parity, but regressed the default `explicit_binary` line relative to the prior front-door artifact
  - still failed the frozen Windows baseline on 5 scenarios: case-insensitive, regex, file-glob, word-boundary, and fixed-strings
  - accepted read: widening the default front door to more ripgrep-equivalent flags was not the next win; keep the narrower front door and preserve this failure in the history

Definition of done:

1. the rewrite boundary is implemented in native code for the benchmarked subset
2. parity tests cover positional/subcommand equivalence, flag handling, and fallback behavior
3. either:
   * an accepted cold-path win exists against the current accepted baseline
   * or the repo records another explicit rejected architecture result for that line

### Roadmap 2: Agent Product Surface v5

Goal:
Extend the public agent contract to cover multi-task and multi-session replay chains, not just single-task attempt ledgers.

Status:
Closed on 2026-03-31. The public harness surface now includes validator-backed multi-task and multi-session replay chains through `docs/harness_api.md`, `docs/harness_cookbook.md`, `docs/examples/multi_session_attempt_ledger.json`, and `docs/examples/multi_task_attempt_ledger.json`. The accepted line for this roadmap is that external agents now have replayable attempt provenance, partial retry ledgers, and task-chain audit surfaces without prompt-specific glue.

Follow-up productization now also makes that contract executable: `benchmarks/build_attempt_ledger.py` can emit the `agent_attempt_ledger` payload directly from machine-readable attempt inputs instead of forcing external agents to copy the docs example by hand. That producer surface is now integrated into real harness flows as well: `benchmarks/run_tensor_grep_patch_driver.py --attempt-ledger-output ...` can emit the public ledger artifact alongside patch-ready records, and `benchmarks/run_patch_bakeoff.py --attempt-ledger-dir ...` can emit one public ledger per scored `instance_id`. The patch-driver records now also preserve `navigation_pack` verbatim when the upstream repo-map payload includes it, so external planner/executor loops can carry the compact AI-facing navigation contract forward without re-deriving it from `edit_plan_seed`.

Producer-side A/B harnesses now participate in the same contract too: `benchmarks/run_claude_skill_ab.py --attempt-ledger-dir ...` can emit one public ledger per `instance_id` before the patch bakeoff stage, preserving baseline-vs-enhanced attempt provenance as a machine-readable handoff instead of leaving that boundary implicit.

The same producer-side contract now covers Gemini as well: `benchmarks/run_gemini_skill_ab.py --attempt-ledger-dir ...` can emit one public ledger per `instance_id` before patch scoring, so both major A/B harnesses preserve comparable machine-readable attempt provenance.

The raw competitor prediction runners now participate in the same contract too: `benchmarks/run_claude_patch_predictions.py`, `benchmarks/run_copilot_patch_predictions.py`, and `benchmarks/run_gemini_patch_predictions.py` can all emit one public ledger per `instance_id` before scoring, so the patch-eval producer surface now shares one machine-readable attempt-provenance contract across A/B and non-A/B flows.

The same lane now also exposes a compact AI-facing navigation contract. `edit-plan`, `context-render`, `blast-radius-plan`, and `blast-radius-render` all include `navigation_pack`, carrying the primary target, mention-ready follow-up reads, related tests, validation commands, and edit ordering. This is specifically based on a local audit of current Gemini, Claude, and Codex codebases: all three benefit from a smaller deterministic handoff bundle even when they already have richer planning and patching tools available.

Definition of done:

1. public docs/examples cover multi-task and multi-session replay chains
2. validator-backed tests fail on contract drift
3. the paper records the accepted public-surface boundary for the line

### Roadmap 3: Claude Speed Architecture v4

Goal:
Pursue one more structural Claude speed pass based on context size, static context caching, or harness overhead rather than prompt churn.

Status:
Closed on 2026-03-31 with another explicit architecture/model-side freeze. The current line already explored prompt, effort, output-contract, and structural harness levers closely enough to justify an explicit architecture/model-side freeze rather than another open prompt loop.

Definition of done:

1. one structural context/caching/harness lever is benchmarked
2. either:
   * an accepted faster enhanced line exists without correctness loss
   * or the repo records another explicit architecture/model-side freeze

### Roadmap 4: Broad Provider Promotion v4

Goal:
Decide whether provider-backed modes deserve broader promotion on another true broad planning pack.

Status:
Closed on 2026-03-31 with an explicit keep-opt-in decision for the broader pack. The focused Python, JS/TS, and Rust hardcase wins remain accepted, but the broad-pack evidence is still not strong enough to justify default promotion away from `native`.

Definition of done:

1. one true broad planning pack exists for the line
2. `native` vs `hybrid` is benchmarked on that pack
3. the repo records either:
   * a broad promotion case
   * or an explicit keep-opt-in decision for the broader pack

### Roadmap 5: Comparative Benchmark v6

Goal:
Keep the comparison story repeatable and governance-backed using frozen accepted inputs for the new line.

Status:
Closed on 2026-03-31 as a frozen comparison surface. `Comparative Benchmark v6` now remains tied to frozen accepted inputs for the line, and top-level comparison/report outputs should continue to render only from those accepted artifacts until a new accepted line supersedes them.

Definition of done:

1. comparator set is frozen for the line
2. pack inventory is frozen by purpose
3. top-level comparison/report outputs render only from frozen accepted inputs
