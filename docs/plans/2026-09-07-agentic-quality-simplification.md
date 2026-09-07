# Agentic Quality and Simplification Implementation Plan

> **For agentic workers:** Use the executing-plans skill to implement this plan task by task.
> Follow the repository's design-review, independent security-review, real-venv verification,
> and release rules. Checkbox steps track work; an unchecked step is not approval or completion.

**Goal:** Make tensor-grep easier to maintain and materially more useful to agents, while
preserving established behavior on the refactor track and proving intended changes on the
correctness/feature track.

**Architecture:** Keep native search and the existing Python analysis services. Introduce small
shared contracts at current integration seams, reuse validated snapshots and existing evaluators,
and retain legacy adapters until consumers migrate. Measure before selecting new caches,
runtimes, semantic providers, or protocol defaults.

**Tech stack:** Python/Typer/FastMCP, Rust/PyO3, tree-sitter/ast-grep, pytest,
existing benchmark scripts, optional Model2Vec, existing LSP provider abstraction.

**Baseline:** 5d67210e343f95f2bd4eac31d155f98b29b0c003, reviewed 2026-09-07.
**Evidence:** [audit and Exa sources](../audits/2026-09-07-agentic-quality-audit.md).
**Ledger:** [BACKLOG.md](../BACKLOG.md#agentic-quality-audit-2026-09-07).
**Status:** detailed candidate plan; no implementation or release claimed. Available agents
audited the source; formal Fable/Opus/council approval was not obtained.

---

## How a junior analyst should use this plan

Work on one package at a time. Start with its evidence citation, read the named function, and
record what it currently does. Run only the bounded controls named for that package. Save
baseline and changed results separately, including command, return code, source SHA, environment,
and whether any output was incomplete. Ask the assigned maintainer to review code-changing
decisions; do not guess at security primitives or modify a test to accommodate a regression.

Definitions:

- **Behavior-preserving refactor:** stdout, stderr, exit codes, ordering, exception categories,
  fallback disclosure, and supported imports retain their contract. Faster execution is allowed.
- **Control:** an input whose correct outcome is known independently of the implementation.
- **RED/GREEN:** the intended bug test fails for the stated behavioral reason before the fix and
  passes after it. An import failure is not a behavioral RED.
- **Pin:** a test preserving current legitimate ranked output before ranking changes.
- **Generation:** identity of one session/repository snapshot; a newer write must not be replaced
  by a computation based on an older generation.
- **Holdout:** labeled tasks not used to select ranking weights or thresholds.
- **Provenance:** where a fact came from. Freshness and correctness confidence are separate facts.
- **Budget:** explicit cap on time, bytes, files, or output. Missing evidence after a cap is not success.

### Deliverable for every package

Create a short PR receipt with: baseline SHA; package ID; exact changed files; baseline control;
changed control; passing/skipped/failed counts; known limitations; benchmark corpus/hardware/warm
state if relevant; rollback instruction; and reviewer verdict tied to the final commit.
For an experiment, REFUTED or NO BENEFIT with valid measurements is a completed research result.
Do not invent a feature patch just to produce a diff.

### Shared preparation and safe test runner

- [ ] Confirm current work without discarding another person's changes:
~~~powershell
git status --short
git rev-parse HEAD
gh pr list --state open --json number,title,headRefName,baseRefName
gh run list --branch main --workflow ci.yml --limit 1 --json databaseId,headSha,status,conclusion
~~~
- [ ] Read AGENTS.md's relevant laws and the package's cited existing plan before implementation.
- [ ] Create an isolated implementation branch/worktree under the repository's usual workflow.
  Re-verify harvested code in the real Windows checkout/venv. Never point WSL uv at this .venv.
- [ ] Record the command contracts before changing code: defaults, supported flags, return shape,
  ordering, failure classes, partial results, and consumers/monkeypatch sites.
- [ ] Define this bounded runner once in the main-checkout PowerShell session:
~~~powershell
function Invoke-BoundedPytest {
    param([string[]]$Nodes)
    $TestSupervisor = @'
import subprocess, sys
try:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest",
         *sys.argv[1:], "-q", "--timeout=15"],
        timeout=120,
        check=False,
    )
    raise SystemExit(completed.returncode)
except subprocess.TimeoutExpired:
    print("outer test timeout after 120 seconds", file=sys.stderr)
    raise SystemExit(124)
'@
    uv run --no-sync python -c $TestSupervisor @Nodes
    if ($LASTEXITCODE -ne 0) { throw "Test run failed: $LASTEXITCODE" }
}
~~~
  Expected: the environment already contains pytest-timeout. A missing plugin is an environment
  failure, not permission to remove the cap. Dangerous deadlock/ReDoS tests must not run against
  an unfixed implementation; use bounded deterministic Event controls, as AGENTS.md A6/A17 require.
  The supervisor runs through uv, then directly supervises pytest in that same interpreter;
  timing out a uv wrapper alone could leave pytest running on Windows. This watchdog does not
  establish process-tree containment. Each fixture-created subprocess must have its own timeout
  and cleanup; Task 15's production runner requires a separately reviewed containment design.
- [ ] For touched Python code, run bounded ruff check, ruff format --check --preview, and mypy
  in the real environment. Format only owned files. Do not repin size ceilings to make a split pass.
  Rust compilation, dense inference, full suites, large corpora, and benchmark matrices go to
  approved CI/cloud capacity. This plan itself does not launch them.
- [ ] Before a PR, use git diff --check and the existing file-size/governance validators.
  Wait for the actual workflow's completion, not a momentarily green check list. Follow the
  repository's publish gate; no release or external comment is authorized merely by this plan.

### Scope and dependency map

IDs below are work owners, not severity numbers. Existing P-items retain their identity.

| Task | Owner ID | Track | Prerequisites | Suggested slice size |
|---|---|---|---|---|
| 01 | AGT-01 | Measurement | none | 2–3 PRs: metrics, joins, holdout |
| 02 | AGT-02 | Correctness | none; review lock design | 2 PRs: race, decision freshness |
| 03 | AGT-03 | Integration | 02 | 2 PRs: actions, wire migration |
| 04 | AGT-04 | Security design/correctness | existing S1/S5 identity design remains required | 2–3 reviewed slices |
| 05 | AGT-05 | Ranking quality | 01 | one hypothesis per PR |
| 06 | P9 | Warm optimization | 02, 03 | reuse first; contention second |
| 07 | P13 | Refactor | import baseline before extraction | one service family per PR |
| 08 | AGT-06 | Refactor | none | 1 PR |
| 09 | AGT-07 | Refactor | none | model then adapter migrations |
| 10 | MCP-SURFACE | Opt-in protocol | 01, 03, 09 and existing Task-4 gate | prototype then negotiated wire |
| 11 | AGT-08 | Explanation integration | 01 | 1–2 PRs |
| 12 | P12 | Dense optimization | 01, 11 | profile then one candidate |
| 13 | P14 | Provenance/provider experiment | 01, 02, 09 | fact envelope before provider pilot |
| 14 | P7 | AST optimization | 01 | census/profile before cache |
| 15 | P10 | Evidence integration | 02, 03, 04, 13 and existing F5/F6 gates | schema then bounded execution |
| 16 | P15 | Docs/tooling | none for baseline facts | generated facts then prose |

The first work wave is 01/02/04 design and 07/08/09/16 baselines. These can be investigated
independently; do not parallel-edit their shared modules. Tasks 02 -> 03 -> 06 are sequential.
Task 10 does not change MCP defaults or bypass MCP-LEAN-DEFAULT. P9 does not reopen
DD-006/CONTINUOUS-REFRESH build authority. Public comparison remains owned by #72;
GPU proof/spend remains owned by #169.

### What “best in class” would have to mean

Use existing run_patch_bakeoff.py and run_agent_workflow_benchmarks.py. Compare a fixed
model/harness/budget across shell+rg, tensor-grep, and a compatible symbol-aware baseline
such as Serena; use Aider/SCIP as design references unless an equivalent executable arm exists.
Pin tool versions and supported languages. Unsupported arms are reported, not scored as failures.

Report separately: verified task completion; primary-file/symbol accuracy; candidate recall;
wrong-confident-primary; selective accuracy versus abstention; tool calls; input/output tokens;
end-to-end latency; incomplete/error rates; warm refresh correctness; and resource consumption.
Missing cost is null, not zero. Every comparison has matched tasks, denominators and uncertainty.
Do not collapse safety and speed into an opaque average that lets a fast wrong answer win.
Expand holdout tasks beyond filename cues and this repository; include ambiguity and absence.
A proposed optimization advances only when existing pins pass and its measured benefit exceeds
measurement noise without a material correctness regression. Define that noise from repeats
before inspecting the candidate. Publication additionally needs #72's existing review.

<a id="task-01"></a>
## Task 01 — AGT-01: Outcome and confidence-risk measurement

**Owner:** evaluation maintainer. **Evidence:** audit finding 1; R1/R2/R7.
**Modify:** benchmarks/run_agent_workflow_benchmarks.py,
benchmarks/build_external_agent_patch_driver_scorecard.py,
tests/eval/test_agent_accuracy.py.
**Reuse:** benchmarks/run_patch_bakeoff.py, benchmarks/run_agent_success_harness.py.
**Tests:** tests/unit/test_agent_workflow_benchmark_script.py;
create tests/unit/test_agent_outcome_join.py.
**Create:** benchmarks/external_eval/agent_quality_holdout_manifest.json only after curator review.

- [ ] Record current fields/denominators and keep candidate-recall success unchanged.
- [ ] Add a pure scoring control with wrong primary, correct alternative, confidence 0.94, and
  ask=false. It must remain hit@3 while the NEW risk metric reports true. Use the existing
  WRONG_CONFIDENT_MISS_THRESHOLD rather than introducing a second threshold.
~~~python
wrong_confident_primary = bool(
    target_selection_evaluated
    and not hit_at_1
    and not ask_required
    and confidence_overall is not None
    and confidence_overall >= WRONG_CONFIDENT_MISS_THRESHOLD
)
~~~
- [ ] Add controls for correct primary, ask=true, absent confidence, incomplete result, and empty
  candidates. Report incomplete observations separately from complete readiness denominators.
- [ ] Extend reports additively; do not rename wrong_confident_miss, which is a top-three metric.
- [ ] Join actual patch outcomes by system_id, instance_id, repo_commit, tool_version,
  model_id, and budget identity. Normalize old records through an explicit adapter; reject
  duplicate identities and retain missing joins as unavailable. Do not fabricate absent IDs.
- [ ] Add this outcome control: a suggested cargo test command with a failing actual patch
  has command-fit credit but zero verified-task-success credit. An absent execution result is
  unavailable, not passing. Tokens/elapsed include failed attempts.
- [ ] Curate development and holdout tasks from existing external/patch fixtures. Record repo
  commit, task text, expected behavior, oracle command, license/provenance, and split. Check that
  baseline fails and a reviewed known-good patch passes each behavior oracle.
- [ ] Keep the synthetic success harness as workflow smoke. Run heavier holdout comparisons only
  in CI/cloud after the controls pass.

**Run:** Invoke-BoundedPytest @("tests/unit/test_agent_workflow_benchmark_script.py",
"tests/unit/test_agent_outcome_join.py").
**Accept:** wrong-first/correct-second is visible; no incomplete/missing execution wins; matched
joins and task-level raw outcomes are inspectable. Existing recall metrics remain available.
**Rollback:** remove additive new report consumers first; keep old schema readers and metrics.
No ranking behavior changes belong in this package.

<a id="task-02"></a>
## Task 02 — AGT-02: Session publication and decision freshness

**Owner:** session maintainer; independent security reviewer required.
**Modify:** src/tensor_grep/cli/session_store.py,
src/tensor_grep/cli/session_resume_service.py,
src/tensor_grep/cli/prepare_service.py.
**Tests:** tests/unit/test_session_resume.py; create
tests/unit/test_session_prepare_refresh_interleaving.py.

- [ ] Diagram refresh's initial read, map build, lock acquisition, payload publication, and index
  publication; compare prepare's corresponding steps. Capture current schema and legacy fixtures.
- [ ] Build a tiny temporary repository and session. Gate refresh immediately after its initial
  read with two threading.Events. Publish a newer prepared decision before releasing refresh.
  Every wait and future.result gets a short timeout; finally always releases both gates.
- [ ] Assert the final saved decision is the newer one. Add the inverse schedule and two concurrent
  refreshes. A timeout is test failure, never skip; avoid sleep-based overlap assertions.
- [ ] For the first minimal fix, re-read the current session payload inside the existing publication
  lock and merge only preserved decision metadata there. Do not blindly merge an obsolete repo_map
  or overwrite a newer generation. If concurrent map refresh can publish obsolete data, compare
  captured generation with the current generation and return an explicit conflict instead.
- [ ] Define versioned decision metadata with original snapshot identity. Proposed additive shape:
~~~json
{
  "decision_generation": "opaque-snapshot-identity",
  "current_generation": "opaque-snapshot-identity",
  "decision_freshness": "current",
  "last_prepare": {}
}
~~~
  Allowed freshness states: current, historical, unknown. Do not infer identity from wall-clock
  timestamps alone. Old snapshots lacking identity are unknown.
- [ ] On unchanged refresh, preserve current status. On changed content, retain history but mark
  historical. Delete/rename the selected target in a fixture, refresh, and verify resume does not
  advertise the retained target as a current actionable decision.
- [ ] Review index/payload atomicity with the existing confined writer and lock primitives.
  Do not introduce a new home-grown lock or widen the trusted path boundary.
- [ ] Re-run legacy sequential resume tests as well as the new interleaving controls.

**Run:** Invoke-BoundedPytest @("tests/unit/test_session_resume.py",
"tests/unit/test_session_prepare_refresh_interleaving.py").
**Accept:** neither controlled schedule loses the latest decision; old history remains readable;
fresh map and old decision are independently labeled; concurrency conflicts are bounded.
**Rollback:** retain readers for the added metadata; reverting performance changes must not revert
the lost-update guard or treat unknown decisions as current.

<a id="task-03"></a>
## Task 03 — AGT-03: One machine-action and prepare serialization contract

**Owner:** agent protocol maintainer. **Depends:** 02.
**Modify:** src/tensor_grep/cli/prepare_service.py and src/tensor_grep/cli/session_resume_service.py under src/tensor_grep.
**Create:** src/tensor_grep/cli/prepare_protocol.py.
**Tests:** tests/unit/test_prepare_next_action.py, tests/unit/test_session_resume.py;
create tests/unit/test_prepare_protocol.py.

- [ ] Inventory capsule validation_plan/validation_commands values and their producers. Do not
  assume those strings are tokenized safely or that a package script is trusted.
- [ ] Define one typed validation advice record in prepare_protocol.py:
~~~python
from dataclasses import dataclass

@dataclass(frozen=True)
class ValidationAdvice:
    status: str                 # available or unavailable
    argv: tuple[str, ...]       # nonempty only for a reviewed structured recipe
    cwd: str
    source: str                # detected recipe identity, not execution authority
    reason: str | None
~~~
- [ ] Convert known detector recipes into this record without shell=True or string splitting.
  For shell-only/unrecognized recipes return unavailable with an inspection action. Preserve
  the human-readable suggestion for review; never execute it automatically.
- [ ] Add Python/Cargo/Go/npm fixtures. Expected examples for already-supported recipes are
  pytest, cargo test, go test ./..., and the detected package-manager test recipe; when detection
  cannot establish a recipe, expect unavailable, not the Python fallback.
- [ ] Replace hardcoded on_success argv with the selected advice and its existing budgets.
  If prerequisites are missing, expose them instead of inventing a successful execution plan.
- [ ] Define stop semantics centrally: ask_user_before_editing.required OR incomplete scan OR
  non-current decision must prevent an executable edit recommendation. Normalize the known legacy
  partial/result_incomplete signals; absent required evidence must be unknown, not false.
- [ ] Introduce an opt-in versioned session response carrying one canonical prepare payload plus
  session metadata. Keep version-1 readers for raw/dataclass layouts. New serialization must not
  repeat primary/confidence/blast-radius sections both at top level and under raw.
- [ ] Route cold/session next_action generation through the same adapter. Record JSON bytes before
  and after on a frozen fixture and compare semantic fields, not merely response size.

**Run:** Invoke-BoundedPytest @("tests/unit/test_prepare_next_action.py",
"tests/unit/test_prepare_protocol.py","tests/unit/test_session_resume.py").
**Accept:** a Rust-only repository never gets unconditional pytest advice; legacy snapshots
remain readable; incomplete/historical input cannot authorize an edit; tool code executes no
suggested commands. CLI/session canonical payloads agree.
**Rollback:** retain version-1 serialization during migration; disable the new version without
removing the corrected validator selection.

<a id="task-04"></a>
## Task 04 — AGT-04: Edit-ticket population and budgets

**Owner:** edit verification maintainer; security design review mandatory.
**Modify:** src/tensor_grep/cli/edit_ticket_service.py.
**Create:** docs/design/2026-09-07-edit-ticket-population.md first;
create tests/unit/test_edit_ticket_population.py.
**Reuse tests:** tests/unit/test_edit_ticket_service.py.
**Prerequisite:** reconcile the existing S1/S5 identity/atomicity threat model and F5/F6 scope.
This task does not declare unsigned/caller-controlled tickets trusted.

- [ ] Write a population table before choosing a walker: tracked source/config including dotfiles;
  explicitly allowed new files; untracked source additions outside allowed scope; ignored dependency
  trees; tool-owned state; symlinks/junctions; unreadable paths. State treatment for each.
- [ ] Use Git-tracked identity when available with a bounded NUL-delimited output reader.
  Preserve tracked .github/.gitignore files regardless of dot prefix. Define non-Git repository
  behavior separately; do not silently substitute a weaker population.
- [ ] Enumerate incrementally. Prune only reviewed excluded directories before descending, while
  still detecting new source files outside the declared edit scope. Avoid sorted(rglob(...)).
- [ ] Add finite file-count, per-file-byte, aggregate-byte and absolute-deadline limits before
  reading. Carry the same budget through enumeration and hashing; do not reset per helper.
  Freeze numerical defaults in the reviewed design from bounded corpus measurements.
- [ ] Model a non-success result explicitly:
~~~json
{
  "verified": false,
  "status": "incomplete",
  "reason": "aggregate_byte_limit",
  "population_policy": "reviewed-policy-version",
  "scanned_files": 12,
  "scanned_bytes": 8192
}
~~~
- [ ] Test tiny lowered limits with small fixtures; never create huge files just to exhaust memory.
  Spy on enumeration/hash calls to prove dependency trees were not traversed and tracked dotfiles
  were hashed. Add outside-scope source addition/removal controls.
- [ ] Add symlink/junction and changing-file controls only through approved confined primitives.
  Compare opened identity and stable content evidence; stat-before-open alone is insufficient.
- [ ] Re-run valid edit, unrelated source edit, no-op, malformed ticket, and legacy-reader controls.
  Review every production caller before presenting the result as edit verification authority.

**Run:** Invoke-BoundedPytest @("tests/unit/test_edit_ticket_service.py",
"tests/unit/test_edit_ticket_population.py").
**Accept:** reviewed source population protected, cache noise handled explicitly, no unbounded
walk/read, exhaustion never PASS, no relaxation of identity/signature/atomicity requirements.
**Rollback:** keep verification fail-closed; a compatibility fallback may refuse, not claim success
for an unverified population.

<a id="task-05"></a>
## Task 05 — AGT-05: Task intent and evidence-based confidence

**Owner:** ranking maintainer. **Depends:** 01.
**Modify only after pins:** src/tensor_grep/cli/agent_capsule_targets.py, src/tensor_grep/cli/agent_capsule_confidence.py,
src/tensor_grep/cli/agent_capsule_builder.py and relevant scoring helpers under src/tensor_grep.
**Tests:** tests/unit/test_agent_capsule_hardcases.py;
create tests/unit/test_agent_target_intent.py.

- [ ] Capture current file and symbol order for the existing hardcases before editing a scorer.
- [ ] Add the audit query and independently reviewed paraphrases to DEVELOPMENT data. Include a
  class-definition question where SearchResult is correct so suppressing that symbol globally fails.
- [ ] Add negative/ambiguous requests and changed-repository variants. Do not tune against the
  holdout introduced by 01.
- [ ] Trace features for the wrong primary: lexical/path matches, centrality, target promotion,
  and post-ranking confidence adjustments. Record which stage changes the candidate; do not infer
  causality merely from the displayed reasons.
- [ ] Test one hypothesis at a time, such as preferring task-relevant implementation evidence over
  generic graph centrality. Keep a no-change control and ablate the new feature.
- [ ] Produce calibration bins with counts and observed top-1 correctness, plus selective accuracy
  versus answer coverage. A useful starting report schema is:
~~~json
{
  "confidence_kind": "heuristic",
  "complete_tasks": 0,
  "incomplete_tasks": 0,
  "wrong_confident_primary": 0,
  "answered_tasks": 0,
  "abstained_tasks": 0,
  "calibration_bins": []
}
~~~
  Zero complete tasks is insufficient evidence, not perfect accuracy.
- [ ] Keep heuristic labeling unless an independently evaluated calibration model justifies
  probability language. Inspect false abstentions so “always ask” cannot win.
- [ ] Run existing ordering pins and affected confidence/validation tests. Document every intended
  reordered task; unrelated legitimate reordering is a stop finding.

**Run:** Invoke-BoundedPytest @("tests/unit/test_agent_capsule_hardcases.py",
"tests/unit/test_agent_target_intent.py").
**Accept:** named development misses improve, held-out risk does not materially worsen, and
existing legitimate order pins remain intact. Report uncertainty rather than promise a universal fix.
**Rollback:** revert the single ranking hypothesis; retain measurement and regression cases.

<a id="task-06"></a>
## Task 06 — P9: Warm reuse and dormant-index disposition

**Owner:** session maintainer. **Depends:** 02/03.
**Modify:** src/tensor_grep/cli/prepare_service.py, src/tensor_grep/cli/session_resume_service.py, src/tensor_grep/cli/session_store.py.
**Inspect:** src/tensor_grep/cli/repo_map_cache.py, src/tensor_grep/cli/session_daemon.py, src/tensor_grep/core/semantic_index.py.
**Create:** tests/unit/test_prepare_map_reuse.py;
docs/design/2026-09-07-cache-ownership.md.

- [ ] Draw which existing component owns repository-map, response, native trigram, AST, and
  semantic chunk state. Record key, lifetime, invalidation, byte caps, and writers.
- [ ] Record dormant semantic_index.py as library-only. Choose retain/document, safely adopt,
  or separately approve deprecation. No in-repo callers is not public-API deletion authority.
- [ ] Extract a prepare-from-map service. Keep the existing cold wrapper building a map once.
  Pass only a map whose identity/freshness has been checked by the owning session service.
- [ ] Add a spy control: unchanged warm prepare makes zero full build_repo_map calls. Cold/warm
  results must match after removing only documented transport/time metadata.
- [ ] Test added/deleted/modified files, changed config/grammar, and a truncated cached map.
  A truncated map must retain rescue/incompleteness behavior; never assume it is a complete universe.
- [ ] Measure lock hold/wait time. If material, capture generation under lock, compute outside,
  then compare generation under lock before publishing. On conflict retry at most once within the
  original remaining deadline, else return explicit conflict/incomplete.
- [ ] If persistent BM25 adoption is justified, use bounded reads and one generation-safe
  publication mechanism. Include additions in the census identity and chunker/model configuration
  in the key. Test corruption and interrupted publication before enabling any reader.
- [ ] Report cold/warm end-to-end times on identical snapshots, including freshness checks and
  serialization. Treat the existing <150ms ambition as workload-scoped, not a universal gate.

**Run:** Invoke-BoundedPytest @("tests/unit/test_prepare_map_reuse.py",
"tests/unit/test_session_resume.py","tests/unit/test_semantic_index.py").
**Accept:** zero redundant warm map builds, stale computation cannot publish, resource caps hold,
and persistent ownership is explicit. New watcher/accept-path projects retain their existing gates.
**Rollback:** use the correct cold path with disclosed fallback; never serve stale caches for speed.

<a id="task-07"></a>
## Task 07 — P13: Import boundaries and shared execution services

**Owner:** architecture maintainer. **Track:** behavior-preserving.
**Read:** docs/design/2026-08-19-split-floor-escape.md.
**Modify:** src/tensor_grep/cli/main.py, src/tensor_grep/cli/mcp_server.py, src/tensor_grep/cli/mcp_symbol_tools.py.
**Create after baseline:** src/tensor_grep/cli/find_service.py,
docs/design/2026-09-07-service-boundaries.md.
**Tests:** tests/unit/test_find_command.py, tests/unit/test_mcp_tg_find.py,
tests/unit/test_bare_call_ratchet.py.

- [ ] Inventory actual imports and local/dynamic late lookups. Group by semantic responsibility,
  not filename size. Identify all monkeypatch targets for _execute_find.
- [ ] Implement P13's frozen dependency baseline using the reviewed linter choice. Include stable
  edge identities; adding a dependency must fail unless reviewed. Do not sanction entire modules.
- [ ] Pin CLI/MCP outputs for plain search, BM25, semantic unavailable, malformed options, partial
  scan, and backend error before extraction. Preserve existing exception categories.
- [ ] Move _execute_find's computation to find_service.py with explicit dependencies; keep
  main._execute_find as a compatibility wrapper whose binding behavior matches the baseline.
- [ ] Make both front doors call that service. Do not let a service import CLI command registration.
  Keep presentation and protocol sanitization in adapters.
- [ ] For one symbol-tool family, replace parent-module access with a narrow explicit dependency
  object. Retain server wrappers until consumers and tests migrate together.
- [ ] Check supported import orders and patch calls. An unchanged test count can still be false
  green if a monkeypatch no longer controls execution; assert the dependency spy is called.
- [ ] Run import/bare-call/file-size ratchets without raising ceilings. Compare bounded import and
  startup measurements only after behavioral parity; moving a function alone is not a speed claim.

**Run:** Invoke-BoundedPytest @("tests/unit/test_find_command.py",
"tests/unit/test_mcp_tg_find.py","tests/unit/test_bare_call_ratchet.py").
**Accept:** fewer cross-layer dependencies with byte/semantic contracts preserved; no hidden
unpatched copies, no public API deletion, no new generic plugin framework.
**Rollback:** restore adapters/service wiring as one unit; retain frozen baseline evidence.

<a id="task-08"></a>
## Task 08 — AGT-06: Shared vendored-root probe

**Owner:** CLI maintainer. **Track:** behavior-preserving.
**Modify:** src/tensor_grep/cli/bootstrap.py, src/tensor_grep/cli/main.py under src/tensor_grep.
**Create:** src/tensor_grep/io/root_probe.py;
tests/unit/test_root_probe_parity.py.
**Reuse policy:** src/tensor_grep/io/scan_limits.py.

- [ ] Capture differences between bootstrap's boolean probe and CLI's sorted diagnostic names:
  empty/stdin/flag-looking inputs, OSError handling, casing, multiple roots and early return.
- [ ] Add a frozen fixture table covering vendored child, ordinary Node root, tool cache, nonexistent
  root, unreadable root, mixed case, and several roots. Compare each old adapter to its own baseline.
- [ ] Implement one lightweight iterator of qualifying root/name observations using the shared name
  policy. Import only standard-library/lightweight I/O policy modules.
- [ ] Bootstrap consumes at most the first match; CLI collects/deduplicates/sorts for diagnostics.
  Leave argv and SearchConfig interpretation in their existing adapters.
- [ ] Instrument directory visits to prove boolean short-circuit survives. Preserve OSError policy
  exactly rather than turning a cleanup into a new refusal behavior.
- [ ] Compare plain and JSON refusal messages and exits on equivalent supported requests.
- [ ] Verify bootstrap import budget with test_bootstrap_fast_path_imports.py.
- [ ] Search sibling/native guards and document intentional platform differences; do not silently
  extend this Python-only refactor into native route changes.

**Run:** Invoke-BoundedPytest @("tests/unit/test_root_probe_parity.py",
"tests/unit/test_cli_bootstrap.py","tests/unit/test_bootstrap_fast_path_imports.py").
**Accept:** both adapters preserve their outputs and cost shape while sharing directory policy.
**Rollback:** revert the helper and both adapter changes together.

<a id="task-09"></a>
## Task 09 — AGT-07: Shared completeness evidence

**Owner:** result-contract maintainer. **Track:** behavior-preserving.
**Modify:** src/tensor_grep/cli/incompleteness.py, src/tensor_grep/cli/main.py.
**Create:** src/tensor_grep/core/completeness.py;
tests/unit/test_completeness_projection.py.
**Reuse:** tests/unit/test_mcp_incomplete_envelope.py,
tests/unit/test_result_incomplete_payload_layer.py.

- [ ] Enumerate producers and consumers of partial, result_incomplete, truncated, scan_limit,
  omitted sections, nested roots, and budget_remediable. Record the public meaning of each.
- [ ] Freeze the truth table below before moving predicates:
~~~text
complete empty scan       -> complete; no fabricated matches
display cap only          -> output limited; scan still complete
file scan cap             -> scan incomplete; disclose scan limit
deadline                  -> scan/assembly state as observed; preserve cause
unreadable path           -> incomplete; not repaired merely by a larger budget
mixed roots, one partial  -> aggregate cannot claim all roots complete
unknown legacy evidence   -> preserve unknown; never manufacture complete
~~~
- [ ] Define an internal immutable record with separate scan_state, output_state, causes,
  retry_kind and provenance. Use enums/typed values rather than another overloaded boolean.
- [ ] Write pure adapters from existing payload forms to that record and back to legacy CLI/MCP
  fields. Keep original spellings and deterministic cause ordering.
- [ ] Migrate one consumer first; run every truth-table row through old and new projections.
- [ ] Migrate the sibling consumer only after equivalence. Preserve result-before-exit ordering
  and the distinction between output caps and exit-2 scan failures.
- [ ] Mutation control: force an unreadable-path cause to budget-remediable; the test must fail.
  Force a nested partial result to complete; the test must fail.
- [ ] Update capability docs only for an actual contract change; this task intends none.

**Run:** Invoke-BoundedPytest @("tests/unit/test_completeness_projection.py",
"tests/unit/test_mcp_incomplete_envelope.py",
"tests/unit/test_result_incomplete_payload_layer.py").
**Accept:** one internal evidence interpretation with unchanged supported projections and exits.
**Rollback:** migrate adapters back independently; do not remove legacy readers prematurely.

<a id="task-10"></a>
## Task 10 — MCP-SURFACE: Bounded disclosure and negotiated structured results

**Owner:** MCP maintainer. **Depends:** 01/03/09 and existing Task-4 gate.
**Modify:** src/tensor_grep/cli/mcp_server.py, src/tensor_grep/cli/orient_capsule.py, src/tensor_grep/cli/repo_map_output_budget.py.
**Create:** docs/design/2026-09-07-mcp-response-contract.md;
tests/unit/test_mcp_response_contract.py.
**Research:** R1/R3/R4. No default protocol upgrade is planned.

- [ ] Capture actual tools/list and tools/call output from the installed SDK/client pair in a tiny
  fixture. Record negotiated protocol revision, tool schemas, content blocks, and error behavior.
  A Python str annotation alone does not prove the SDK lacks structured wire output.
- [ ] Inventory existing legacy and meta-tool surfaces; keep current default registration.
  Identify which parameters actually apply to each action and reject irrelevant combinations
  explicitly in the opt-in contract.
- [ ] Define concise/detailed response profiles and a COMPLETE serialized payload byte cap.
  Keep existing snippet-token budgets unchanged; label estimates as estimates.
- [ ] Prioritize target, incompleteness, confidence, next safe action, and provenance before optional
  snippets. If mandatory metadata alone exceeds the cap, emit a bounded explicit limit error.
- [ ] Prototype bounded follow-up references for omitted source. Bind each reference to root,
  snapshot identity, parameters, expiry and range. A stale/tampered/cross-root reference must fail
  or force explicit refresh, not return silently unrelated source.
- [ ] Add structuredContent/outputSchema only for supported negotiated SDK/protocol versions.
  Preserve compatible text JSON for old clients where required. Test both success and errors.
- [ ] Evaluate concise responses with 01's task outcomes and tokens; fewer bytes must not hide the
  missing fact needed for a correct edit. Do not conflate tools/list cursors with result pagination.
- [ ] Security-review reference confinement/bounds and wire error sanitization. Keep network
  listeners, new default tool catalogs and durable task services out of this slice.

**Run:** Invoke-BoundedPytest @("tests/unit/test_mcp_response_contract.py",
"tests/unit/test_mcp_contract_fixes.py").
**Accept:** negotiated schemas validate, bytes stay bounded, required warnings survive,
follow-ups cannot escape their snapshot/root, and legacy clients still work.
**Rollback:** disable opt-in profile/structured result path; preserve existing default wire.

<a id="task-11"></a>
## Task 11 — AGT-08: Explain actual ranking

**Owner:** retrieval maintainer. **Depends:** 01.
**Modify:** src/tensor_grep/core/reranker.py, src/tensor_grep/core/retrieval_fusion.py, src/tensor_grep/cli/main.py.
**Tests:** tests/unit/test_find_why_ranked.py;
create tests/unit/test_rank_explanation_contributions.py.

- [ ] Pin ranking order with why-ranked disabled/enabled on the same fixture.
- [ ] Record contributions at their actual computation points rather than reconstructing them from
  rendered snippets. Include absent/degraded dense state, lexical scores, path channel, fusion mode,
  post-rank transformations, and tie-break reason.
- [ ] Define an additive explanation record such as:
~~~json
{
  "channels": {
    "bm25": {"rank": 2, "score": 1.7},
    "dense": {"status": "unavailable", "reason": "model_not_installed"}
  },
  "fusion": {"mode": "max", "k": 60},
  "tie_break": "stable_chunk_order",
  "post_rank_transforms": []
}
~~~
  The numbers above are fixture examples, never invented live scores.
- [ ] Thread that record through find results while retaining the existing explanation strings
  for compatibility. Do not compute scores twice.
- [ ] Add fixtures where lexical wins, dense wins using a fake tiny encoder, a deterministic tie
  occurs, and dense is unavailable. Explanation must name the contributor that actually ran.
- [ ] Verify max versus sum fusion is represented correctly; current default max must not be
  described as summed RRF.
- [ ] Verify why-ranked cannot alter the returned order or trigger model installation.
- [ ] Measure incremental serialization cost on a bounded fixture; no duplicate full chunk corpus.

**Run:** Invoke-BoundedPytest @("tests/unit/test_find_why_ranked.py",
"tests/unit/test_rank_explanation_contributions.py").
**Accept:** explanations account for real decisions and failures, with unchanged order.
**Rollback:** retain legacy explanations while disabling additive contribution details.

<a id="task-12"></a>
## Task 12 — P12: Dense retrieval optimization by measurement

**Owner:** retrieval maintainer. **Depends:** 01/11.
**Inspect/modify:** src/tensor_grep/core/retrieval_dense.py, src/tensor_grep/core/reranker.py, src/tensor_grep/cli/main.py.
**Tests:** tests/unit/test_retrieval_dense.py;
create benchmarks/profile_dense_stages.py only if current profiling lacks these stages.

- [ ] Confirm actual model/operator path from StaticModel loader and Model2Vec documentation.
  Correct the old premise that ONNX transformer inference is necessarily the bottleneck.
- [ ] Instrument startup, load, chunking, corpus encode, query encode, score, top-k and serialization
  separately with identical corpus/model hashes. Run dense measurements in CI/cloud.
- [ ] First use a tiny fake encoder in unit tests to count load/encode calls without real models.
- [ ] Compare model/corpus reuse keyed by content/config/model identity and bounded memory.
  Include invalidation cost; no speed claim from skipping freshness work.
- [ ] If top-k sorting dominates, compare a stable bounded selection against the current full sort.
  Tie order must remain identical, including repeated equal scores.
- [ ] Only then test float16/int8 or an alternative runtime in an isolated optional arm. Record
  model sizes, supported CPU instructions, platform availability and numerical ranking changes.
  AVX-512 must not become a baseline machine requirement.
- [ ] Retain the BM25-only fallback and corrupt-model error distinction; never auto-fetch a model.
- [ ] Accept the simplest candidate exceeding measured noise while preserving named correctness
  pins and held-out quality. NO BENEFIT is a valid P12 research result; do not ship speculative deps.

**Run:** Invoke-BoundedPytest @("tests/unit/test_retrieval_dense.py",
"tests/unit/test_retrieval_dense_fetch.py").
**Accept:** exact route/corpus evidence, stable ties, bounded reuse, unchanged fallback semantics.
**Rollback:** use the existing float32/runtime path; old model artifacts remain readable.

<a id="task-13"></a>
## Task 13 — P14: Per-fact provenance and precise-provider pilot

**Owner:** symbol intelligence maintainer. **Depends:** 01/02/09.
**Modify:** src/tensor_grep/cli/lang_registry.py, src/tensor_grep/cli/repo_map.py, src/tensor_grep/cli/mcp_symbol_tools.py.
**Reuse:** src/tensor_grep/cli/lsp_external_provider.py,
benchmarks/run_provider_navigation_bakeoff.py.
**Create:** tests/unit/test_symbol_fact_provenance.py;
docs/design/2026-09-07-precise-provider-pilot.md.

- [ ] Inventory existing per-fact and aggregate evidence first. Distinguish parser-confirmed
  definition, parser-confirmed in-file call, cross-file prefilter candidate, and provider result.
- [ ] Define additive facts without inventing a probability:
~~~json
{
  "provenance": {
    "provider": "native",
    "method": "parser",
    "source_revision": "snapshot-identity",
    "freshness": "current",
    "confidence_kind": "evidence-tier"
  }
}
~~~
  Cache is a storage source, not a semantic method; cached parser facts retain parser provenance.
- [ ] Preserve unknown revisions/freshness on legacy results. Do not treat filesystem mtime age as
  proof the fact matches current content. Carry explicit disagreement between providers.
- [ ] Adapt defs/refs/callers and their MCP surfaces; keep path/line/symbol fields unchanged.
- [ ] Choose one existing supported language and test precise references through the existing LSP
  provider. Pin alias, same-name symbol, cross-file import, missing provider, timeout, and stale
  document controls in the existing provider bakeoff.
- [ ] Compare with native candidates on identical fixtures. Provider absence must retain labeled
  native fallback; no automatic server installation or unbounded process lifetime.
- [ ] Consider SCIP read-only ingestion only if the pilot shows an unmet use case. A separate
  reviewed slice must specify index revision, size caps, path confinement, schema, and stale-index
  rejection before parsing arbitrary uploaded indexes.
- [ ] Remove first-to-market assertions unless a documented competitor census substantiates the
  exact narrow claim. Existing source-based providers are not evidence of tensor-grep superiority.

**Run:** Invoke-BoundedPytest @("tests/unit/test_symbol_fact_provenance.py").
Run the existing provider fixture subset in approved CI with provider versions pinned.
**Accept:** consumers distinguish evidence/freshness per fact; precise pilot improves a measured
gap without suppressing uncertain fallback evidence.
**Rollback:** disable optional provider confirmation; preserve additive provenance and native route.

<a id="task-14"></a>
## Task 14 — P7: AST cache census before new cache work

**Owner:** AST maintainer. **Depends:** 01.
**Inspect:** src/tensor_grep/backends/ast_backend.py, src/tensor_grep/cli/ast_scan.py, src/tensor_grep/cli/ast_workflows.py,
rust_core/src/backend_ast.rs and backend_ast_workflow.rs.
**Tests:** create tests/unit/test_ast_cache_route_matrix.py; reuse existing AST backend tests.

- [ ] Build a route matrix: native Python extension, Python AstBackend, ast-grep wrapper, Rust CLI.
  For each, list parser/query/source/node-index/result cache, scope, key, limits and invalidation.
- [ ] Confirm existing caches at ast_backend.py:150/:347/:487. Do not write a second cache for the
  same route merely because P7's older description implies none exists.
- [ ] Use tiny fixtures and counters for parse/compile/match work across repeated requests.
  Separate same-process reuse from a new CLI process.
- [ ] Profile one missing-reuse case in CI. Record rule/language/flags, file size/count, grammar
  version, cold/warm state and process startup. The old <5ms target is meaningful only with scope.
- [ ] If useful, key the missing cache by content identity, rule identity, language/grammar version,
  and all match semantics. Reuse the existing bounded cache owner.
- [ ] Test changed rule, changed source with preserved timestamp, flag change, grammar mismatch,
  corrupt cache and entry eviction. A miss must recompute correctly, not look like no matches.
- [ ] For persisted state, use the reviewed confined publication/read limits. Any writer/lock
  change gets an independent security review.
- [ ] If existing caches cover the measured workload, close the investigation with the matrix and
  measurements; retain no unnecessary implementation work.

**Run:** Invoke-BoundedPytest @("tests/unit/test_ast_cache_route_matrix.py").
Native compilation/performance belongs in approved CI.
**Accept:** route-specific measured benefit or justified no-change disposition; identical matches.
**Rollback:** disable the new cache layer and recompute; never retain stale results for latency.

<a id="task-15"></a>
## Task 15 — P10: Execution evidence tied to real changes

**Owner:** evidence maintainer; independent security design review.
**Depends:** 02/03/04/13 and existing F5/F6 requirements.
**Modify/reuse:** src/tensor_grep/cli/evidence_receipt.py, src/tensor_grep/cli/evidence_signing.py,
src/tensor_grep/cli/diff_impact.py, benchmarks/run_patch_bakeoff.py.
**Create:** docs/design/2026-09-07-validation-execution-evidence.md;
tests/unit/test_validation_execution_receipt.py.

- [ ] Census current receipt, signature, checkpoint and review-bundle fields. Identify what is
  observed by a runner versus supplied by a caller.
- [ ] Define a separate execution record containing repo revision, diff digest, argv, cwd,
  environment/tool identity, start/end, exit, timeout, collected-test IDs/count, output digest and
  truncation. Link it to the decision generation and existing signing contract.
- [ ] Keep validation advice from 03 separate from approved execution. Never launch a repository
  command solely because a context capsule recommended it.
- [ ] Reuse the bounded patch/validation runner where its trust model fits. Any new runner must
  specify process-tree containment, output draining/caps, timeout, cleanup and network policy on
  each supported platform; Windows Job behavior requires its own approved primitive design.
- [ ] Add controls for nonzero exit, timeout, zero collected tests, malformed output, replay after
  source change, altered argv, untrusted signer, and tampered receipt. All must fail or be explicitly
  unavailable rather than “tests passed”.
- [ ] Distinguish associated_tests from measured_coverage. An import/caller graph can recommend
  tests; only an actual coverage instrument can assert which symbols executed.
- [ ] Add the positive control: the reviewed known-good patch executes the named tests at the
  recorded revision and its trusted receipt verifies. Producer and consumer exit codes are retained.
- [ ] Review refusal paths before any enforcing integration. Do not describe a signature as proof
  that an untrusted signer told the truth.

**Run:** Invoke-BoundedPytest @("tests/unit/test_validation_execution_receipt.py").
Platform containment controls run in their approved real CI environments.
**Accept:** execution is independently attributable to the exact change; planned/observed/covered
remain separate; replay and zero-test controls cannot pass.
**Rollback:** disable enforcement and label evidence unavailable; never substitute self-attestation.

<a id="task-16"></a>
## Task 16 — P15: Generated capability facts and truthful positioning

**Owner:** documentation maintainer. **Depends:** none for current facts.
**Modify:** README.md, docs/tool_comparison.md, docs/harness_api.md.
**Create:** scripts/render_capability_reference.py;
tests/unit/test_capability_reference.py.
**Read before CI edits:** docs/CI_PIPELINE.md.

- [ ] Record claims from the audit: entry-point startup/delegation, map cap 2000 versus stale 512,
  language tiers, AST cache lifetime, GPU experimental status, and confidence meaning.
- [ ] Use LANGUAGE_REGISTRY and existing capability descriptors as producers. Separate static
  product support from environment-installed grammar/provider availability.
- [ ] Generate a bounded marked table section without importing heavy optional models or running
  providers. Support --check to compare generated text without writing.
- [ ] Use a mutation control that changes one registry capability in a fixture: --check must detect
  stale generated text. Avoid hardcoding the count “10” in another source of truth.
- [ ] Correct universal “no subprocess overhead” wording to route-specific behavior. Preserve
  ripgrep-subset/GPU/optional-dependency caveats.
- [ ] Review the tagline against measured user workflows and Exa R1/R4/R5. Lead with useful
  edit-readiness outcomes; do not claim first/best/fastest without corresponding evidence.
- [ ] Wire the generation check into the existing applicable docs/code gate only after reviewing
  CI_PIPELINE.md. Do not create an extra always-on heavyweight workflow.
- [ ] Link each generated fact to its producer and each benchmark claim to a dated receipt.
  P4 remains closed; this is its already-owned P15 residual work.

**Run:** Invoke-BoundedPytest @("tests/unit/test_capability_reference.py").
Also: uv run --no-sync python scripts/render_capability_reference.py --check.
**Accept:** generated sections fail on drift, current route/limit claims match code, optional
capabilities are correctly labeled, and performance language cites real evidence.
**Rollback:** preserve the last correct generated table and source references; disable broken
generation wiring without reverting to false prose.

## Completion and review checklist

- [ ] Every new finding has exactly one owner in BACKLOG.md and TASK_BOARD.md; existing P/S/task
  owners are linked rather than duplicated.
- [ ] Each package has baseline, controls, evidence, implementation files, tests and rollback.
- [ ] Dependency producers exist before consumers. In particular generation precedes warm publication,
  completeness precedes new response projections, and actual execution precedes enforcing receipts.
- [ ] Refactors preserve observable behavior; intended bug fixes state their exact changed behavior.
- [ ] Existing public APIs, legacy schemas, ranking pins and mandatory platform/security gates remain.
- [ ] No benchmark speed/quality/first-to-market claim is inferred from source presence or a unit pass.
- [ ] No new cache/runtime/provider is selected without the named profile/census and correctness control.
- [ ] Final review inspects exact Git bytes and final PR metadata. Record artifact hash method.
- [ ] Post-merge verification uses the merged artifact; campaign closeout uses the actually published
  wheel only when a release campaign has really occurred.

The audit/planning request is complete when these documents and tracker entries are validated.
Implementation tasks remain open until their own acceptance and repository review process completes.
