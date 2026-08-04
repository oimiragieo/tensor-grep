# Backlog Closeout Campaign Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to execute this plan task-by-task. Every implementation task requires a fresh implementer, specification review, and quality/security review. Use `superpowers:test-driven-development` and `superpowers:verification-before-completion` for every code slice.

**Goal:** close every AI-actionable backlog item and tracker contradiction with evidence, while preserving explicit CEO/financial gates.
**Architecture:** sequential release-safe programs built in isolated worktrees; shared preparation and verification services sit behind thin CLI/MCP adapters; language navigation dispatch becomes registry-driven before adding five extractors; bounded graph projection and Git-diff impact reuse those services without introducing a graph runtime.
**Tech stack:** Python 3.11+, Typer, pytest, Ruff, mypy, Rust/Clap native front door, tree-sitter grammars, FastMCP, GitHub Actions, PyPI.
**Design:** `docs/plans/2026-08-02-backlog-closeout-design.md`

**Execution status (2026-08-03):** APPROVED. Round 60 incorporates eight adversarial-security `FIX-FIRST` findings and one independent TDD boundary finding. Cursor Auto's contradiction loop, independent TDD, and Sol cleared the status-stamped pair; final raw hashes are recorded outside these self-referential plan bytes in `MEMORY.md` and the dated CEO audit. Any further plan edit invalidates approval. Production starts with Task 2A's independent RED.

## Global execution contract

- Run all commands from an isolated worktree created under ignored `.claude/worktrees/`.
- Before every dispatch, refresh `git status`, open PRs/issues, newest `main` CI, latest GitHub release, and PyPI version.
- Do not merge while the newest `main` CI run is not `completed` or while the latest release is not yet served by PyPI.
- Do not start another release-affecting build when more than five PRs are undrained or `main` is red.
- Locally run only focused/scoped tests. Put full test, eval, benchmark, Cargo, and large-boundary matrices in CI/cloud.
- Wrap potentially hanging tests with a 120-second process timeout and pytest's 15-second per-test timeout where available.
- New CLI commands must update all four registrations: `src/tensor_grep/cli/commands.py`, `rust_core/src/main.rs` command enum, `rust_core/src/main.rs` dispatch, and `tests/e2e/test_routing_parity.py::PUBLIC_TOP_LEVEL_COMMANDS`; Typer registration in `src/tensor_grep/cli/main.py` is the executable implementation.
- Any MCP exposure is a fifth registration/wire-contract site and requires a contract bump plus adversarial review.
- After each rebase involving shared tests or registries, union assertions and rerun the complete affected test group even when Git reports a clean rebase.
- A build agent's test report is a hypothesis. Re-run in the real environment with `uv run --no-sync` after harvesting.

## Task 0: approve this plan through the thinktank loop

**Files:**

- Review: `docs/plans/2026-08-02-backlog-closeout-design.md`
- Review: `docs/plans/2026-08-02-backlog-closeout-implementation-plan.md`
- Modify: the same files for must-fix findings

**Step 1: dispatch three independent seats**

- Architecture/contract seat: dependency order, duplication, compatibility, release boundaries.
- Adversarial security seat: path confinement, symlinks, TOCTOU, schema confusion, command injection, lock and receipt trust.
- TDD/evaluation seat: falsifiable red arms, positive controls, output/ranking pins, CI placement, dogfood adequacy.

Each seat returns exactly `SHIP` or `FIX-FIRST` and, for every finding, `file:line`, a reproduction/counterexample, and the smallest acceptable plan change.

For every round, the canonical artifacts are the raw working-tree bytes at
`C:\\dev\\projects\\tensor-grep\\.claude\\worktrees\\task2-tracker-truth\\docs\\plans\\2026-08-02-backlog-closeout-design.md`
and the sibling implementation-plan path. Compute each with PowerShell `Get-FileHash -Algorithm SHA256`; reviewers must report both exact hashes before their verdict. Git clean-filter or another worktree's bytes are not substitutes.

**Convergence record:** round 15 reached three-seat `SHIP`, and exact-hash rounds 16–18 repaired later
architecture/security/TDD blockers. Task 2 then produced new live evidence: #89 and #90 fail when the
WSL launcher selects a Windows-native executable but passes it WSL-domain paths. That evidence expires
the Round 18 approval. Round 25 then reached three-seat `SHIP` on the repaired WSL scope, but the user
added the graph-coding research/build slice before the status stamp. Tasks 3–16 remain frozen while the
enlarged design is reviewed to three-seat `SHIP`, status-stamped, and confirmed against one exact hash
pair. Rounds 52–53 completed that approval, but the mandatory read-only pre-build deep dive then found
concrete Task 2A plan-to-code mismatches recorded as design Round 54. Task 2A production and Task 2 PR
merge were frozen until the repaired pair again reached `SHIP`, a new status stamp, and final exact-hash
confirmation. Round 60 has now closed that gate through Cursor Auto contradiction review, independent
TDD `SHIP`, Sol substantive `SHIP`, and status-stamp confirmation. The Round-58/59 checkpoint hashes are
superseded; the final raw-byte pair lives in `MEMORY.md` and the dated CEO audit. Any plan-body change
reopens the gate. PR #911 push/CI and Task 2A implementation remain distinct later artifacts.

**Step 2: apply every must-fix**

Edit both documents with `apply_patch`. Record the review round in the design's thinktank section. Do not resolve a finding by deleting or weakening its acceptance test.

**Step 3: re-review from a fresh framing**

Repeat all three seats until all return `SHIP`. A no-verdict seat is failed and is replaced; it is not approval and not a blocker.

After three seats approve one pair, change only the design status and convergence record, recompute both raw-byte hashes, and have all three seats perform a final exact-hash confirmation with no edits. Any finding or byte change reopens the loop.

**Step 4: commit the approved planning artifacts**

```powershell
git add docs/plans/2026-08-02-backlog-closeout-design.md docs/plans/2026-08-02-backlog-closeout-implementation-plan.md
git commit -m "docs: plan evidence-first backlog closeout"
```

## Task 1: drain PR #910 and rebase onto tracker truth

**Files expected in #910:**

- `AGENTS.md`
- `docs/TASK_BOARD.md`

**Step 1: independently audit the PR**

```powershell
gh pr diff 910
gh pr checks 910
gh pr view 910 --json isDraft,mergeStateStatus,headRefOid,files,commits
```

Verify that it restores exactly one `BLOCKED — environment` heading, does not move CEO-gated entries into the active queue, closes stale #904 state, and does not overwrite newer unrelated documentation.

**Step 2: verify the release gate**

```powershell
gh run list --branch main --workflow ci.yml --limit 1 --json databaseId,status,conclusion,headSha
gh release list --limit 1
uvx --from tensor-grep@1.102.1 tg --version
```

Expected: newest main run `completed`; release and installed wheel agree.

**Step 3: publish the independent verdict as a PR comment**

Post `SHIP` with the exact structural checks performed. If any check fails, post `FIX-FIRST` and repair #910 before continuing.

**Step 4: ready and merge one PR**

```powershell
gh pr ready 910
gh pr merge 910 --squash --delete-branch
```

**Step 5: verify merged artifact and rebase campaign**

```powershell
git fetch origin main
git rebase origin/main
git status --short --branch
```

Re-run the section-count assertions against the merged `origin/main`, not merely the PR branch.

## Task 2: reconcile the live tracker and close stale contradictions

**Files:**

- Modify: `docs/TASK_BOARD.md`
- Modify: `docs/BACKLOG.md`
- Modify: `docs/SESSION_HANDOFF.md`
- Modify: `MEMORY.md`
- Modify: `docs/audits/2026-08-02-ceo-backlog-update.md` to mark it interim/superseded by the canonical board plus reconciliation receipt
- Create: `docs/audits/2026-08-02-backlog-reconciliation.md`
- Create: `tests/unit/test_backlog_tracker_truth.py`
- Review without speculative edits: `AGENTS.md`, `docs/CONTRACTS.md`, `src/tensor_grep/cli/main.py`, and `docs/audits/2026-08-01-backlog-verification-receipts.md`; the executable exit contract and prior appended #859 correction were already current.

**Step 1: write deterministic document-invariant tests**

Add `tests/unit/test_backlog_tracker_truth.py` and one documented `## Canonical status index` block near the top of `docs/TASK_BOARD.md`. Its first nonblank line has the exact unique grammar `Canonical status index version: YYYY-MM-DD.N`; `docs/SESSION_HANDOFF.md` carries the same exact metadata line once. Historical narrative elsewhere is deliberately outside this parser. Each canonical row has exactly this grammar: `- [ ] **ID** — Status: TOKEN; PR: VALUE; Trigger: TEXT` or the checked equivalent, where `TOKEN` is exactly one of `IN_FLIGHT|READY|BLOCKED|CEO_GATED|DEMAND_GATED|SHIPPED|RETIRED`; `VALUE` is exactly `PR #NNN` for `SHIPPED`/`IN_FLIGHT` and `none` otherwise; and `TEXT` is nonempty (`none` only for terminal `SHIPPED`/`RETIRED`). The checkbox is checked if and only if status is `SHIPPED` or `RETIRED`. The parser rejects missing/duplicate/malformed version metadata, duplicate IDs, duplicate/missing canonical sections, malformed/multiline rows, unknown tokens, checkbox/status disagreement, missing/multiple PRs, and ambiguous PR-field `#NNN` values that are not prefixed by `PR`. Composite prose such as #131/#169 is represented as separate canonical IDs even when its historical narrative remains combined.

At Task 2 completion the closed-world canonical ID set is exactly `#22`, `F2`, `#36`, `#37`, `#48`, `#72`, `#77`, `#89`, `#90`, `#109`, `#131`, `#169`, `#255`, `#859`, `F5`, `F6`, `F7`, `F8`, `MCP-SURFACE`, `CPU-BACKEND`, `REF-CALL-REGISTRY`, `F10`, `DD-004`, `DD-006`, `AST-DSL-PARITY`, `MCP-LEAN-DEFAULT`, `CONTINUOUS-REFRESH`, and `RUST-REPLACE-SYMLINK`. `F5`, `F6`, `F7`, `F8`, `MCP-SURFACE`, `CPU-BACKEND`, and `REF-CALL-REGISTRY` are `READY` and own Tasks 4–13 as mapped below; `#77` is the sole canonical row owning the `#77`/F9 alias pair, while `#48`, `#72`, `#77`, `#131`, and `#169` remain the exact five `CEO_GATED` rows. The last seven IDs plus `#255` are the complete demand-gated population from the design. `RUST-REPLACE-SYMLINK` owns the unresolved public Rust direct-leaf-symlink behavior and is not silently closed with `CPU-BACKEND`. Any later task that adds/removes a canonical ID must update this exact-set assertion in the same commit; an unowned extra or missing row fails closed.

Program ownership is exact: `MCP-SURFACE` → Task 4; `CPU-BACKEND` → Task 5; `F6` → Tasks 6–7; `F5` → Task 8; `REF-CALL-REGISTRY` → Task 9; `F7` → Tasks 10–11; `F8` → Tasks 12–13. Each row stays `READY` until its first implementation PR number exists, then becomes `IN_FLIGHT` with that implementation `PR #NNN`; a separate post-merge closure change records the merged SHA and moves it to checked `SHIPPED` while preserving the final implementation PR in the PR field. When one row spans multiple implementation PRs, the trigger carries the ordered implementation-PR list and the PR field names the final implementation PR; the closure PR appears only in the trigger/audit. The parser verifies both lists rather than silently losing earlier receipts.

Task 14 deliberately extends the exact set in its first RED commit with unique `CODE-GRAPH-PROJECTION` and `CHANGE-IMPACT` rows as `READY`, plus `GRAPH-TRACE`, `GRAPH-CROSS-REPO`, `GRAPH-WORKFLOW-RUNTIME`, `GRAPH-PDG`, and `GRAPH-MUTATION` as `DEMAND_GATED`, and updates this exact-set assertion plus ownership mapping in the same commit. Build ownership is `CODE-GRAPH-PROJECTION` → Tasks 14A/14C and `CHANGE-IMPACT` → Tasks 14B/14C. The Task 15 disposition steward owns the five research rows until their recorded triggers fire. No earlier task may predeclare them as shipped, and Task 14 may not begin until Tasks 10–13 have merged and their exact registries/contracts are reverified.

For every owning implementation row in Tasks 3–14, each task's declared file list is additive to the mandatory lifecycle files `docs/TASK_BOARD.md`, `docs/BACKLOG.md`, `docs/SESSION_HANDOFF.md`, and `tests/unit/test_backlog_tracker_truth.py`. Push the first independently failing RED commit, open a draft implementation PR, then immediately add the exact `READY` → `IN_FLIGHT` transition with that real `PR #NNN`, update the ordered PR trigger, rerun the tracker test, and require the PR CI to include that transition before further production work. Later PRs for the same row replace the PR field with the new final implementation PR and append—never overwrite—the ordered implementation-PR trigger. No task may leave its row `READY` once its draft PR exists. The separate post-merge closure PR remains mandatory.

Assert that:

- every canonical row has one status token, every `SHIPPED`/`IN_FLIGHT` row has exactly one literal `PR #NNN`, and no historical prose is accidentally parsed;
- F1/#22 is `RETIRED` and agrees with `docs/CONTRACTS.md` plus executable behavior: exit 0 complete, exit 1 complete no-match, exit 2 incomplete; an unhonored explicit GPU request remains an in-band `gpu_request_unhonoured` disclosure and does not independently force exit 2;
- F2 is `RETIRED` and agrees with `ledger_store.resolve_agent_id`'s documented legacy compatibility decision;
- #109/#36/#37 are `SHIPPED` with PR #605/#903/#908 and are absent from active/hardware sections;
- #89 is `READY` with raw/same-native translated treatment-control evidence for the reproduced WSL-to-Windows search path-domain defect and owns Tasks 2A/2C;
- #90 is `READY`, cites the shipped doctor-half PR #571 without calling the whole item shipped, records raw/same-native translated scan treatment-control evidence (`0` false-clear versus six matches), and owns Tasks 2B/2C;
- #859 is `READY` as an actionable class-level AST writer-ratchet task, and the August 1 audit contains an appended correction stating that its codemap-only test did not satisfy the class-level population contract;
- the exact CEO-owned IDs `#48`, `#72`, `#77`, `#131`, and `#169` each occur once as `CEO_GATED`, and the exact demand-gated IDs `#255`, `F10`, `DD-004`, `DD-006`, `AST-DSL-PARITY`, `MCP-LEAN-DEFAULT`, `CONTINUOUS-REFRESH`, and `RUST-REPLACE-SYMLINK` each occur once as `DEMAND_GATED`; none also appears in an active canonical status;
- `SESSION_HANDOFF` current version equals the canonical tracker handoff version and its current/next-work prose contains no obsolete v1.45/v1.9.1-era direction.

The test must parse the canonical heading, metadata, and rows rather than assert one raw full-file snapshot. Include a minimal valid synthetic document and individually named negative controls for missing/duplicate/malformed version metadata, missing/duplicate canonical sections, duplicate IDs, malformed/multiline rows, checkbox/status mismatch, missing/multiple/nonliteral PR values, unknown status, empty trigger, CEO/demand duplication, closed-world population drift, and historical-prose false positives. It must not call GitHub or claim that a static fixture proves a PR is still open.

TDD sequencing is semantic, not merely “the file is absent.” First add a valid canonical skeleton that preserves the reviewed base's stale statuses, so parser controls are green. Then add and run each exact invariant node independently—`test_exit_contract_retirement`, `test_legacy_agent_id_retirement`, `test_shipped_receipts`, the historical #90 semantic node (subsequently amended to `test_mixed_90_reproduction_is_ready` after the live defect reproduced), `test_859_is_ready_with_audit_correction`, `test_program_ownership_and_ready_statuses`, `test_ceo_and_demand_ownership`, and `test_handoff_version_and_current_prose`—and record its expected pre-reconciliation failure. A canonical-section absence must not be the common reason all semantic nodes fail. The later #89 reproduction receives its own independent `BLOCKED` → `READY` RED.

Run:

```powershell
uv run --no-sync pytest tests/unit/test_backlog_tracker_truth.py -q --timeout=15
```

Expected: parser controls green; each named semantic invariant fails independently for its stated stale fact.

**Step 2: record one-shot GitHub truth separately from CI**

```powershell
git fetch origin main
git rev-parse origin/main
gh pr list --state open --limit 100 --json number,title,isDraft,headRefOid,statusCheckRollup
gh issue list --state open --limit 100 --json number,title,labels,state
gh run list --branch main --workflow ci.yml --limit 3 --json databaseId,status,conclusion,headSha,updatedAt
gh release list --limit 3
```

Copy the raw JSON/text output with timestamp and commands into `docs/audits/2026-08-02-backlog-reconciliation.md` using `apply_patch`. This is a dated reconciliation receipt, not a timeless pytest oracle.

**Step 3: update tracker truth**

Fetch `origin/main` first and record its exact remote SHA separately because a semantic-release commit may not have its own `main` workflow run. Record source/PR/release receipts for every retirement. Preserve historical narrative but clearly mark it historical. Add a re-open trigger for every parked item. Remove stale PR #882 from the live board table after confirming GitHub state.

Reconcile the named decisions rather than only moving version tokens:

- retire F1/#22 across `BACKLOG`, the contradictory `CONTRACTS` GPU bullet, and the stale explanatory `main.py` comment without changing executable behavior;
- retire F2 against `ledger_store.resolve_agent_id` and its existing anonymous-claim tests;
- close #109/#36/#37 with PR #605/#903/#908 receipts;
- keep #89 `READY` with exact raw and translated same-native search receipts and Task 2A/2C ownership;
- keep #90 `READY` as a mixed outcome (PR #571 doctor fix plus reproduced scan path-domain/false-clear defect), with exact raw and translated same-native scan receipts and Task 2B/2C ownership;
- append the #859 audit correction and register the class-level ratchet as `READY` for Task 3;
- remove duplicate #72 ownership outside the CEO index, freeze separate CEO records for #48/#72/#77/#131/#169, and keep #255 solely demand-gated;
- refresh the substantive current-state and next-work sections of `SESSION_HANDOFF`; a release-number-only edit is insufficient.

For #89, run only a bounded WSL probe if WSL is available; never restart/shutdown WSL:

```powershell
wsl.exe -e sh -lc 'timeout 30 tg --version && timeout 60 tg search tensor_grep /mnt/c/dev/projects/tensor-grep/src --json >/tmp/tg-89.json; rc=$?; printf "%s\n" "$rc"; python3 - <<"PY"
import json
print(json.load(open("/tmp/tg-89.json"))["result_incomplete"])
PY'
```

Freeze the outcome table: unavailable or missing prerequisites → remain `BLOCKED` with the exact environment trigger; a bounded clean reproduction → `RETIRED` with the raw receipt and environment fingerprint; a reproduced failure → `BLOCKED` when the failing environment is still required. The actual reproduction was locally actionable, so #89/#90 are `READY`, progression to Task 3 is stopped, and Tasks 2A–2C below must pass this amended thinktank/TDD/implementation-PR/post-merge-closure lifecycle. “Unavailable” is never retirement evidence, and no outcome may invent a fix.

**Step 4: make the test pass**

```powershell
uv run --no-sync pytest tests/unit/test_backlog_tracker_truth.py -q --timeout=15
uv run --no-sync ruff check tests/unit/test_backlog_tracker_truth.py
uv run --no-sync ruff format --check --preview tests/unit/test_backlog_tracker_truth.py
```

**Step 5: commit as non-release documentation/test work**

```powershell
git add MEMORY.md docs/TASK_BOARD.md docs/BACKLOG.md docs/SESSION_HANDOFF.md docs/audits/2026-08-02-backlog-reconciliation.md docs/audits/2026-08-02-ceo-backlog-update.md tests/unit/test_backlog_tracker_truth.py
git commit -m "test: pin live backlog truth"
```

Task 2 landed as commits `56c938871a8a76bb2fb70b5b8edc6880a3b87b65` and `9bfccb889810f8ff8ef1e9a589072a8368c47e6a` on its isolated branch after independent specification and security reviews both returned `SHIP`. The focused tracker suite is 41/41 and the combined governance set is 127/127; Ruff, preview-format, and diff checks are clean. These branch-local SHAs are evidence, not a claim of merge or publication.

**Step 6: land tracker truth and the approved amendment before code**

After the amended plan reaches final exact-hash `SHIP`, commit the plan pair on this branch, push the exact HEAD SHA, and open one non-releasing documentation/test PR containing Task 2 plus the approved plan. Post both independent Task 2 verdicts and the three-seat plan verdict/hash receipt. Require PR CI, merge from an open main gate, fetch current `origin/main`, and rerun the 127-test governance set plus exact plan-hash/source checks on the merged SHA. Only then create Task 2A from that `origin/main`; never bundle reconciliation into a `fix:` implementation PR or branch Task 2A from a pre-reconciliation main.

## Task 2A: bridge typed WSL search paths for #89

**Files:**

- Modify: `scripts/install.ps1`
- Modify: `rust_core/Cargo.toml`
- Modify: `rust_core/Cargo.lock`
- Create: `rust_core/src/path_domain.rs`
- Modify: `rust_core/src/lib.rs`
- Modify: `rust_core/src/main.rs`
- Modify: `rust_core/src/native_search.rs`
- Modify: `rust_core/src/rg_passthrough.rs`
- Modify: `rust_core/src/python_sidecar.rs`
- Create: `rust_core/src/search_input_ledger.rs`
- Create: `rust_core/tests/test_search_input_ledger.rs`
- Modify: `rust_core/tests/test_public_native_cli_parity.rs`
- Create: `src/tensor_grep/cli/_win32_path_domain.py`
- Create: `src/tensor_grep/cli/search_input_ledger.py`
- Modify: `src/tensor_grep/cli/runtime_paths.py`
- Modify: `src/tensor_grep/cli/bootstrap.py`
- Modify: `src/tensor_grep/cli/main.py`
- Modify: `src/tensor_grep/cli/rg_root_ignore.py`
- Modify: `src/tensor_grep/backends/ripgrep_backend.py`
- Modify: `src/tensor_grep/core/result.py`
- Modify: `src/tensor_grep/cli/formatters/json_fmt.py`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/fixtures/task2a_windows_node_manifest.json`
- Create: `scripts/run_task2a_pytest_nodes.py`
- Create: `scripts/run_task2a_rust_node.py`
- Create: `scripts/verify_task2a_windows_nodes.py`
- Modify: `tests/unit/test_install_scripts.py`
- Modify: `tests/unit/test_runtime_paths.py`
- Modify: `tests/unit/test_cli_bootstrap.py`
- Modify: `tests/unit/test_bootstrap_fast_path_imports.py`
- Modify: `tests/unit/test_formatters.py`
- Modify: `tests/unit/test_ndjson_zero_match_still_discloses.py`
- Modify: `tests/unit/test_native_e2e_ci_coverage_contract.py`
- Create: `tests/unit/test_wsl_path_domain.py`
- Create: `tests/unit/test_win32_path_domain.py`
- Create or modify: `tests/e2e/test_native_wsl_path_domain.py`
- Modify: `tests/e2e/test_native_json_byte_fidelity.py`
- Create: `tests/fixtures/path_domain_v1.json`
- Create: `tests/fixtures/search_input_ledger_v1.json`
- Create: `tests/unit/test_search_input_ledger.py`
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/routing_policy.md`
- Mandatory lifecycle: `docs/TASK_BOARD.md`, `docs/BACKLOG.md`, `docs/SESSION_HANDOFF.md`, `tests/unit/test_backlog_tracker_truth.py`

**Exact translation-owner matrix:**

This is the complete Program 0A registry contract. Task 2A implements only the generated-shim row and the bootstrap/full/direct-native `search` rows; every non-search row below is a frozen shared-contract control for its later owning task and cannot expand Task 2A scope.

| Public route | Sole owner | Child rule |
|---|---|---|
| generated WSL shim | none | provenance/`WSLENV` only; original argv |
| Python bootstrap/full `search` | Python | translate complete typed argv, set consumed marker, native rejects remaining POSIX typed fields |
| direct native `search` | Rust | translate complete typed argv before search |
| Python `scan` or native `scan` delegation | Python scan sidecar | native forwards complete original argv with no partial translation |
| Python/Typer `run` | Python at a selected Windows child boundary; otherwise local | translate its positional root only before a Windows `sg`/native spawn; WSL-local execution keeps the WSL root |
| direct native `run` | Rust | translate root/batch/audit/key complete set before effects/delegation |
| MCP index/rewrite direct and meta tools | Python MCP | translate complete typed request after caller-string preflight, before target authorization/native spawn |
| Task 6 Python evidence | Python | `REPO`, manifest, capsule, cost/env-cost, explicit/env signing key, previous, out, edit-verification complete set; edit-verification `-` opaque; target-created default key stays target-domain |
| Tasks 7/8/12 Python front doors | Python | translate each task's complete typed set |
| Tasks 7/8/12 direct native front doors | Rust | translate complete typed set, sidecar receives consumed argv |
| Task 13 MCP workspace tool | Python MCP | translate anchor/roots complete set before target authorization |

The consumed marker is internal coordination, not trust: with it, any remaining absolute POSIX typed value is `invalid_provenance`. Every row shares the same 256-distinct/10-second per-public-request budget; child delegation cannot reset it.

**Step 1: pin current behavior and write independent REDs**

Pin existing non-WSL argv byte-for-byte before changing routing. Freeze the exact IDs `root`, `file-short-separate`, `file-long-separate`, `file-short-attached`, `file-long-equals`, `file-bundle-attached`, `file-bundle-separate`, `ignore-long-separate`, and `ignore-long-equals`; no other `--ignore-file` attached form exists, and `-f=PATH` is an attached filename beginning `=`. Add exact public-route tests for bootstrap `search`, full Typer search spawning native, and direct native `search`. Bootstrap's four nonbundled pattern-file forms, full Typer's eight option forms plus pre-existence root ordering, and direct native's eight option forms first receive independently runnable registration/ownership REDs and behaviorless greens. Those tests may not monkeypatch the native gate true or count Rust→Python sidecar forwarding as Rust ownership. Only then add one semantic RED per route/ID, observing the raw POSIX operand at the injected Windows boundary, translated child operand, unchanged slot/order, no opaque rewrite, and duplicate-path one-call caching. Add composition arms for multiple roots, repeated files/ignores, and root+file+ignore. Add opaque controls for patterns, regexp, glob/iglob, type/filter, replacement, path separator, and post-`--` values; add relative, `.`, `..`, `-`, drive, and UNC controls. Execute every node independently and preserve its failure text.

The existing pattern/ignore delegation gates are pinned correctness quarantines. Before any gate opens, independently RED/green actual Rust pattern-file loading, multi-pattern matching/result parity, explicit user ignore-file forwarding, generated root-ignore behavior, missing/unreadable pattern/ignore files, duplicates, and no-Python-sidecar ownership. A parser-only green is insufficient. Freeze a committed real-rg-derived semantic table for empty file, LF/CRLF, blank line, trailing terminator, duplicates, missing/unreadable, invalid UTF-8/NUL, and mixed positional/`-e`/`-f` ordering. Enforce bounded streaming at 1 MiB per file, 32 files combined across pattern and ignore files, 4 MiB aggregate decoded bytes across that same combined population, 16 KiB per pattern or ignore rule, 65,536 total positional/`-e`/`-f` patterns, and 65,536 total rules across explicit and generated ignore inputs. Rule splitting itself is streaming and stops at cap+1 before matcher construction. Every selected matcher engine must charge construction and match-loop operations to `SearchInputLedger`; hard-refuse an engine that cannot expose those charges, including an uninstrumented backtracking/PCRE2 route, with `incomplete_reason_class="search_input_limit"` before compilation. Independently observe RED at cap−1/cap/cap+1 for each 1 MiB file, the combined 32-file and 4 MiB decoded-byte totals, per-pattern/rule length, the mixed positional/`-e`/`-f` pattern total, and explicit+generated ignore-rule totals; cross the aggregate boundaries with mixed source types so split counters cannot pass. Cap is accepted, while cap+1 or invalid content returns the full structured envelope with `incomplete_reason_class="search_input_limit"` and exit 2 before search, never a sampled partial-as-complete result. The exact sequence is caller-domain enumeration/validation → target selection → complete distinct translation → typed-slot rewrite plus consumed-marker validation → target-domain root checks, pattern/ignore reads, generated-ignore discovery, request resolution, and downstream spawn. Only an explicitly selected WSL-local route retains raw values and local-domain state.

The installer RED parses and executes the generated WSL shim with a fake target binary and proves it currently lacks caller-domain transport. Executable controls cover unset and exactly empty `WSLENV` as zero entries; unrelated unflagged and `/u|w|p|l` entries; reserved entries unflagged and with every flag; duplicates/case variants; similarly prefixed unrelated names; malformed nonempty values including leading/trailing/interior empty tokens; 256/257 entries; incoming and final rebuilt UTF-8 at cap−1/cap/cap+1; and distro UTF-8 at 255/256/257 plus empty/control. They pin unrelated byte/order preservation, reserved-entry removal, exactly one canonical `/w` entry per reserved name, explicit exports, original argv bytes, and every invalid arm's exact exit 2 plus sole sanitized stderr line/no exec. Because process environments cannot contain NUL, only the shared pure byte-parser corpus exercises embedded-NUL rejection; no shell or executable-launcher test claims to construct that impossible environment.

Before Step 2 changes the bridge, write every bridge/security behavior test and run each independently RED: poisoned `SystemRoot`; executable reparse/substitution/signature/identity changes; held-handle replacement and dual swap; valid embedded and real-System32 catalog-signed treatment; generic trusted-but-non-Microsoft embedded/catalog signer; untrusted catalog; catalog member-hash mismatch; cold-cache network canary; unavailable cache-only chain; ambiguous/null application name; command-line round-trip; inherited-handle leak; actual-child-image mismatch; input, unique-path, child, aggregate, stdout, and stderr cap boundaries; duplicate caching; nonzero child; invalid UTF-8; empty/multiline/relative/drive-relative/root-relative/ADS/device/NT/oversize/control-character output; descendant-held pipes; Job termination; bounded reap; and exact evidence/reason/error mappings. Fail independently at every boundary after successful `CreateProcessW`: Job assignment, thread resume, child-image query, and pipe-worker initialization. Before process/thread handles return, lexical/trust/setup failures assert zero bridge/downstream child and zero protected target-data read/write. Once handles return, every later failure permits the one failing bridge child, requires direct termination/reap or its assigned Job and descendants killed/reaped, and asserts zero downstream search/rg/native/sidecar child plus zero protected target-data read/write; reason mapping is independent of child existence and metadata/signature reads are excluded from target-data counts. Use existing monkeypatch/process boundaries where they can express the behavior. If a required adapter or symbol is absent, first record one import/registration RED, add only a behaviorless shell, then run the independent semantic REDs before implementing that behavior. Configurable-automount fixtures independently prove a managed-copy shim locates its sibling binary and translates operands without `/mnt`; a clean foreign sibling receives no shim. A separate fresh-managed-install → replace only `tg.exe` with a canary foreign executable → managed-upgrade fixture proves the `InstallerShimReceiptV1`-owned old shim is atomically tombstoned, its directory is removed from tensor-grep-managed PATH routing, direct-old-shim and managed-primary PATH routes cannot run the canary, foreign executable and genuinely foreign shim remain untouched, and the managed-primary shim works. Bash byte-cap tests run under non-ASCII locales and still count UTF-8 bytes.

Pin output separately: tensor-grep JSON match/no-match carries one object; cross-domain tensor-grep NDJSON match/no-match carries provenance only in one terminal metadata row; same-domain NDJSON, successful/no-match text, and raw `--format rg --json` bytes remain unchanged; translation failure JSON/NDJSON/text is complete, sanitized, exit 2, and has no downstream child. There is no invented separate raw-NDJSON grammar. Before production, commit `tests/fixtures/task2a_windows_node_manifest.json` version 1 with the exact fully qualified node ID, owning workflow/job names, required runner class, command digest, selected Rust test target/binary where applicable, and required non-skip disposition for every installer, Python contract, Rust unit, and compiled-native node named in this task; the committed manifest contains no live run or attempt identifier because `NativeCiReceiptV1` owns current-run binding. Each Python manifest subset runs through `scripts/run_task2a_pytest_nodes.py`, which invokes one dedicated pytest `--junitxml` command and emits strict `NativeCiReceiptV1` binding that JUnit node census/digest plus the exact argv/output/exit and current-run fields. Stable Rust does not promise libtest JUnit: `scripts/run_task2a_rust_node.py` uses stable Cargo build-message output to locate the manifest-selected test binary, invokes its stable `--list --format terse` surface and requires the fully qualified node exactly once, then invokes only that node with `--exact --include-ignored`, requires success, and writes strict `NativeCiReceiptV1` with every current-run identity and node/manifest/binary/list/argv/output/exit digest required by the design. `scripts/verify_task2a_windows_nodes.py` compares both Python and Rust `NativeCiReceiptV1` records to the closed-world manifest and cross-checks each Python record against its bound JUnit artifact; deletion, rename, absent/duplicate node, skip, duplicate ownership, unexpected dedicated node, wrong target/binary, wrong run/job, wrong runner, wrong command digest, or wrong-job execution fails. The ordinary complete stable Rust suite still runs separately and must pass, but its unrelated population is not compared to the Task 2A manifest. CI uploads every raw dedicated receipt and the final manifest verdict. The governance test pins all three exact artifacts and workflow wiring; filename-glob or dynamic collection alone is insufficient.

Round-60 REDs close the remaining decidability gaps before Step 2. First, a behaviorless strict `InstallerShimReceiptV1` parser independently fails on corrupt/duplicate/unknown keys, unknown generated-shim bytes, foreign asset digest, wrong tag/managed-directory/installer-state identity, forged/duplicate/cross-tag receipt, receipt/shim/binary reparse or alias, and Event-gated post-hash leaf/parent swaps. Receipt discovery starts only from the fixed protected ProgramData installer-state root and its retained identity; planted receipts in PATH or the managed binary directory, an outside-state valid receipt, a caller-selected state path, a missing/changed CNG signature, a public-key-thumbprint mismatch, and an install-command-digest-only receipt all fail with zero authority. A PATH seam parses a bounded string into exact ordered tokens and removes only a token whose no-follow opened directory volume/file identity equals the retained receipt-owned managed directory; case variants, 8.3 names, `\\?\\` spellings, trailing separators, and junction aliases cannot bypass or broaden identity. Production uses only `CreateTransaction`, a transacted registry open/write, and `CommitTransaction`; an unavailable TxR primitive or a racing non-transacted writer rolls back/fails closed with no lock/read-compare-write fallback. Inject failure/crash at every `InstallerTxnV1` stage, registry transaction boundary, and leaf replacement; recovery must converge idempotently to the complete old or complete new state, never overwrite an unrelated PATH edit, and never expose an executable unsafe old-shim→foreign-binary route. Every arm pins foreign files byte-identical.

Second, `SearchInputLedger` REDs independently pin 64 MiB compiled live memory, 10,000,000 matcher transitions, and a fixed 300-second deadline anchored at public-request entry before generated discovery. Run cap−1/cap/cap+1 for every numeric file/byte/rule/memory/operation dimension, including each 1 MiB file, combined pattern+ignore 32-file/4 MiB budgets, mixed positional/`-e`/`-f` patterns, and explicit+generated ignore rules; inject split-counter and inclusive-boundary mutations plus an already-expired pre-open clock and expiry inside discovery/read/normalize/compile/match. Cap is admitted; cap+1 or expiry emits the route's full structured envelope with `incomplete_reason_class="search_input_limit"` and exit 2 before the next allocation/transition or downstream child. Separate REDs select each supported matcher engine and prove it charges both construction and inner-loop work. Bootstrap, full CLI, direct native, native-to-rg, and native-to-sidecar doors each select an uninstrumentable backtracking/PCRE2 route and prove refusal before compiler, native, rg, sidecar, or matcher child creation with that same literal reason.

Third, retain a no-reparse System32 directory handle and add Event-gated parent/junction swap REDs between system-directory resolution, directory open, relative `wsl.exe` open, and post-create identity comparison. Embedded and catalog tests pin `WTD_UI_NONE`, `WTD_REVOKE_WHOLECHAIN`, `WTD_CACHE_ONLY_URL_RETRIEVAL`, and `WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT`; clearing cache-only trips the network canary. Require `CERT_CHAIN_POLICY_MICROSOFT_ROOT` with test-root acceptance disabled plus a maintained production Microsoft-root SHA-256 thumbprint allowlist; a user-trusted foreign same-Organization chain is RED. Job tests pin `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, absence of both breakaway limit flags, absence of `CREATE_BREAKAWAY_FROM_JOB`, and a real descendant breakaway attempt that cannot survive Job close. Fourth, extend the committed manifest/JUnit/Rust-runner contract with strict `NativeCiReceiptV1` anti-replay REDs: seeded/nonempty current-run directory, duplicate binding, cross-attempt copy, missing/wildcard commit/run/job binding, one-byte manifest drift, binary pre/post drift, skipped/extra node, list/argv/output/exit digest drift, source-tree-as-wheel attribution, and receipt identity fields that disagree with independently derived live Actions repository/commit/run/attempt/job/runner/artifact-context values each fail; one current-run full-census Python-plus-Rust receipt set is the positive control.

The final node verifier must not accept the current-run tuple merely because a receipt repeats it. It independently derives repository, commit SHA, workflow run ID, attempt, job, runner identity, and artifact namespace from the live Actions environment and current artifact-download context, cross-checks Python receipts against their JUnit population/digest and Rust receipts against their stable `--list` census, and rejects every environment/receipt disagreement.

**Step 2: implement bounded provenance and translation**

Step-2 order is mandatory. First make the four Round-60 RED groups in Step 1 independently fail: protected cryptographic installer receipt plus transacted-registry/PATH identity, the ledger on every existing matcher/delegation route with combined-counter boundaries, retained System32/Microsoft-root/Job non-breakaway identity, and independently re-derived current-run CI receipt anti-replay. Then implement the Round-60 guard paragraph immediately below. Existing matcher routes receive ledger charging first; new `-f`/`--ignore-file` charge sites are added only with their separately red gate-opening work. Only after those guards are green may the older launcher/bridge/pattern-routing behavior be exposed. No intermediate commit may ship earlier receipt shorthand, PATH-discovered authority, path-string-only identity, emulated registry CAS, an unnamed/split matcher budget, breakaway-capable jobs, Organization-only signer trust, or self-attested CI evidence.

Implement Round-60 production only after the exact four Round-60 RED groups named above are independently failing. The installer opens the fixed protected ProgramData state root, verifies its restrictive security descriptor and retained identity, verifies the canonical `InstallerShimReceiptV1` signature with the bound non-exportable CNG key/public-key thumbprint, boundedly decodes it with duplicate/unknown-key refusal, retains no-follow receipt/directory/shim/binary handles, and stages new leaf bytes invisibly. A bounded fsynced `InstallerTxnV1` journal records retained identities, exact PATH preimage/intended image, staged/old-leaf identities, and monotonic phases. PATH changes use only a TxR registry transaction and commit after identity-based token selection; unsupported TxR, recovery ambiguity, or a competing non-transacted write fails closed with no emulated fallback. Injected post-PATH failure conditionally restores the exact preimage and retained old leaves, while startup recovery idempotently completes old or new without overwriting unrelated concurrent PATH edits. The bridge opens `wsl.exe` relative to the retained System32 directory identity, enforces the exact offline WinTrust/Microsoft-root policy and production-root thumbprints, and uses a kill-on-close non-breakaway Job. `SearchInputLedger` is no-refund across Python/Rust, explicit/generated, and child delegation; every public/delegation door installs it before route selection, charges before discovery/open/retain/allocate/transition, and checks the same 300-second absolute deadline before/after each bounded phase and inside matching. `NativeCiReceiptV1` is produced only in a freshly created empty per-run directory; the verifier independently derives the live Actions/artifact tuple before it accepts any bound field or census digest.

Implement Program 0A's bounded reserved-entry normalization. The shim overwrites and exports both values, preserves unrelated `WSLENV` entries byte-for-byte/in-order, appends exactly two canonical `/w` entries, and executes original `"$@"`. It does not run `eval`, `sh -c`, `cmd /c`, PowerShell interpolation, or any argv-rewrite loop. It resolves a sibling only when the installer recorded and hash-verified the managed copy in that same directory. A clean directory containing a preserved foreign `tg.exe` receives no shim. On upgrade, authorize tensor-grep ownership only from the retained protected installer-state identity after CNG signature/public-key binding plus bounded complete `InstallerShimReceiptV1` validation; a digest-only, path-only, PATH-discovered, caller-supplied, outside-state, unsigned, or partial receipt is never authority. Stage the owned fail-closed tombstone, primary shim, and receipt under retained handles, then let the `InstallerTxnV1` journal coordinate a TxR registry commit for removal of only the opened-identity-matched receipt-owned PATH token with handle-relative atomic leaf replacements; unsupported/failed TxR has no CAS/read-compare-write fallback and returns with no visible mutation. Later injected failures recover to a complete old or new transaction state without overwriting unrelated registry edits. Preserve foreign executables and genuinely foreign shims; the managed primary directory remains the supported WSL launcher. Native/Python accept both variables absent as legacy only; partial/invalid provenance is `invalid_provenance`/exit 2.

Implement `PathDomainContractV1`, `PathDomainEvidenceV1`, and the shared fixture corpus behind injectable Windows process/clock/handle/signature adapters. Add a narrowly featured target-Windows direct `windows-sys` dependency and lock update; keep all Rust unsafe calls and RAII HANDLE ownership in `path_domain.rs`. Lazily import Python `_win32_path_domain` and retain the fast-path import test. Resolve System32 with `GetSystemDirectoryW`, retain its no-reparse directory handle, and open the regular non-reparse `wsl.exe` leaf relative to that handle using read sharing only while denying write/delete sharing through identity capture, trust, spawn, and comparison; a final-path string is corroboration only. For embedded signatures, run offline `WinVerifyTrust` with `WTD_CHOICE_FILE`, the held `WINTRUST_FILE_INFO.hFile`, `WTD_UI_NONE`, `WTD_REVOKE_WHOLECHAIN`, and `WTD_CACHE_ONLY_URL_RETRIEVAL | WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT`. For catalog signatures, calculate/enumerate the catalog member hash from that same held handle and run the identical offline flags with `WTD_CHOICE_CATALOG` and `WINTRUST_CATALOG_INFO.hMemberFile`; require the member hash to remain equal. Both branches then require `CERT_CHAIN_POLICY_MICROSOFT_ROOT` with test roots disabled, a terminal-root SHA-256 in the maintained production Microsoft allowlist, leaf Code Signing EKU `1.3.6.1.5.5.7.3.3`, time validity, and normalized Organization only as corroboration. Create with direct `CreateProcessW`, exact non-null application name, round-tripped command line, `CREATE_SUSPENDED` without `CREATE_BREAKAWAY_FROM_JOB`, and inherited-handle allowlist; retain process/thread/source handles, assign before resume to a Job with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and neither breakaway flag, query the actual child image, and compare it to the same held source identity. The return of process/thread handles defines post-create cleanup: any later Job-assignment, resume, image-query, pipe-initialization, cap, timeout, decode, exit, or identity failure terminates and boundedly reaps the primary process; after Job assignment it also kills/reaps descendants. Pin the exact drive-absolute/ordinary-UNC grammar and device/NT/ADS/reserved-name rejections from the design. Logs/errors disclose only domains and exhaustive reason code, never raw usernames, distro names, paths, signer details, or child stderr.

Extend the grammar-derived field registry: `_search_path_args_raw` remains the positional-root classifier, while an index/span-based option-operand classifier owns `-f/--file` and `--ignore-file` in the frozen spelling set without rewriting arbitrary argv. Do not infer paths from slash-shaped tokens. Implement the committed golden-table Rust pattern/ignore semantics and numeric streaming caps before removing the gates. Every route follows exact enumeration/validation → target selection → complete translation → typed-slot rewrite/consumed validation → target-domain reads/resolution/spawn; WSL-local execution explicitly retains raw values. Python owns the complete rewritten argv it sends to native; direct native owns its complete request. A consumed child marker is valid only when no absolute POSIX typed field remains. Preserve result paths in Windows form. Tensor-grep aggregate JSON uses the exact nested evidence; cross-domain tensor-grep NDJSON emits it once in a terminal summary including zero-match; same-domain NDJSON, raw `--format rg --json`, and ordinary successful text stay byte-identical.

**Step 3: adversarial and mutation proof**

Rerun all Step 1 semantic nodes green, including exact manifest-versus-dedicated-receipt job proof, the spelling census, real pattern-file/multi-pattern/ignore ownership and numeric/golden semantics, executable distro UTF-8 at 255/256/257 bytes plus empty/control, pure-parser embedded-NUL rejection, input at 16 KiB boundaries, 256/257 unique paths, unset/exactly-empty/malformed/cap-boundary `WSLENV`, locale-independent byte counts, managed/clean-foreign/managed-then-foreign non-`/mnt` launcher arms, dependency/import governance, held-handle embedded/catalog/cache-only signer policy, command-line/handle/actual-child identity, every pre-/post-handle process/output/failure arm, terminal NDJSON provenance, and one sanitized stderr snapshot. Pre-handle failures prove zero bridge/downstream child; every post-handle bridge failure proves kill/reap and zero downstream child; all translation failures prove zero protected target-data read/write. For every spelling, retain both its registration test and later semantic/correctness test. As post-green sensitivity proof, temporarily remove each positional/pattern-file/ignore-file bootstrap/full/native semantic mapping, pattern/ignore correctness gate or bound, output mapping, embedded/catalog trust/share/owned-shim-retirement guard, receipt node/target/job mapping, or bridge cleanup boundary while leaving its public test unchanged; require the expected observable to fail, restore, and rerun green. Post-green mutations supplement but never replace the recorded Step 1 REDs.

Round-60 mutations must fail unchanged tests: count only explicit or only generated inputs; split pattern/ignore file/byte/rule totals; accept cap+1 or reject cap; change/remove 64 MiB, 10,000,000, or 300 seconds; omit memory or construction/inner-transition charging; install the ledger after one bootstrap/full/native/rg/sidecar decision; allow an uninstrumented matcher engine instead of refusing it before compilation; anchor the deadline after discovery; reset it at a route/child boundary; stop checking one later phase; authorize from PATH, adjacency, install-command digest, or an unsigned/outside-state receipt; change the protected CNG key binding; release/reopen one retained installer/System32 identity; replace TxR with a process lock/read-compare-write fallback; remove opened-directory identity from one PATH alias; clear one offline WinTrust/Microsoft-root/thumbprint flag; enable one Job/process breakaway flag; drop one journal phase/fsync, conditional PATH rollback, retained-old-leaf rollback, or crash recovery; accept seeded/duplicate/cross-attempt/self-attested CI evidence; skip live Actions/JUnit/Rust-census re-derivation; or omit one receipt digest/attribution check. Restore each mutation and rerun green.

Run focused Python tests locally under `timeout 120`/`--timeout=15`; put Rust and Windows matrices in CI. The exact native test IDs join the native CI coverage census and may not skip on Windows.

**Step 4: lifecycle, gate, merge, and dogfood**

Push the first genuine RED commit, open a draft PR, immediately move #89 to `IN_FLIGHT` with its real PR number and ordered implementation list, then build TDD-first. Require independent specification and adversarial security `SHIP`, focused real-venv tests, Ruff/preview-format/mypy, and non-skipped Windows/native CI whose current-run `NativeCiReceiptV1` evidence proves every static-manifest node under its expected workflow/job name, runner class, and command digest. Merge only after the newest main run is completed and any preceding release is served by PyPI. Dogfood from the exact tagged public `install.ps1`/release asset in a clean WSL location: record URL/SHA-256, generated shim digest, selected binary path/version/hash, managed, clean-foreign, and managed-shim→foreign-executable upgrade arms, mixed-flag and cap-boundary `WSLENV`, distro cap boundaries, real System32 catalog-signed treatment, match exit 0, no-match exit 1, `-f/--file` and `--ignore-file` in every spelling family, protected-data no-read failure, and translation failure exit 2 with zero downstream child; pre-handle failures have zero bridge child; every post-handle bridge failure has its bounded bridge child terminated and descendants killed/reaped. Also prove managed upgrade regenerates the fixed primary shim, retires the owned unsafe old shim/PATH route, and preserves genuinely foreign files. Exclude source-tree/global shims. #89 remains `IN_FLIGHT` until Task 2C and the separate closure PR finish.

Round-60 lifecycle proof is additive to Step 4: verify downloaded installer/asset SHA-256 against the exact tag's live `CHECKSUMS`; open/digest the generated shim and selected binary under retained verified parents and Event-gate digest-through-exec swaps; prove managed upgrade changes only cryptographically receipt-owned artifacts and opened-identity-matched PATH tokens and refuses forged/stale/aliased/outside-state authority without changing foreign state. Upload raw `NativeCiReceiptV1`, live Actions/artifact tuple, JUnit, Rust census inputs, and final verdict; reject any receipt not independently bound to the current run/attempt/job/commit/runner/manifest/binary/list/argv/output/exit. Source-tree receipts can never satisfy published-wheel or installer proof.

## Task 2B: fix typed WSL scan paths and #90 false-clear behavior

**Files:**

- Modify: `src/tensor_grep/cli/main.py`
- Modify: `src/tensor_grep/cli/_index_lock.py`
- Modify: `src/tensor_grep/io/directory_scanner.py`
- Modify: `rust_core/src/main.rs`
- Create: `tests/unit/test_scan_wsl_path_domain.py`
- Modify: `tests/unit/test_directory_scanner.py`
- Modify: `tests/unit/test_directory_scanner_hardening.py`
- Modify: `tests/unit/test_scan_unreadable_disclosure.py`
- Create: `tests/unit/test_atomic_write_bytes_anchoring.py`
- Create or modify: `tests/e2e/test_native_wsl_path_domain.py`
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/routing_policy.md`
- Mandatory lifecycle: `docs/TASK_BOARD.md`, `docs/BACKLOG.md`, `docs/SESSION_HANDOFF.md`, `tests/unit/test_backlog_tracker_truth.py`

**Step 1: pin the false clear with same-executable controls**

Create an AST rule and target equivalent to the Task 2 receipt. Through the same injected Windows-native executable, raw WSL path treatment must reproduce exit 0, `matched_rules=0`, and an unreadable-path diagnostic; translating only the typed path must find the pinned nonzero count. Keep executable, rule, config, cwd, environment, and every non-path argv value identical. Add a direct `DirectoryScanner.walk(missing_root)` RED proving the requested missing root currently disappears as an empty iterator without incomplete state.

Add one independent RED for each typed scan field: positional roots; `--config`; `--rule`; ruleset `--path`; `--baseline`; `--write-baseline`; `--suppressions`; and `--write-suppressions`. Add negative controls for ruleset name, inline YAML, filter, language, justification, glob, type, and evidence text that happen to begin with `/` or contain backslashes. Add config-only WSL-provenance coverage proving the Rust shortcut currently bypasses typed translation.

Characterize `--write-baseline` and `--write-suppressions` independently on base for new-leaf and existing-regular-file behavior before changing either writer. Then record one import/registration RED for `_index_lock.atomic_write_bytes_anchored`, add only a behaviorless callable shell, and write/run independent semantic REDs before implementing it: directory/live/dangling/reparse leaf refusal; target parent junction/symlink; Event-gated parent/intermediate and late-leaf swaps; translated escape; canonical alias; unsupported primitive; and failure before publication. For every read/write field, inject translation failure and assert zero config/rule/baseline/suppression opens, zero AST child calls, zero destination/temp/parent creation, exit 2, and byte-identical intended/external trees. Pin exact 0/1/2 JSON-before-exit behavior and the durable six-match treatment/control here, before Step 2 production changes.

**Step 2: translate typed fields and fail closed**

Use Task 2A's contract/vectors; do not create a second policy. Native scan forwards the complete original scan argv and provenance to the Python sidecar, which is the sole owner and translates every typed field before config/rule/baseline/suppression read, candidate collection, AST child spawn, or output creation. Native must not partially translate config-only or other scan forms. Each successful tensor-grep JSON result preserves the pre-pinned complete scan key set and adds exactly `path_domain=PathDomainEvidenceV1`; failure preserves the separately pinned complete error key set and adds `path_domain`, `result_incomplete=true`, `incomplete_reason_class="path_domain_mismatch"`, with exit 2. Text leads with the exact sanitized incomplete line and prints no clean totals; SARIF preserves its full schema with `executionSuccessful=false` and the same nested object. Raw data, distro, username, path, and child stderr never appear.

Change `DirectoryScanner.walk` so an explicitly requested missing root records exact cause `missing_explicit_root` instead of silently returning. Propagate it through candidate collection and every scan caller. Pin complete JSON, text, and SARIF outputs: JSON keeps the normal full scan key set with empty findings/counts plus `partial=true`, `partial_reason="missing_explicit_root"`, `result_incomplete=true`, and exit 2; text prints the sanitized incomplete line before any count and exits 2; SARIF sets `executionSuccessful=false`. Existing partial-scan compatibility remains unchanged for unrelated mid-walk unreadable children; the new exit-2 rule is limited to translation failure or a missing explicit requested root, both of which mean zero of requested scope was scanned.

Before Task 2B can publish, add exact shared `_index_lock.atomic_write_bytes_anchored(path: Path, data: bytes, *, mode: int | None = None, replace: bool) -> None` and `tests/unit/test_atomic_write_bytes_anchoring.py`. Characterize `--write-baseline` and `--write-suppressions` independently on base for new leaf and existing regular file, then preserve each observed overwrite/no-clobber behavior by passing the corresponding `replace` value. The helper opens/identity-verifies the parent without delete sharing, refuses directory/live/dangling/reparse leaves, creates temp and publishes handle-relatively with no path fallback, rejects canonical aliases, and holds/revalidates the parent through publication. Unsupported primitives, translated escape, race substitution, or leaf/intermediate swap exits 2 with intended/external trees byte-identical and no residue. Task 3 changes its file scope from creating to extending these already-shipped helper/tests; it may not move, weaken, or duplicate them.

**Step 3: prove no read/write on failure**

Rerun every Step 1 field, false-clear, missing-root, no-effect, alias, timeout, invalid-output, leaf, and Event-gated swap semantic node green. As post-green sensitivity proof, temporarily remove every production field mapping or anchored-publication guard one at a time while keeping tests byte-identical; require its dedicated public test to fail on the expected argv/evidence/no-effect observable, restore, and rerun green. Post-green mutations supplement but never replace the recorded Step 1 REDs.

**Step 4: lifecycle, gate, merge, and dogfood**

Open a draft PR from a real RED, move #90 to `IN_FLIGHT` with the PR and ordered implementation list, and require independent specification plus adversarial-security `SHIP`. After focused real-venv tests, Ruff/preview-format/mypy, Windows/native CI, and posted verdict, merge through the release gate. Dogfood the published WSL launcher with the exact import rule: raw caller input must be translated internally, return the pinned matches, and disclose WSL→Windows provenance; injected translation failure must exit 2 and create no baseline/suppression output. #90 remains `IN_FLIGHT` until Task 2C and the separate closure PR finish.

## Task 2C: close WSL path-domain twins in run, indexed search, and MCP rewrite routes

**Files:**

- Modify: `rust_core/src/main.rs`
- Modify: `src/tensor_grep/cli/main.py`
- Modify: `src/tensor_grep/cli/mcp_server.py`
- Modify: `tests/unit/test_medlow2_run_json.py`
- Modify: `tests/unit/test_mcp_server.py`
- Modify: `tests/integration/test_mcp_stdio_protocol.py`
- Modify: `tests/unit/test_mcp_contract_version_docs_are_pinned.py`
- Modify: `tests/unit/test_mcp_contract_fixes.py`
- Modify: `tests/unit/test_harness_api_docs.py`
- Create: `tests/unit/test_run_rewrite_wsl_path_domain.py`
- Create or modify: `tests/e2e/test_native_wsl_path_domain.py`
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/harness_api.md`
- Modify: `docs/routing_policy.md`
- Mandatory lifecycle: `docs/TASK_BOARD.md`, `docs/BACKLOG.md`, `docs/SESSION_HANDOFF.md`, `tests/unit/test_backlog_tracker_truth.py`

**Step 1: freeze the complete mutation-route field census and REDs**

Pin Python/Typer `tg run` and direct-native run separately. Python/Typer exposes and owns only its positional root, keeping that root raw for local WSL execution and translating it immediately before any selected Windows `sg`/native child. Direct native owns its root plus `--batch-rewrite`, `--audit-manifest`, and `--audit-signing-key`; those three fields are tested only through the real direct-native surface, never as unreachable Typer options. Freeze the exact MCP census: direct `tg_index_search`; `tg_query(action="index")` with `path` and every `workspace_roots[]` element; direct `tg_rewrite_plan`, `tg_rewrite_apply`, and `tg_rewrite_diff`; and `tg_rewrite(action="plan|apply|diff")`. Typed rewrite fields are `path`, apply-only `policy`, `audit_manifest`, and `audit_signing_key`. Add independent semantic REDs through real Python/native/function/stdio surfaces for every reachable route/field, including workspace-root aggregation. Add negative controls for pattern, replacement, language, globs, filters, expected digests/counts, lint/test command strings, inline data, agent identity, and justification. No RED may be a missing module/registry/tool or unknown option.

Before Step 2 changes authorization or effects, add independent REDs for index, plan, diff, apply, direct-native batch rewrite, and audit output with injected failures before/during/after translation, before/after target authorization, and during safe read/publication. Assert byte-identical repositories, indexes, policies, keys, and pre-existing audit files; no new temp/parent; zero downstream operation child; and no partial rewrite. pre-handle failures have zero bridge child; post-handle failures require bounded bridge-child termination and descendant kill/reap. Add leaf/intermediate symlink/junction swaps, translated escape, canonical root/key/policy/audit aliasing, descendant timeout, and lock/publication races. Canary values must be absent from stdout/stderr/logs/exceptions/receipts. Existing public functions provide the semantic seam; if one new guard adapter is absent, record one registration/import RED, add only a behaviorless shell, and then observe its semantic RED before implementing the guard.

**Step 2: translate after policy, before effects**

Reuse the versioned contract/fixture corpus. Python/Typer run translates only its positional root at a selected Windows child boundary; Python MCP owns the complete typed argv before native spawn; direct native run owns its root/batch/audit/key set. A consumed marker with any remaining POSIX typed field is invalid. MCP performs only grammar/provenance/size preflight on caller-domain strings, then translates and enforces target-domain canonical no-follow `_mcp_root` confinement plus the complete index/rewrite policy on every canonical identity before effects. Do not apply Windows confinement to raw `/...` strings. Reject canonical read/write and multi-field alias collisions. Event-gate leaf and intermediate junction swaps between translation, authorization, and use.

`tg_index_search`/index-action is mutation-capable: translation and target authorization finish before `.tg_index` state/parent/temp creation, and publication uses the existing per-root lock/atomic contract. For run/rewrite, bounded policy/signing-key reads use regular no-follow, final-path/identity-verified opened handles; the audit destination is published handle-relatively and cannot alias the key, policy, root, or another destination. Retain/revalidate handles through signing/publication where required. Translation/authorization/read failure produces no index, child, rewrite, audit, temp, or parent creation.

All tensor-grep run responses preserve a pinned full normal/error key-set snapshot and add only the exact nested path-domain object/incomplete fields. Every MCP route returns its existing complete envelope plus `path_domain`; failures add exact `error={"code":"path_domain_mismatch","reason":PathDomainReason}`, `result_incomplete=true`, and the standard MCP fields, never an uncaught tool error or empty string. Stdout/stderr and exceptions are scanned for path, distro, username, policy/key canaries, and child stderr. This published MCP wire change bumps `_TG_MCP_SERVER_CONTRACT_VERSION` from `1.7.0` to `1.8.0` with history comment, exact docs/unit/stdio pins, and real stdio calls for each direct/meta route.

**Step 3: mandatory adversarial no-mutation gate**

Rerun all Step 1 translation/authorization/safe-read/publication/no-effect/race semantic nodes green. Keep tests byte-identical; as post-green sensitivity proof, remove one production typed mapping or bypass one production effect guard at a time, require the unchanged public test to fail on its expected observable, restore, and rerun green. Post-green mutations supplement but never replace the recorded Step 1 REDs. This independent gate returns only `SHIP` or `FIX-FIRST(file:line + repro + minimal fix)` and is posted to the PR.

**Step 4: lifecycle and joint closure**

The Task 2C implementation PR is appended to both #89 and #90 ordered lists and becomes each row's final implementation PR; both stay `IN_FLIGHT`. Its fake-bridge/process/target-handle/index/rewrite tests run non-skipped in the Windows native-build census, and the CI receipt asserts every node ID executed; live WSL is not the CI oracle. After independent spec/security review, release-safe merge, publication, and exact tagged-installer WSL dogfood for run, direct/meta index, and direct/meta rewrite plan/diff/apply in a disposable repository, create one separate non-releasing closure PR from current `origin/main`. Dogfood records contract `1.8.0`, selected binary hash/version, index/repository/audit before/after, producer exits, and sanitized structured artifacts. It proves failure is byte-identical/no-effect. The closure records all implementation PRs/merged SHAs/published versions, marks #89/#90 `SHIPPED`, passes CI, merges under the same gate, and is reverified on its exact merged SHA. No implementation PR certifies its own merge.

## Task 3: restore #859's class-level atomic-writer ratchet

**Files:**

- Create: `tests/unit/test_cli_atomic_writer_ratchet.py`
- Create unmodified historical fixture: `tests/fixtures/audits/codemap_pre_859.py`
- Modify: `src/tensor_grep/cli/main.py`
- Modify: `src/tensor_grep/cli/_index_lock.py`
- Test changed writers in: `tests/unit/test_mcp_server.py`
- Test changed CLI/scaffold writers in: `tests/unit/test_cli_modes.py` and the existing focused command test files discovered by the census
- Modify: `tests/unit/test_atomic_write_bytes_anchoring.py` created and shipped by Task 2B
- Modify: `docs/TASK_BOARD.md`
- Modify: `docs/BACKLOG.md`
- Modify: `tests/unit/test_backlog_tracker_truth.py`
- Modify: `docs/audits/2026-08-01-backlog-verification-receipts.md`

**Step 1: build the AST detector and historical positive controls**

The test parses Python under `src/tensor_grep/cli/`, resolves module and function-local imports plus assignment aliases with lexical-scope-aware rebinding/shadowing, and discovers every generated-Python execution root from production subprocess/spawn callsites rather than from a fixed helper-name list. Every statically resolvable payload is parsed as a separate synthetic source unit with a stable identity that includes its outer module, outer function or `<module>`, resolved callsite fingerprint, and generated `<module>`/function identity; any dynamic or unparseable payload fails closed. Destination provenance is part of helper-backed classification: direct or aliased `.resolve()`, `os.path.realpath`, or equivalent canonicalization of the caller-selected leaf before an approved writer is a violation because it erases symlink identity. It then classifies functions/source units that:

- write directly to a caller-selected destination through `open(..., write-mode)`, `Path.open(..., write-mode)`, `Path.write_text`, or `Path.write_bytes`;
- create/write a temporary file and then publish it through `os.replace`, `os.rename`, `Path.replace`, `Path.rename`, `shutil.move`, `shutil.copy`, `shutil.copyfile`, or `shutil.copy2`;
- call an approved shared atomic helper;
- publish through `replace_with_retry`, including imported/renamed aliases;
- perform a separately sanctioned runtime/directory swap.

Create `tests/fixtures/audits/codemap_pre_859.py` as the byte-exact `codemap.py` blob from commit `0c46863cd038efa438fe6af2fc533109af257dc7`, SHA-256 `dd16398dc3278efd66d46ab63170cd71cf4e3c9512234f340ef292dff5f2fe76`; keep provenance constants in the test rather than modifying the fixture with a header. Require historical `_atomic_write_text` to classify as violating while current `codemap.py` classifies helper-backed. Add individually red controls for renamed `os.replace`, renamed `shutil.move`, renamed `shutil.copy`/`copy2`, an imported/assignment-aliased `replace_with_retry`, local imports, shadowing/rebinding, direct writers bound under another name, variable write modes, `io.open`, `os.open` flag propagation, `Path.open`, `Path.write_text`, `Path.write_bytes`, tempfile-to-publish flows, and direct plus assignment/import-aliased leaf pre-resolution before an approved helper. Safe negative controls must create their temporary directory/file inside the analyzed function; a caller-supplied “temp” path requires an explicit sanction because confinement is not statically decidable. Build an independently derived lexical/raw-call candidate inventory from production spawn and write callsites that also surfaces `shutil.copyfileobj`, `urllib.request.urlretrieve`, archive extraction, `os.write`, and generated-source sinks; every candidate must resolve to `sanctioned`, `helper-backed`, or `violating`, and every unresolved call fails instead of disappearing from the population. Sanctions are exact fingerprints of `module:outer-function:resolved-callsite:operation:destination-provenance`, never whole-function exemptions. Mutation controls add a third generated `python -c` helper, an unsafe sink inside an otherwise sanctioned outer function, and direct/aliased pre-resolution before an approved helper; each must increase the discovered population and violation count.

Pin the complete current population by stable source/function/fingerprint/classification identity, not by line number.

```powershell
uv run --no-sync pytest tests/unit/test_cli_atomic_writer_ratchet.py -q --timeout=15
```

First add a behaviorless detector shell so collection/import is green. Then make each positive/negative control red independently; the first required red is the renamed-`os.replace` assertion returning no sink, not an absent module. Implement resolution one sink family at a time. Historical-fixture controls remain green permanently by expecting the known violation. Pin the complete current population while explicitly expecting the three live violations below; that inventory test is green. Then add `test_no_violation_write_json_refuse_symlink`, `test_no_violation_write_ast_project_scaffold`, and `test_no_violation_new`, and run each exact node separately before fixing its corresponding symbol; never combine them under global `-x` and infer that all three were observed red. Mutation tests inject both an ordinary unsafe writer and a third generated helper into copies of the final current tree and prove the population and violation count each increase by exactly one. The final census is green with zero unresolved/violating candidates.

**Step 2: inspect every reported production site**

Expected direct `os.replace` sites initially include:

- `src/tensor_grep/cli/main.py`
- `src/tensor_grep/cli/lsp_provider_setup.py`
- `src/tensor_grep/cli/_index_lock.py`
- `src/tensor_grep/cli/session_daemon.py::_write_daemon_metadata` via `replace_with_retry`

Classify artifact writers separately from launcher/native-runtime/directory swaps. Do not force runtime swaps through `atomic_write_bytes`.

The initial exact violating symbols are:

- `main:_write_json_refuse_symlink` (`main.py:6222-6264` on the reviewed base), including production callers that currently call `Path.resolve()` before the helper and erase original leaf-symlink identity;
- `main:_write_ast_project_scaffold` (`main.py:14961-14990`), whose three caller-selected YAML artifacts use direct `write_text`;
- `main:new` (`main.py:14995-15074`), whose caller-selected YAML artifact follows a dangling destination symlink.

**Step 3: fix real bypasses, if any**

First characterize each symbol's current publication semantics. Ruleset/artifact refresh outputs retain create-or-overwrite behavior. `new`'s destination and the project scaffold's `sgconfig.yml` retain create-if-absent behavior even when a competitor creates the leaf after the initial existence check; route them through a shared atomic no-clobber variant rather than an overwrite-capable helper. The no-clobber result is a visible refusal and leaves the competing bytes untouched.

Before changing any caller routing, extend Task 2B's shipped anchoring suite and the affected public CLI/MCP tests with separately named semantic nodes for every changed writer: ordinary create/overwrite, create-if-absent/no-clobber, missing parent, live symlink, dangling symlink, existing directory, failure-before-publication/no-temp-leak, and production call order. Add Event-gated swaps before every directory-creation boundary plus late-leaf-symlink and parent-directory-swap/junction races on Unix and Windows. Run each affected writer node independently RED against its current direct/pre-resolved route; the expected failure must be its public replacement, refusal, ordering, or external-tree observable—not the static detector. Every live/dangling/reparse leaf-link node, including overwrite routes, asserts refusal rather than directory-entry replacement. The complete external tree must remain byte-identical and no external same-name artifact/directory may be created. Task 2B already provides the callable helper/test seam, so no new registration shell is needed here.

Only after those semantic RED receipts exist, route the user-facing byte/text/JSON artifact publishers through shared anchored writers in `src/tensor_grep/cli/_index_lock.py`, one writer at a time, and rerun its dedicated nodes green before continuing. Confinement/expansion occurs before publication without resolving away the destination leaf. Directory creation, temporary creation, and publication are anchored to an opened, no-follow, identity-verified parent/ancestor handle for their entire lifetime: POSIX walks/creates missing components relative to `O_DIRECTORY|O_NOFOLLOW` directory fds (`mkdirat`-style) and publishes relative to the final fd (using a same-directory link/rename no-replace primitive for no-clobber); Windows opens the ancestor with `FILE_FLAG_OPEN_REPARSE_POINT`, creates missing directories/temporary children relative to verified handles, and publishes with handle-relative `FileRenameInfoEx`/equivalent semantics, with replace disabled for no-clobber. If a platform implementation cannot create a missing component relative to the handle, it fails closed rather than performing a path-based `mkdir`. A path-based recheck followed by path-based mkdir/rename is not sufficient on either platform. The shared Task 2B helper contract is immutable here: every live, dangling, or reparse leaf link is refused for both replace and no-clobber modes.

Tracker lifecycle is exact: Task 2 lands #859 as `READY`; Task 3 keeps it `READY` until the implementation PR number exists, then changes it to `IN_FLIGHT` with that exact `PR #NNN`. After that PR merges, create a separate non-releasing closure PR/commit from current `origin/main` that reruns the merged treatment arm, records the implementation PR/merge SHA in the August 1 audit, updates `test_backlog_tracker_truth.py`, and changes #859 to checked `SHIPPED`. A code PR cannot certify its own future merge.

**Step 4: verify the class contract**

```powershell
uv run --no-sync pytest tests/unit/test_cli_atomic_writer_ratchet.py tests/unit/test_codemap_write_refuses_symlink.py tests/unit/test_evidence_bundle_atomic_write.py -q --timeout=15
uv run --no-sync pytest tests/unit/test_atomic_write_bytes_anchoring.py -q --timeout=15
uv run --no-sync ruff check src/tensor_grep/cli/_index_lock.py src/tensor_grep/cli/main.py tests/unit/test_cli_atomic_writer_ratchet.py tests/unit/test_atomic_write_bytes_anchoring.py
uv run --no-sync ruff format --check --preview src/tensor_grep/cli/_index_lock.py src/tensor_grep/cli/main.py tests/unit/test_cli_atomic_writer_ratchet.py tests/unit/test_atomic_write_bytes_anchoring.py
uv run --no-sync mypy src/tensor_grep/cli/_index_lock.py src/tensor_grep/cli/main.py
```

**Step 5: mandatory independent security gate**

The reviewer must attempt a live/dangling symlink bypass, existing-directory destination, leaf precheck-to-replace race, parent-directory swap/junction race, and create-if-absent clobber race against every changed writer semantic. Verdict must be `SHIP` before merge.

## Task 4: disclose the MCP tool surface and bump contract 1.8.0 → 1.9.0

**Files:**

- Modify: `src/tensor_grep/cli/mcp_server.py`
- Modify: `tests/unit/test_mcp_server.py`
- Modify: `tests/unit/test_mcp_contract_version_docs_are_pinned.py`
- Modify: `tests/unit/test_mcp_contract_fixes.py`
- Modify: `tests/integration/test_mcp_stdio_protocol.py`
- Modify: `tests/unit/test_harness_api_docs.py`
- Modify: `docs/harness_api.md`
- Modify: `docs/CONTRACTS.md`

**Step 1: add red unit and subprocess assertions**

In the normal capability test assert `payload["tool_surface"] == "full"`. Extend `_MCP_FLAG_PROBE_SCRIPT` to emit the capability payload's `tool_surface`, then assert:

- default/on values: `full`, 58 tool names;
- recognized off values: `lean`, 12 tool names;
- capability registry exactly equals live tool registry in both states.

Update stdio integration to assert the field through a real `tg_mcp_capabilities` call.

```powershell
uv run --no-sync pytest tests/unit/test_mcp_server.py -q -k "capabilities or legacy_tools" --timeout=15
```

Expected: fail because the field is absent.

**Step 2: implement from the import-time source of truth**

Capture one immutable `_LEGACY_TOOLS_ENABLED = _legacy_tools_enabled()` boolean during module import before registration begins. Use that frozen value for `_register_legacy_tool`, `_build_mcp_tool_capabilities`, and `"full" if _LEGACY_TOOLS_ENABLED else "lean"` in `_mcp_capabilities_payload`. Do not reread the environment at capability-call time, infer the field from tool count, or change the default flag.

Add a same-process test that imports under the default/full state, mutates `TG_MCP_LEGACY_TOOLS=0`, and proves both the live registry and `tool_surface` remain frozen at full. The existing subprocess tests remain the oracle for choosing the other import-time state.

Starting from Task 2C's `1.8.0`, set `_TG_MCP_SERVER_CONTRACT_VERSION = "1.9.0"` with a history comment. Update exact version pins and docs.

**Step 3: verify both flag arms and stdio**

```powershell
uv run --no-sync pytest tests/unit/test_mcp_server.py tests/unit/test_mcp_contract_version_docs_are_pinned.py tests/unit/test_mcp_contract_fixes.py tests/unit/test_harness_api_docs.py -q -k "capabilities or contract or legacy_tools or harness" --timeout=15
uv run --no-sync pytest tests/integration/test_mcp_stdio_protocol.py -q --timeout=15
uv run --no-sync ruff check src/tensor_grep/cli/mcp_server.py tests/unit/test_mcp_server.py tests/integration/test_mcp_stdio_protocol.py
uv run --no-sync ruff format --check --preview src/tensor_grep/cli/mcp_server.py tests/unit/test_mcp_server.py tests/integration/test_mcp_stdio_protocol.py
uv run --no-sync mypy src/tensor_grep/cli/mcp_server.py
```

**Step 4: mandatory adversarial MCP gate**

Probe default/on/off/nonsense values in fresh subprocesses, inspect real `tools/list`, and confirm old tool calls still work. Publish the verdict as a PR comment before merge.

## Task 5: retain/harden public Rust `CpuBackend.replace_in_place` and fix the Python adapter twin

**Files:**

- Inspect/possibly modify: `rust_core/src/backend_cpu.rs`
- Inspect/possibly modify: `rust_core/tests/test_replace.rs`
- Modify: `src/tensor_grep/backends/cpu_backend.py`
- Modify: `tests/unit/test_cpu_backend.py`
- Modify: `docs/BACKLOG.md`
- Create: `docs/investigations/2026-08-02-replace-in-place-surface.md`

**Step 1: prove the search instrument**

Run exact identifier, public export, FFI, string/dynamic registry, documentation, and test searches. Include a known called sibling method as a positive control. Do not run cold Cargo locally.

```powershell
rg -n -w "replace_in_place" rust_core src tests docs
rg -n "replace_in_place|PyO3|pymethods|pub use|extern.*C|match.*replace" rust_core/src src
```

**Step 2: write red Rust public-API error tests**

The public `backend_cpu` module, public `CpuBackend`, public method, and crate `rlib` mean an in-repo zero-caller result cannot authorize deletion. Preserve the exact public `anyhow::Result<()>` signature and streaming traversal. Add an external compile-time assertion equivalent to `const _: fn(&CpuBackend, &str, &str, &str, bool, bool) -> anyhow::Result<()> = CpuBackend::replace_in_place;` so a return-type or argument-shape change fails even when `.unwrap()` callers would still compile. First characterize public success/direct-file-failure/directory behavior green. Then refactor without behavior change so the public method unconditionally delegates to the same private injectable core and rerun the characterization green; a disconnected test-only seam is forbidden. Put narrow private injectable seams plus their fault tests inside `rust_core/src/backend_cpu.rs`; external integration tests cannot access a private seam. Add and run independently red directory-mode arms for walk failure, literal child replacement/write failure, and regex child replacement/write failure, each proving its seam fired through that delegated core and requiring `Err(...)` with stable operation/path context. Do not claim the direct-file arm is red—its errors already propagate. Retain external successful zero-match, direct-file failure, and successful replacement controls in `rust_core/tests/test_replace.rs`. Do not rely on OS permission bits, collect the directory before processing, or silently change nonexistent-path/direct-leaf-symlink behavior; those two compatibility/security decisions remain documented follow-ups.

**Step 3: write and fix the Python A27 twin REDs**

In `tests/unit/test_cpu_backend.py`, inject a fake native module whose `search` records argv and raises an internal `TypeError`. Add `test_simple_fixed_inverted_internal_typeerror_fails_closed` for the inline adapter and `test_word_regexp_inverted_internal_typeerror_fails_closed` for `_rust_match_set`; run each exact node independently on base and prove two calls occurred with the second call missing `invert_match`. Add and run `test_cpu_backend_has_one_native_adapter_and_zero_typeerror_retries` independently on base before production changes; it must expose the current `(2 native adapters, 2 TypeError compatibility retries)` population against the required `(1, 0)`, then rerun green after implementation. The fixed contract is one native call with `invert_match=True`, then `BackendExecutionError` preserving the failure—never a retry with dropped semantics and never a fixed-string Python fallback.

Replace the inline adapter at the reviewed base's `cpu_backend.py:427-444` with `self._rust_match_set(...)`, then remove `_rust_match_set`'s reviewed-base `except TypeError` retry at `:830-849`, leaving one exact-signature call. Map a native-call `TypeError` to `BackendExecutionError` inside the helper, and re-raise `BackendExecutionError` before the simple path's generic fixed-string fallback so the native fault cannot be masked. Preserve genuine native-absence behavior only through its explicit `ImportError`/`ModuleNotFoundError` arm. Retain `CPUBackend`, `RustCoreBackend`, and the PyO3 class; do not route through `RustCoreBackend` because that would create a circular dependency and alter fallback/ReDoS contracts.

**Step 4: implement Rust typed propagation**

Propagate errors through the existing `anyhow::Result<()>` with stable contextual messages at the public method boundary. Do not introduce a new public error type, remove, or rename the method. Any future removal requires an explicit breaking-API decision, a deprecation release, downstream migration guidance, and a major-version compatibility plan.

Do not change the current direct-leaf-symlink behavior in this task. Record its current follow-target behavior, public compatibility surface, threat boundary, owner, and reopen trigger under canonical `RUST-REPLACE-SYMLINK=DEMAND_GATED`; closing `CPU-BACKEND` must leave that separate row visible rather than imply it shipped.

**Step 5: verify and use CI for Rust verification**

Run each exact Python behavioral node and `test_cpu_backend_has_one_native_adapter_and_zero_typeerror_retries` separately in both RED and green arms, then the focused `test_cpu_backend.py` group locally under the timeout protocol. Preserve the individual red/green receipts. Run `cargo fmt --check` locally only if Rust changes. Push the branch and use GitHub Actions for Cargo tests/checks under the shared-machine rule. Require an independent backend/security review to attempt fixed/non-fixed native-internal `TypeError` masking, dropped inversion, directory-walk failure, literal/regex child failure, and accidental public-API removal before merge.

## Task 6: create the versioned pure edit-verification service

**Files:**

- Create: `src/tensor_grep/cli/prepare_service.py`
- Create: `src/tensor_grep/cli/edit_verification.py`
- Create: `tests/unit/test_edit_verification.py`
- Modify: `src/tensor_grep/cli/evidence_receipt.py`
- Modify: `src/tensor_grep/cli/evidence_signing.py`
- Modify: `src/tensor_grep/cli/main.py` for additive `tg evidence emit --edit-verification FILE|-`
- Modify: `tests/unit/test_evidence_receipt.py`
- Modify: `tests/unit/test_evidence_signing.py`
- Create: `tests/integration/test_evidence_command.py`
- Modify: `tests/integration/test_prepare_oneshot_cuj.py`
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/harness_api.md`
- Modify: `src/tensor_grep/cli/runtime_paths.py`
- Modify: `tests/unit/test_wsl_path_domain.py`
- Modify: `tests/fixtures/path_domain_v1.json`
- Modify: `tests/e2e/test_native_wsl_path_domain.py`
- Rerun unchanged: `rust_core/src/path_domain.rs` conformance tests

**WSL path-domain extension:** register the complete Python-owned evidence set: positional `REPO`; `--manifest`; `--capsule`; `--cost-json` and transported `TG_EVIDENCE_COST_JSON`; explicit/transported `--signing-key` and `TG_EVIDENCE_SIGNING_KEY` (the target-created default key stays target-domain); `--previous`; `--out`; and `--edit-verification FILE`. Only edit-verification `-` is an opaque stdin sentinel; query/model/agent/checkpoint values remain opaque. Translate the complete set before any read/sign/write and reject canonical aliases. Successful WSL receipts include the optional signed/digested top-level path-domain component; non-WSL/legacy bytes remain unchanged. Bridge failure emits the design's full evidence error envelope with exact object/reason/incomplete fields, exit 2, and no receipt/read/sign/write. Add shared-vector semantic REDs, unchanged production mutations, signed/keyless cases, non-skipped Windows native coverage, and published dogfood.

**Step 0: extract the prepare service before edit verification depends on it**

Pin the current `tg prepare` payload, capsule, help, exit, and stdout bytes green on base. Extract the existing private payload/capsule composition pair and blast-radius builder from `main.py` into `prepare_service.py` without changing those bytes or importing `main.py` back into the service. Run `tests/integration/test_prepare_oneshot_cuj.py` and the focused prepare/capsule tests green before creating or importing `edit_verification.py`. Tasks 6–8 consume this service; Task 8 must modify, not create, it. This prerequisite extraction is refactoring-only and cannot claim an edit-verification RED.

**Step 1: write schema and bounded-reader red tests**

Test:

- exact `EditBaselineV1`, `PrimaryTargetV1`, `PathStateV1`, `ValidationDescriptorV1`, `TrustDisclosureV1`, `PrepareSnapshotV1`, `EvidenceEditVerificationComponentV1`, `EditVerificationResultV1`, `PathDeltaV1`, and `EditReadyTicketV1` key sets, literals, types/nullability, cross-field invariants, complete reason vocabulary/precedence, and malformed-input mappings from the design;
- exact prepare/capsule projection fixtures for complete, confirmation-tie, validation-resolved tie, deadline-partial, scan-truncated, unrelated-partial, and mixed scan+deadline+unrelated-source inputs;
- canonical JSON digest stability;
- exact `receipt_digest`/`canonical_receipt_bytes` preimage reuse, top-level digest exclusion, and one-field mutation invalidation for `EditVerificationResultV1`;
- schema v1 round trip;
- unknown major version rejection;
- the single canonical baseline writer/reader cap permits generated encoded output of 5 MiB - 1 and exactly 5 MiB, refuses 5 MiB + 1 before JSON parse/persistence, and self-reads every accepted output;
- final redirected `verify-edit` stdout (including one newline) is accepted at 5 MiB - 1/exactly 5 MiB; an otherwise 5 MiB + 1 complete result becomes the exact full-schema `INCOMPLETE/result_byte_limit` envelope with no sampled changed/blast paths and is ingestible by evidence;
- the shared file-backed JSON reader rejects Unix FIFO/device, leaf symlink, Windows leaf reparse, parent junction/escape, and Event-gated identity swaps before semantic use; mandatory Windows arms cannot skip;
- duplicate keys at top level and every nested baseline/primary-receipt/previous-receipt location map to `duplicate_json_key` before schema/canonical verification;
- missing/unknown required policy fields fail closed;
- repository identity mismatch;
- real SHA-1 and `git init --object-format=sha256` repositories round-trip format-consistent 40/64-hex commits and index object IDs without truncation or schema rejection;
- commit drift and dirty-tree drift;
- unchanged pre-existing out-of-scope dirt is not attributed to the edit;
- changing the contents of an already-dirty path is detected even when its porcelain status remains ` M`;
- toggling only a tracked dirty file's executable bit is detected through normalized `worktree_mode` even when content/status/stage identity are unchanged;
- any `assume-unchanged` or `skip-worktree` index entry returns `INCOMPLETE/index_flag_unsafe` and writes no strict baseline;
- an `MM` path whose staged blob changes while its worktree bytes are restored is detected through changed stage-0 mode/object identity;
- unmerged stage 1/2/3 entries fail closed;
- mutation of a file nested under a newly untracked directory is observed because status collection uses `--untracked-files=all`;
- staged/unstaged status changes, renames, deletions, regular-file bytes, untracked-file bytes, and symlink target changes are distinguished; v1 deliberately makes no copy-classification promise;
- path-count fixtures pin 9,999/10,000/10,001; total-hashed-byte fixtures pin 64 MiB - 1/64 MiB/64 MiB + 1; per-file fixtures pin 8 MiB - 1/8 MiB/8 MiB + 1; every over-cap case fails closed without sampling;
- every strict output outside canonical `.tensor-grep/edit-baselines/` is refused;
- the owned baseline output is consistently excluded from baseline and verification state while a sibling `.tensor-grep` path is not;
- path escape and symlinked baseline refusal;
- changed file inside/outside editable scope;
- blast-radius widening;
- widening wholly contained within declared review-only paths yields ordered reason `blast_radius_widened_within_review_scope`, verdict `WARN`, complete-result fields, and exit 1; widening outside that set yields `BLOCK`;
- deterministic `PASS`, `WARN`, `BLOCK`, `INCOMPLETE` reason ordering.

```powershell
uv run --no-sync pytest tests/unit/test_edit_verification.py -q --timeout=15
```

Expected first RED: only the public constructor/comparator imports fail. Add typed, behaviorless shells immediately. Then run each bullet above as its own red-green slice and record the targeted assertion failure (wrong mode, missed `MM`, wrong reason, cap acceptance, and so on); no later behavior slice may claim a red receipt from an import/registration failure.

**Step 2: implement immutable data and pure functions**

Implement the exact immutable types and pure builders/comparators from the design. Use real temporary Git repositories—not mocked Git text—for SHA-1/SHA-256 object formats, executable-mode, `MM`, stage 1/2/3, assume-unchanged, skip-worktree, nested-untracked, rename, deletion, and symlink fixtures; separately unit-test the bounded subprocess parser/fault adapters. Build `preexisting_changes` from exactly `git status --porcelain=v1 -z --untracked-files=all`, stage-0 `{mode, object_id}` records from NUL-delimited `git ls-files --stage -z`, and index flags from bounded `git ls-files -v -z`; output-cap/parser ambiguity returns `INCOMPLETE`. The existing aggregate `dirty_tree_sha256` remains a summary, not the edit-delta oracle. On Unix, hash through an `O_NOFOLLOW` opened descriptor verified by `fstat`. On Windows, open with `CreateFileW` plus `FILE_FLAG_OPEN_REPARSE_POINT`, reject a leaf reparse point, obtain final path plus volume serial/file ID from the same handle, require final-path confinement, and hash only through that handle. Both adapters accept regular files only, stream at most `cap + 1`, and compare identity/metadata before and after; if the guarantee is unavailable, return exact `INCOMPLETE/platform_no_follow_unavailable` before reading, while an escaped final path returns `opened_path_escape`. Cover swaps with deterministic Event handshakes and FIFO handling on Unix. Windows CI must execute (not skip) leaf-reparse, parent-junction, and swap fixtures and assert their test IDs in the job receipt.

Every result includes `coverage="git-visible"`, `authorization=false`, `ignored_paths_unobserved=true`, and `identity_trust="self-asserted"`. Explicitly declared editable ignored paths are hashed under the same limits; arbitrary ignored-file writes are outside coverage and can never be represented as authorized.

Keep Git subprocess calls in narrow injectable adapters with fixed argv, cwd, timeout, and output cap.

**Step 3: connect receipt generation**

Extend EvidenceReceipt additively with an optional `edit_verification` component carrying baseline digest, policy digest, verifier version, verdict, reasons, and the four mandatory trust fields `coverage="git-visible"`, `authorization=false`, `ignored_paths_unobserved=true`, and `identity_trust="self-asserted"`. These fields live inside the canonical signed/digested component. Decode the baseline, primary receipt, and nested previous receipt with one bounded duplicate-rejecting decoder at every nesting depth before canonical/schema verification. Red tests create freshly and correctly signed/trusted receipts—not post-signature tampering—with each trust field omitted or contradicted, plus keyless equivalents; every case must fail semantic verification. Positive signed/keyless controls and a legacy receipt with no optional component remain valid byte-for-byte.

First pin existing no-option `tg evidence emit` stdout/receipt bytes green on base. Then add a registration/help RED for `--edit-verification`, register a behaviorless option shell returning only sentinel `edit_verification_not_implemented`, and make help/routing plus legacy no-option behavior green. Only after that add each behavior node by itself and require its component/payload/exit-specific RED; no node may cite the unknown-option or sentinel response. Read repo-confined `FILE` only through the shared opened-handle safe JSON reader at cap−1/cap/cap+1 around 5 MiB. Freeze `FILE="-"` as the production handoff: read stdin exactly once to EOF or `cap+1`, apply the identical exact-byte cap and duplicate-rejecting decoder, reject coexistence with another stdin consumer, and never persist the result in the repository. Require exact `EditVerificationResultV1` with non-null baseline/policy digests, nonempty result-producing `verifier_version`, valid result `receipt_sha256`, and internally consistent verdict/reasons/trust. The receipt component copies that version verbatim and binds the exact result digest as `verification_result_sha256`; it never substitutes the running emitter version. Extend the receipt builder so it captures canonical root/repository identity/object format/commit/dirty digest once using the verifier's exact revision helper/exclusions, compares the result inside the builder, then places and signs that same immutable capture in the outer receipt—no adapter precheck plus later reread and no caller override. It may coexist with existing capsule/manifest inputs when none consumes stdin. Cross-repo, post-result clean/dirty/revision drift, old-verifier/current-emitter relabel attempts, one-field result mutation/digest disconnect, an Event-gated mutation before builder capture, outside-root, leaf/parent link/reparse, FIFO/special, swap, malformed, duplicate-key, or inconsistent input exits 2 and writes no receipt. A mutation after capture cannot alter the signed captured subject. Run every arm in both keyless and trusted-signed modes by node ID, plus coexistence and legacy controls. Task 6 uses directly constructed and service-produced canonical result bytes for evidence-ingestion tests; it does not invoke the not-yet-registered public `verify-edit` command. Public producer→consumer subprocess round trips and `tests/e2e/test_native_evidence_edit_verification.py` belong to Task 7 after command registration, so their REDs are behavioral rather than command-discovery failures.

**Step 4: verify**

```powershell
uv run --no-sync pytest tests/unit/test_edit_verification.py tests/unit/test_evidence_receipt.py tests/unit/test_evidence_signing.py tests/unit/test_review_bundle_evidence_receipts.py tests/integration/test_evidence_command.py -q --timeout=15
uv run --no-sync ruff check src/tensor_grep/cli/edit_verification.py src/tensor_grep/cli/evidence_receipt.py src/tensor_grep/cli/evidence_signing.py src/tensor_grep/cli/main.py tests/unit/test_edit_verification.py tests/unit/test_evidence_signing.py tests/integration/test_evidence_command.py
uv run --no-sync ruff format --check --preview src/tensor_grep/cli/edit_verification.py src/tensor_grep/cli/evidence_receipt.py src/tensor_grep/cli/evidence_signing.py src/tensor_grep/cli/main.py tests/unit/test_edit_verification.py tests/unit/test_evidence_signing.py tests/integration/test_evidence_command.py
uv run --no-sync mypy src/tensor_grep/cli/edit_verification.py src/tensor_grep/cli/evidence_receipt.py src/tensor_grep/cli/evidence_signing.py src/tensor_grep/cli/main.py
```

## Task 7: expose `tg verify-edit` through all front doors

**Files:**

- Modify: `src/tensor_grep/cli/commands.py`
- Modify: `src/tensor_grep/cli/main.py`
- Modify: `rust_core/src/main.rs`
- Modify: `tests/e2e/test_routing_parity.py`
- Modify: `tests/unit/test_cli_bootstrap.py`
- Create: `tests/integration/test_verify_edit_command.py`
- Create: `tests/e2e/test_native_verify_edit.py`
- Create: `tests/e2e/test_native_evidence_edit_verification.py`
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/harness_api.md`
- Modify: `src/tensor_grep/cli/runtime_paths.py`
- Modify: `rust_core/src/path_domain.rs`
- Modify: `tests/unit/test_wsl_path_domain.py`
- Modify: `tests/fixtures/path_domain_v1.json`
- Modify: `tests/e2e/test_native_wsl_path_domain.py`

**WSL path-domain extension:** register positional `REPO` and `--validation-file FILE` as typed paths through Python and native; `--baseline NAME` remains an opaque owned basename. `EditVerificationResultV1` always carries `path_domain` null/translated/error. Bridge failure emits the full result key set with verdict `INCOMPLETE`, reasons `['path_domain_mismatch']`, unavailable fields null/empty, exact subreason evidence, exit 2, and no baseline/validation read. Freeze Python-owner versus direct-native-owner forwarding, shared vectors, opaque controls, unchanged production mutations, final-wire caps, non-skipped native CI nodes, and published WSL dogfood.

**Step 1: pin registration failure first**

Freeze exact Python/native argv `tg verify-edit REPO --baseline NAME --baseline-sha256 DIGEST --validation-file FILE [--deadline SECONDS] --json`. `NAME` is only the owned-state basename from the design; `DIGEST` is exact lowercase-64-hex external trust state; and the loader accepts only `EditBaselineV1`—never a capsule. Both baseline and validation inputs must flow through the shared safe bounded JSON reader, and exact opened bytes are hashed/compared before JSON use. Add `verify-edit` to the expected public set test before production registrations and capture that registration RED alone. Then add all four registrations with a behaviorless adapter that emits the exact result shape using sentinel reason `missing_required_field`; make routing tests green before behavior mapping. Immediately create `test_native_verify_edit.py` and `test_native_evidence_edit_verification.py` against that behaviorless compiled route—do not wait for Step 3. Add Python/native help snapshots, argv-parity, missing/63/65/nonhex/uppercase/well-formed-mismatch digest nodes, one-byte and schema-valid scope-expanding baseline mutations, validation-file cap/link/reparse/FIFO/swap, invalid basename, in-owned-dir baseline leaf/swap, outside-owned-state escape, duplicate-key, wrong-schema capsule, same-file target-symbol deletion, unchanged blast set, widening inside declared review-only WARN, widening outside BLOCK, exact descriptor drift, deadline partial, redirected final-wire cap−1/cap/cap+1 (newline included) with `result_byte_limit`, PASS exit 0, and JSON-before-exit tests. Every real INCOMPLETE node expects its own non-sentinel reason. For each PASS/WARN/BLOCK/digest-valid-INCOMPLETE behavior, add its Python node, compiled-native node, and signed/keyless `verify-edit` captured-stdout → `evidence emit --edit-verification -` subprocess node before implementing that producer mapping. The first execution after registration may fail on the sentinel; record it as wiring RED only, never as sufficient behavioral proof. Implement the state mapping and run all four surfaces green. Then use `apply_patch` to introduce one temporary per-state mutation that flips that state's verdict/exit/result digest and, for the compiled route, one forwarding mutation that drops or changes a required argv value; run the exact Python/native/signed/keyless nodes and require state-specific assertion failures. Restore each mutation with `apply_patch` and rerun green before the next state. These mutation receipts—not the sentinel—are the independent behavioral RED proof. Malformed/null/invalid-digest consumer-2 controls are already behaviorally available from Task 6 and run green beside every slice. Run each new test by exact node ID because the repository's global `-x` can otherwise hide later red arms.

```powershell
uv run --no-sync pytest tests/unit/test_cli_bootstrap.py tests/e2e/test_routing_parity.py tests/integration/test_verify_edit_command.py -q --timeout=15
```

Expected sequence: registration node fails; registration becomes green with the shell; native suites are created immediately; each state gets wiring RED on the sentinel, mapping green, then temporary verdict/exit/digest plus native-forwarding mutations that make its Python, compiled-native, and paired signed/keyless nodes fail specifically, followed by restored green. No behavior, native, or roundtrip contract may cite the missing-command/sentinel response as its only RED.

**Step 2: complete the thin Typer adapter after every mapping has a targeted RED**

The Typer command implements exactly `tg verify-edit REPO --baseline NAME --baseline-sha256 DIGEST --validation-file FILE [--deadline SECONDS] --json`, validates only adapter syntax/path confinement/digest grammar, calls `edit_verification`, prints the complete capped JSON payload, and maps verdict to exit code. It does not contain comparison or capsule-conversion policy.

**Step 3: verify Python and real native front door**

Rerun the real subprocess round trips created during the behavior slices as one complete matrix: `verify-edit` captures producer stdout bytes and exit directly, then supplies those bytes to `evidence emit --edit-verification -` without a shell pipeline and asserts evidence subject/result digest plus consumer exit in signed and keyless modes. Required arms are PASS 0→0; valid WARN and BLOCK 1→0 with producer=1 retained and the receipt preserving the verdict; digest-valid `result_byte_limit` INCOMPLETE 2→0 with producer=2 retained and the receipt preserving INCOMPLETE; and malformed/null/invalid-digest consumer=2 with no receipt. Direct shell piping is unsupported unless both statuses are preserved. No test may materialize a result file or allow a consumer zero to mask producer 1/2. `tests/e2e/test_native_evidence_edit_verification.py` pins compiled help/argv, stdin handoff and all four status classes, malformed, duplicate, version preservation, result-digest binding, cross-repo, revision/dirty drift, redirected cap−1/cap/cap+1, coexistence, legacy no-option, keyless, and signed cases with `TG_REQUIRE_RG_PARITY=1`; missing native binary or a skipped node is a CI failure.

```powershell
uv run --no-sync pytest tests/unit/test_cli_bootstrap.py tests/e2e/test_routing_parity.py tests/integration/test_verify_edit_command.py -q --timeout=15
uv run --no-sync ruff check src/tensor_grep/cli/commands.py src/tensor_grep/cli/main.py src/tensor_grep/cli/edit_verification.py tests/integration/test_verify_edit_command.py
uv run --no-sync ruff format --check --preview src/tensor_grep/cli/commands.py src/tensor_grep/cli/main.py src/tensor_grep/cli/edit_verification.py tests/integration/test_verify_edit_command.py
uv run --no-sync mypy src/tensor_grep/cli/edit_verification.py src/tensor_grep/cli/main.py
cargo fmt --manifest-path rust_core/Cargo.toml --check
```

Rerun the already-created `tests/e2e/test_native_verify_edit.py` and require its recorded per-state CI mutation-RED receipts; the existing `native-build-smoke` `test_native_*.py` census executes the compiled binary with `TG_REQUIRE_RG_PARITY=1`. Missing compiled binary or a skipped node is a failure. The native file itself—not only routing parity—pins exact help, argv, deadline/input bounds, malformed/schema/path cases, baseline and validation safe-reader arms, target/blast/descriptor drift, PASS/WARN/BLOCK/each INCOMPLETE reason, stdout JSON, and real exit codes; it includes a control proving the Python service was reached through Rust dispatch. Then dogfood the built executable with all four verdicts.

## Task 8: implement strict `tg edit-ready` without changing legacy prepare/ledger

**Files:**

- Modify: `src/tensor_grep/cli/prepare_service.py`
- Modify: `src/tensor_grep/cli/main.py`
- Modify: `src/tensor_grep/cli/commands.py`
- Modify: `rust_core/src/main.rs`
- Modify: `tests/e2e/test_routing_parity.py`
- Create: `tests/unit/test_edit_ready.py`
- Create: `tests/integration/test_edit_ready_command.py`
- Create: `tests/e2e/test_native_edit_ready.py`
- Modify: `tests/integration/test_prepare_oneshot_cuj.py`
- Modify: `src/tensor_grep/cli/ledger_store.py`; do not change `resolve_agent_id(None)`
- Modify: `src/tensor_grep/cli/_index_lock.py` with a claims-only OS-fence helper; do not change the existing shared `IndexLock` contract for checkpoint/session/finding consumers
- Modify: `tests/unit/test_index_lock.py`
- Modify: `tests/unit/test_index_lock_concurrency.py`
- Modify: `tests/unit/test_ledger_concurrency.py`
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/harness_api.md`
- Modify: `src/tensor_grep/cli/runtime_paths.py`
- Modify: `rust_core/src/path_domain.rs`
- Modify: `tests/unit/test_wsl_path_domain.py`
- Modify: `tests/fixtures/path_domain_v1.json`
- Modify: `tests/e2e/test_native_wsl_path_domain.py`

**WSL path-domain extension:** register positional `REPO` and `--validation-file FILE` as typed paths. Query, `--agent-id`, `--out NAME`, and repo-relative `--editable`/`--review-only` values remain opaque; absolute WSL or Windows scope values stay invalid and have negative controls. `EditReadyTicketV1` always carries path-domain null/translated/error. Bridge failure emits the full ticket key set with status `incomplete`, reasons `['path_domain_mismatch']`, null claim/baseline/prepare/verification, exit 2, and no ledger/baseline/validation effect. Freeze one owner per Python/native route, shared vectors, unchanged-test production mutations, final-wire caps, non-skipped native CI, and published dogfood.

**Step 1: pin legacy prepare byte behavior**

Re-run the Task 6 fixtures proving ordinary `prepare` output and anonymous claim behavior remained byte-identical after service extraction. Keep them green before strict additions.

**Step 2: extend the existing shared preparation service with zero legacy behavior change**

Add the strict typed composition API beside Task 6's already-extracted private pair/blast builder while leaving the existing Typer adapter's output identical. Run all prepare tests before adding strict behavior.

**Step 3: write strict red tests**

Freeze exact Python/native argv `tg edit-ready REPO QUERY [--agent-id ID] --validation-file FILE --out NAME [--editable PATH ...] [--review-only PATH ...] [--deadline SECONDS] --json`. Query is 1..16,384 UTF-8 bytes, parser-optional agent ID uses the design's 1..128-character grammar when present, and deadline defaults to 60 with `[0.1,300]` bounds. The service—not parser/config fallback—maps missing ID to the full `anonymous_identity` ticket/exit 2. `FILE` flows through the shared safe bounded JSON reader at 256-KiB cap−1/cap/cap+1 and contains 1..32 exact descriptors; `NAME` is the owned-state basename. First add a behaviorless strict service/CLI registration that emits the exact ticket shape with sentinel reason `missing_required_field`; this closes the import/registration red only. Then red-green each contract slice independently, with a distinct named reason/status/exit assertion—not registration/sentinel—as its red oracle. Cover Python/native help and argv parity plus:

- missing/anonymous identity → exit 2, no claim/baseline write;
- unresolved or ambiguous target → exit 2;
- partial/deadline result → exit 2 with payload;
- foreign overlap → full `EditReadyTicketV1` with `status="blocked"`, `reasons=["claim_overlap"]`, null claim/baseline fields, exit 1, and no state write;
- a pre-existing same-`agent_id` overlap → the identical full blocked-ticket contract;
- two concurrent strict claims for the same scope → exactly one succeeds, under Event-gated scheduling;
- same-root legacy submit, strict submit, and release mutually exclude across the entire read/modify/write transaction, while different roots remain independent;
- with stale lease metadata forced present, process B cannot publish while process A holds the OS fence; after the killed holder's OS lock is released, B publishes from a newly read snapshot while the lease file remains byte-identical;
- symlinked/reparse-point fence files and Event-gated intermediate state-directory swaps before fence creation, after lock acquisition, and before index publication fail closed before any claim mutation, split-brain success, or external-tree change;
- release against an absent index preserves the absent-index fast path, and release with no matching claim preserves existing index bytes, inode, and mtime exactly;
- ledger read/write failure → exit 2;
- symlink/dangling-symlink output → exit 2;
- invalid/empty validation descriptors → exit 2;
- validation-file 0/1/32/33 rows, cap−1/cap/cap+1, empty argv0, NUL, bool-as-number/int, duplicate ID/command, unknown/duplicate/null/wrong-type keys, cwd escape/nonexistent/symlink, leaf link/reparse, parent junction, FIFO/special, and Event-gated swap each have separately named RED nodes;
- shell-shaped legacy validation rows are never converted; descriptors come only from `--validation-file`;
- primary target is always editable; explicit editable additions and caller/blast-floor review-only paths normalize exactly as specified, with duplicate/category-overlap/path-count/root/symlink/new-leaf-parent arms;
- exact baseline request fields are invariant to caller option order;
- same `--out NAME` sequential and Event-gated concurrent calls prove atomic create-if-absent: exactly one artifact is ever published, every existing file/dir/link/reparse is untouched, and each loser rolls back only its exact claim ID;
- deterministic Unix and mandatory Windows parent-swap arms fire after owned-directory handle verification but before temp creation/publication and prove handle-relative anchoring creates no outside artifact; leaf reparse and unavailable-Windows-primitive arms fail closed without any path-based fallback;
- complete, confirmation-tie, validation-resolved tie, deadline-partial, scan-truncated, unrelated-partial, and mixed scan+deadline+unrelated-source real prepare fixtures produce the exact `PrepareSnapshotV1` projection and corresponding readiness gate;
- success → named claim, atomic baseline, self-verification PASS, exit 0;
- self-verification passes the digest of the exact published bytes as `baseline_sha256`; an Event-gated post-publish byte mutation yields `baseline_digest_mismatch`, exit 2, and exact-claim rollback;
- rollback on baseline-write or self-verification failure releases only the exact `claim_id` returned by this invocation.

Use deterministic Event handshakes and bounded acquisition attempts; never assert wall-clock overlap. Service-level critical-section tests may use `threading.Event`, but OS-fence proofs must use two separately spawned processes plus `multiprocessing.Event`/IPC so they exercise actual cross-process crash release and exclusion on both Unix and Windows. Production constants are `poll_interval_s=0.02` and `timeout_s=12.0`; tests inject `timeout_s<=0.25` so the anti-hang 15-second budget is never approached. Non-skipped platform tests cover same-root submit/strict/release exclusion, different-root independence, killed-holder release with unchanged lease metadata, timeout exception identity, and exact final index contents—no lost or duplicated records.

**Step 4: implement strict composition**

Register `edit-ready` through all four CLI sites with only the frozen argv above. The shared service owns the exact prepare projection, scope normalization, safe-reader descriptor validation, no-clobber baseline publication, and baseline request; thin Python/native adapters cannot synthesize fields. Open and identity-verify the owned baseline directory once, then publish only relative to that handle: Unix `openat`/`linkat(dirfd,...,dirfd,..., no-replace)`/`unlinkat` plus file/directory fsync; Windows relative `NtCreateFile` plus `FileRenameInfoEx`/`FILE_RENAME_INFO` with the verified directory as `RootDirectory` and replacement disabled. Never fall back to a path-based publish; unsupported primitives, parent/leaf swaps, or existing/same-NAME races return `baseline_write_failed`, remove only the temp, and roll back only this invocation's exact claim. Preserve release's existing pre-fence missing-index fast path so it creates neither ledger directories nor a fence; every actual RMW goes through callback-style `mutate_claims_index(index_path, callback, *, poll_interval_s=0.02, timeout_s=12.0)`. That helper acquires the claims fence, reads, invokes the callback, and accepts exactly `WRITE(records, result)` or `NO_WRITE(result)`. Only `WRITE` atomically publishes before release; `NO_WRITE` preserves existing bytes/inode/mtime, and callers never receive an independently publishable snapshot. The helper locks a stable per-root `<claims-index>.fence` artifact that normal operation never unlinks or atomically replaces, so all contenders lock the same file object. Starting from an identity-verified canonical-root handle, create missing claims-state components only handle-relative (`mkdirat`-style Unix; `NtCreateFile` with parent `RootDirectory` on Windows), failing closed if unsupported. Hold the final verified state-directory handle; create/open the fence, read the claims index, and publish its replacement only relative to that handle. Unix uses `openat(state_dirfd, ..., O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW, 0600)` and `flock(fence_fd, LOCK_EX|LOCK_NB)`. Windows uses `NtCreateFile` with the state-directory handle as `RootDirectory`, read/write sharing but no delete sharing, then `LockFileEx(LOCKFILE_EXCLUSIVE_LOCK|LOCKFILE_FAIL_IMMEDIATELY)` over byte `[0,1)`. Hold both handles and the exclusive lock across read, callback, handle-relative publication/fsync or `NO_WRITE`; release only in `finally`/RAII after the transaction, with process death providing crash release. Path-based `mkdir`, `O_CREAT`, `CreateFileW(OPEN_ALWAYS)`, reads, or renames after a parent check are forbidden. Revalidate canonical-root/state-directory identity before fence creation, after lock acquisition before read, and before publication. Event-gated two-process tests swap an intermediate component before anchored state/fence creation, after acquisition, and before publication; assert no split-brain success, claim mutation, or external-tree change. `ClaimsFenceTimeoutError` subclasses `IndexLockTimeoutError`, preserving every legacy CLI's current exit-2 mapping; the strict adapter emits full `EditReadyTicketV1` with `status="incomplete"`, reason `claim_fence_timeout`, and exit 2. Other open/lock faults map to `claim_fence_error`/exit 2. Do not change the shared stale-reclaimable `IndexLock` behavior for checkpoint, session, or finding consumers. If claims lease metadata is retained, always acquire the OS fence first; lease expiry/reclaim is diagnostic only and can never authorize a concurrent writer. The strict callback treats every pre-existing overlapping claim as a conflict regardless of self-asserted `agent_id` and returns `NO_WRITE` with the full blocked ticket. Retain the opaque returned `claim_id` and rollback exclusively with `release_claim(claim_id=that_exact_id)`. Assert the released ID equals the captured ID; do not introduce a second nonce or claim-identity schema. Result fields state `authorization=false` and `identity_trust=self-asserted`.

**Step 5: verify and adversarially gate**

```powershell
uv run --no-sync pytest tests/unit/test_edit_ready.py tests/integration/test_edit_ready_command.py tests/integration/test_prepare_oneshot_cuj.py tests/unit/test_anonymous_claim_signal.py tests/unit/test_index_lock.py tests/unit/test_index_lock_concurrency.py tests/unit/test_ledger_concurrency.py -q --timeout=15
uv run --no-sync ruff check src/tensor_grep/cli/prepare_service.py src/tensor_grep/cli/main.py tests/unit/test_edit_ready.py tests/integration/test_edit_ready_command.py
uv run --no-sync ruff format --check --preview src/tensor_grep/cli/prepare_service.py src/tensor_grep/cli/main.py tests/unit/test_edit_ready.py tests/integration/test_edit_ready_command.py
uv run --no-sync mypy src/tensor_grep/cli/prepare_service.py src/tensor_grep/cli/main.py
cargo fmt --manifest-path rust_core/Cargo.toml --check
```

Mandatory security review attacks identity spoofing, claim-release races, symlink outputs, stale baselines, root escapes, partial results, and fail-open exception paths.

Add `tests/e2e/test_native_edit_ready.py` to the native-build-smoke census with `TG_REQUIRE_RG_PARITY=1`. The file itself pins compiled help/argv, query/ID/deadline/validation/scope bounds, safe-reader malformed/link/swap arms, anonymous refusal, same-ID overlap, same-NAME no-clobber, success, stdout JSON, and preserved exits. Missing binary or a skipped node is a CI failure.

## Task 9: make reference/caller dispatch registry-driven without changing output

**Files:**

- Modify: `src/tensor_grep/cli/repo_map.py`
- Modify: `src/tensor_grep/cli/lang_registry.py`
- Create: `tests/unit/test_language_reference_dispatch.py`
- Modify: `tests/eval/test_agent_accuracy.py`
- Modify: `tests/unit/test_repo_map_graph.py`

**Step 1: pin current ranked output green on base**

Add fixtures spanning Python, JS/TS, Rust, and Go. Pin full ordered definitions/references/callers plus provenance, resolution confidence, gaps, and partial fields.

**Step 2: add a registry-dispatch red test**

Register a synthetic language spec with a spy `references_and_calls` function. Assert both refs and callers builders invoke the registered function rather than falling through to `_regex_references_and_calls`.

**Step 3: refactor the two hard-coded dispatch ladders**

Add a shared invocation adapter that handles uniform registry signatures plus the Go definition-directory context. Preserve JS/TS/Rust provider-alias and regex fallbacks exactly. Foundational languages with `references_and_calls=None` keep their current honest fallback until their own wave.

**Step 4: prove zero output drift**

```powershell
uv run --no-sync pytest tests/unit/test_language_reference_dispatch.py tests/unit/test_repo_map_graph.py tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate -q --timeout=15
```

Any legitimate-entry reorder is a stop finding. Do not update the pin unless the following language feature explicitly intends that change.

## Task 10: deliver five parser-backed language waves

Execute each subtask as an independent release PR in this order: Java, C#, PHP, C, C++. Rebase each onto the previously merged language wave and union all shared registry tests. For every newly promised AST/config behavior below, add one named pytest node and run that node alone before its implementation; record its behavior-specific assertion failure. Only after each node has its own RED may the complete file run. This is mandatory because global pytest `-x` would otherwise let the first registry failure mask untested ordering, decoy, provenance, grammar-missing, or AST-shape arms.

### Task 10A: Java references and calls

**Files:**

- Create: `src/tensor_grep/cli/lang_java.py` and move Java-specific extraction behind that module's registered seams
- Modify: `src/tensor_grep/cli/repo_map.py` to replace Java's `references_and_calls=None` registration with the new extractor
- Modify: `tests/unit/test_lang_java.py`
- Modify: `tests/unit/test_language_reference_dispatch.py`
- Modify: `docs/tool_comparison.md`

First keep a base-green test characterizing the current regex fallback. Then write a pre-fix-red assertion that Java's registered `references_and_calls` is non-`None`, emitted provenance is parser-backed, exact `ref_kind`/ordering is pinned, and an AST-only qualified/member/constructor distinction defeats the regex fallback. Add AST-shape tests for `method_invocation`, `object_creation_expression`, qualified/member calls, constructor/type references, same-name declarations, strings/comments, and grammar absence. Implement `java_references_and_calls` in the new module and register it.

### Task 10B: C# references and calls

**Files:**

- Modify: `src/tensor_grep/cli/lang_csharp.py`
- Modify: `src/tensor_grep/cli/repo_map.py`
- Modify: `tests/unit/test_lang_csharp.py`
- Modify: `tests/unit/test_language_reference_dispatch.py`

Keep a base-green regex-fallback characterization, then require a red non-`None` registry assertion, parser-backed provenance, exact `ref_kind`/ordering, and an AST-only invocation/member-access/object-creation/generic-name distinction that regex cannot satisfy. Cover aliases, same-name decoys, and grammar absence. Do not claim `.csproj` resolution until a later resolver reads it.

### Task 10C: PHP references and calls

**Files:**

- Modify: `src/tensor_grep/cli/lang_php.py`
- Modify: `src/tensor_grep/cli/repo_map.py`
- Modify: `tests/unit/test_lang_php.py`
- Modify: `tests/unit/test_language_reference_dispatch.py`

Keep a base-green regex-fallback characterization, then require a red non-`None` registry assertion, parser-backed provenance, exact `ref_kind`/ordering, and an AST-only member/static/object-creation/namespaced distinction. Cover aliases, dynamic-call honesty, decoys, and grammar absence.

### Task 10D: C references and calls

**Files:**

- Modify: `src/tensor_grep/cli/lang_c.py`
- Modify: `src/tensor_grep/cli/repo_map.py`
- Modify: `tests/unit/test_lang_c.py`
- Modify: `tests/unit/test_language_reference_dispatch.py`

Keep a base-green regex-fallback characterization, then require a red non-`None` registry assertion, parser-backed provenance, exact `ref_kind`/ordering, and an AST-only `call_expression` versus declaration/function-pointer distinction. Cover field/member calls, type references, macro/preprocessor honesty, decoys, and grammar absence. Do not fabricate include targets.

### Task 10E: C++ references and calls

**Files:**

- Modify: `src/tensor_grep/cli/lang_cpp.py`
- Modify: `src/tensor_grep/cli/repo_map.py`
- Modify: `tests/unit/test_lang_cpp.py`
- Modify: `tests/unit/test_language_reference_dispatch.py`

Keep a base-green regex-fallback characterization, then require a red non-`None` registry assertion, parser-backed provenance, exact `ref_kind`/ordering, and an AST-only qualified/template/member/operator/constructor distinction. Cover type references, overload ambiguity, macro/preprocessor honesty, same-name decoys, and grammar absence. Preserve the accepted `class MACRO Name` limitation unless a preprocessor-aware oracle is added.

### Verification for every language wave

```powershell
$tgLanguage = "java" # set to exactly one of: java, csharp, php, c, cpp for that wave
uv run --no-sync pytest "tests/unit/test_lang_$tgLanguage.py" tests/unit/test_language_reference_dispatch.py tests/unit/test_repo_map_graph.py tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate tests/eval/test_retrieval_quality_regression.py -q --timeout=15
uv run --no-sync ruff check "src/tensor_grep/cli/lang_$tgLanguage.py" src/tensor_grep/cli/repo_map.py "tests/unit/test_lang_$tgLanguage.py"
uv run --no-sync ruff format --check --preview "src/tensor_grep/cli/lang_$tgLanguage.py" src/tensor_grep/cli/repo_map.py "tests/unit/test_lang_$tgLanguage.py"
uv run --no-sync mypy "src/tensor_grep/cli/lang_$tgLanguage.py" src/tensor_grep/cli/repo_map.py
```

Run decisive accuracy/retrieval matrices in CI/cloud. Published-wheel dogfood must include one positive call, one same-name decoy, and one grammar-missing disclosure for the shipped language.

## Task 11: implement truthful cross-file resolution in six separate waves

This is separate from Task 10's in-file AST caller/reference depth. Every wave modifies its language module, `src/tensor_grep/cli/repo_map.py`, `src/tensor_grep/cli/lang_registry.py`, the named language test, `tests/unit/test_language_reference_dispatch.py`, `tests/eval/test_agent_accuracy.py`, and `docs/tool_comparison.md`. Each begins with a base-green unresolved payload pin and a pre-fix-red resolved-edge test. Each published-wheel triplet is: one resolved edge, one same-name decoy excluded, and one unsupported/config-missing `resolution_gaps` entry.

All six waves first use one shared bounded configuration-reader contract, either as a small common module or byte-identical per-language adapters covered by one contract test. It opens through identity-verified no-follow handles confined to the workspace and permits at most 64 configuration files, 8 MiB per file, 32 MiB total exact opened bytes, 10,000 literal mapping/include rows, and 10,000 derived roots. Absolute/`..` mappings, project references, source roots, working directories, and include roots that escape the workspace—plus symlink/junction/reparse escapes—are rejected before the target is read. Malformed, dynamic, oversized, count-limited, or identity-swapped inputs produce an explicit fixed-vocabulary `config_invalid|config_limit|config_outside_workspace|config_identity_changed` resolution gap and no partial mapping. Each language wave adds absolute, `..`, symlink/junction, malformed, per-file/aggregate/count boundary, and Event-gated config-leaf/parent swap controls on Unix and mandatory Windows CI; the external target/tree remains unread and byte-identical. These tests are additive to the language-specific fixtures below.

### Task 11A: Java package/source-root resolution

**Files:** `src/tensor_grep/cli/lang_java.py`, `tests/unit/test_lang_java.py`

Version 1 supports package declarations plus conventional Maven/Gradle `src/main/java` and `src/test/java` roots, and literal Maven `<sourceDirectory>`/`<testSourceDirectory>` values. Dynamic Gradle source-set code and property expansion remain explicit gaps. Fixture: `app/src/main/java/com/acme/Caller.java` imports `com.lib.Foo`; `lib/src/main/java/com/lib/Foo.java` exports `Foo`; a `decoy/src/main/java/com/other/Foo.java` must not resolve. Pin `resolution_provenance=["java-package","java-source-root","reverse-export"]`, the resolved target, exact ordering, and the missing/custom-source-root gap.

Focused command:

```powershell
uv run --no-sync pytest tests/unit/test_lang_java.py tests/unit/test_language_reference_dispatch.py tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate -q --timeout=15
```

### Task 11B: Go module/import resolution

**Files:** `src/tensor_grep/cli/lang_go.py`, `tests/unit/test_lang_go.py`

Version 1 reads the nearest `go.mod` module path and literal imports/replaces whose target stays within the workspace. Fixture: module `example.com/app` imports `example.com/lib/foo`; the target package exports `Foo`; another package exports a decoy `Foo`. Pin current unresolved output before the new configuration arm, exact `go-module-import`/`reverse-export` provenance after, and explicit gaps for missing `go.mod`, external replace targets, and ambiguous package directories.

```powershell
uv run --no-sync pytest tests/unit/test_lang_go.py tests/unit/test_language_reference_dispatch.py tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate -q --timeout=15
```

### Task 11C: PHP Composer PSR-4 resolution

**Files:** `src/tensor_grep/cli/lang_php.py`, `tests/unit/test_lang_php.py`

Version 1 reads literal `composer.json` `autoload.psr-4` and `autoload-dev.psr-4` maps plus namespace/use declarations. Scripts/plugins and generated Composer metadata are not executed. Fixture maps `Acme\\` to `src/`, imports `Acme\\Service\\Foo`, and includes an unmapped same-name decoy. Pin exact `composer-psr4`/`reverse-export` provenance, ordering, and gaps for malformed/missing/dynamic config.

```powershell
uv run --no-sync pytest tests/unit/test_lang_php.py tests/unit/test_language_reference_dispatch.py tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate -q --timeout=15
```

### Task 11D: C# project resolution

**Files:** `src/tensor_grep/cli/lang_csharp.py`, `tests/unit/test_lang_csharp.py`

Version 1 reads literal `.csproj` `Compile Include/Remove`, `ProjectReference`, and `RootNamespace` values without executing MSBuild or expanding non-literal properties. Fixture has App→Lib `ProjectReference`, `using Lib.Services`, exported `Foo`, and a same-named unreferenced project. Pin `csproj-project-reference`/`csharp-namespace`/`reverse-export` provenance and gaps for property-expanded, SDK-generated, or missing project configuration.

```powershell
uv run --no-sync pytest tests/unit/test_lang_csharp.py tests/unit/test_language_reference_dispatch.py tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate -q --timeout=15
```

### Task 11E: C compile-database include resolution

**Files:** `src/tensor_grep/cli/lang_c.py`, `tests/unit/test_lang_c.py`

Version 1 reads `compile_commands.json` entries and extracts explicit `-I`, `-isystem`, and working-directory-relative include roots without executing compiler commands. Fixture includes `include/acme/foo.h` exporting `foo`, a source entry with the required include path, and an unlisted decoy header. Pin `compile-commands-include`/`reverse-export` provenance; unresolved/system/generated/macro include paths remain gaps.

```powershell
uv run --no-sync pytest tests/unit/test_lang_c.py tests/unit/test_language_reference_dispatch.py tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate -q --timeout=15
```

### Task 11F: C++ compile-database include resolution

**Files:** `src/tensor_grep/cli/lang_cpp.py`, `tests/unit/test_lang_cpp.py`

Use the same non-executing compile-database contract as C with C++ header suffixes, namespaces, templates, and reverse export confirmation. Fixture resolves `acme::Foo` through a listed include root while excluding a same-named namespace/header decoy. Pin `compile-commands-include`/`cpp-namespace`/`reverse-export` provenance and explicit gaps for modules, generated headers, macro includes, and absent databases.

```powershell
uv run --no-sync pytest tests/unit/test_lang_cpp.py tests/unit/test_language_reference_dispatch.py tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate -q --timeout=15
```

For every Task 11 wave, run the retrieval-quality gate in CI/cloud, pin the complete ordered payload, and perform the published-wheel triplet before the next language merges.

## Task 12: federated multi-root prepare, internal service first

**Files:**

- Modify: `src/tensor_grep/cli/prepare_service.py`
- Create: `src/tensor_grep/cli/workspace_prepare.py`
- Create: `tests/unit/test_workspace_prepare.py`
- Create: `tests/integration/test_workspace_prepare_command.py`
- Create: `tests/e2e/test_native_workspace_prepare.py`
- Modify: `src/tensor_grep/cli/main.py`
- Modify: `src/tensor_grep/cli/commands.py`
- Modify: `rust_core/src/main.rs`
- Modify: `tests/e2e/test_routing_parity.py`
- Modify: `tests/unit/test_cli_bootstrap.py`
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/harness_api.md`
- Modify: `src/tensor_grep/cli/runtime_paths.py`
- Modify: `rust_core/src/path_domain.rs`
- Modify: `tests/unit/test_wsl_path_domain.py`
- Modify: `tests/fixtures/path_domain_v1.json`
- Modify: `tests/e2e/test_native_wsl_path_domain.py`

**WSL path-domain extension:** register `ANCHOR` and every `--root ROOT` as typed fields for Python/direct-native routes; query remains opaque and relative roots retain their documented anchor-relative meaning. Pin shared vectors, complete owner/field census, unchanged-test mapping mutations, no-dispatch failure, non-skipped native CI, and published WSL dogfood.

**Step 1: red schema/aggregation tests**

First add a behaviorless `workspace_prepare` service plus CLI/native registrations that emit the exact schema with `result_incomplete=true`; this closes only the missing-API/registration red. Then red-green schema, validation, ordering, deadline, aggregation, and wire-cap slices independently. Pin the exact argv `tg workspace-prepare ANCHOR QUERY --root ROOT [--root ROOT ...] [--deadline SECONDS] --json`. Typer collects absent `--root` as an empty list so 0-root input reaches the service JSON arm. Cover 0/1/2/8/9 roots, duplicate/nested roots, relative roots resolved against the anchor, absolute roots inside/outside the anchor, symlink escapes, nonexistent roots, mixed success/failure, shared-deadline exhaustion, omitted roots, canonical path ordering, aggregate `result_incomplete` truth, query UTF-8 bytes at 0/1/16,384/16,385, anchor/root bytes at 32,768/32,769, nonfinite/nonpositive/>300-second deadlines, and the final-wire 8,388,608-byte cap at cap - 1/cap/cap + 1.

Pin the shared service/CLI workspace schema version 1 exactly as amended in the design, including always-present `path_domain:PathDomainEvidenceV1|null`, routing reason `path-domain-error`, error code and incomplete reason `path_domain_mismatch`. Non-WSL uses null; translated WSL success uses status translated; bridge failure occurs before canonicalization/dispatch and emits the full key set with null anchor, valid query or null, empty roots/omissions, zero counts, `routing_reason="path-domain-error"`, `result_incomplete=true`, reasons `['path_domain_mismatch']`, sanitized error, error evidence, and exit 2. Zero/duplicate/nested/out-of-anchor/>8-root remains `invalid-input`. Require exact nullability, reason precedence, final-wire cap accounting after path-domain/MCP/newline injection, and service/Python/native equality for success, partial, invalid, omitted, output-limit, and every path-domain subreason.

Choose sequential execution in canonical root order under one shared absolute deadline. Remove root-parallelism and lock-overlap claims; tests spy the exact dispatch order and prove that root N+1 receives only the remaining deadline. The 1/2/8-root CI benchmark is a scaling observation for this sequential contract, not a concurrency claim.

**Step 2: implement explicit bounded aggregation**

Accept only explicit roots. With absent provenance, directly canonicalize/confine. With WSL provenance, grammar/size-check caller strings, translate the complete anchor/root set, then canonicalize/confine in the target domain before dispatch. Allocate one shared absolute deadline and report every omitted root. Use one compact UTF-8 serializer with transport fields/suffix supplied before the 8,388,608-byte inclusive measurement; CLI's trailing newline is inside its cap. Payload omission produces the same minimal envelope, and tests assert it fits at maximum valid query/path/error inputs. Do not create cross-root ledger enforcement.

**Step 3: expose CLI only after service tests pass**

Register the separately versioned `workspace-prepare` command through all four sites. The Typer adapter accepts positional `ANCHOR`, positional `QUERY`, and parser-optional repeatable `--root`; the service semantically requires 1–8 roots. It calls `workspace_prepare` directly and never changes the existing `prepare` signature or payload. Exact zero-root tests execute both Typer and the compiled native command, parse the raw stdout JSON as the shared invalid-input envelope, and assert exit 2 with no parser-prose substitution.

**Step 4: benchmark in CI/cloud**

Measure 1/2/8 roots and assert fixtures actually cross the intended work boundary. Pin latency and token/output ceilings as regression alerts, not unsupported universal claims.

**Step 5: execute through the compiled native front door**

Add `tests/e2e/test_native_workspace_prepare.py` to the existing native-build-smoke census. Missing binary fails under the CI marker. Exercise one-root success, two-root ordered success, out-of-anchor exit 2, and a partial root exit 2, asserting raw stdout JSON and the process exit code.

## Task 13: MCP exposure for federated prepare

**Files:**

- Modify: `src/tensor_grep/cli/mcp_server.py`
- Modify: `tests/unit/test_mcp_server.py`
- Modify: `tests/integration/test_mcp_stdio_protocol.py`
- Modify: `tests/unit/test_mcp_contract_version_docs_are_pinned.py`
- Modify: `tests/unit/test_mcp_contract_fixes.py`
- Modify: `tests/unit/test_harness_api_docs.py`
- Modify: `docs/harness_api.md`
- Modify: `docs/CONTRACTS.md`
- Modify: MCP contract/version docs and pins
- Modify: `src/tensor_grep/cli/runtime_paths.py`
- Modify: `tests/unit/test_wsl_path_domain.py`
- Modify: `tests/fixtures/path_domain_v1.json`
- Rerun unchanged: `rust_core/src/path_domain.rs` conformance tests and `tests/e2e/test_native_wsl_path_domain.py`

**WSL path-domain extension:** register MCP `anchor` and every `roots[]` element. Perform caller-string preflight, then translation, target-domain `_mcp_root`/anchor confinement, canonical duplicate/nesting checks, and only then dispatch. Query remains opaque. Pin direct function and stdio treatment/control, unchanged-test production mutations, no-dispatch failure, and published full/lean WSL dogfood using the exact `PathDomainEvidenceV1` object.

Expose exact always-on task tool `tg_workspace_prepare`, available in both full and lean surfaces. It accepts `{anchor: str, query: str, roots: list[str], deadline: float | null}` and returns Task 12's exact shared envelope, whose always-present `path_domain` is null/translated/error; MCP injects only `mcp_contract_version`. Success, partial, invalid-input, and path-domain-error arms preserve that key set and meanings; the adapter does not invent a second error envelope. The shared serializer injects all transport fields before enforcing the 8,388,608-byte inclusive final tool-string cap and reserves their exact overhead.

Absent provenance retains direct confinement. With WSL provenance, the adapter grammar/size-checks caller strings, translates typed fields, resolves the translated `anchor` through `_confine_mcp_path` under `_mcp_root()`, then contains every translated canonical root under both. Add absolute, `..`, symlink/junction escape, translated escape/alias, 0/1/8/9-root, shared-deadline, omitted-root, every path-domain subreason, and final-tool-string cap - 1/cap/cap + 1 tests. Same-fixture equality compares service, Python CLI, native CLI, and real MCP stdio for success, partial, invalid, and path-domain arms after removing only MCP version; boundary tests compare actual bytes.

Write the registration red first, add a behaviorless live tool, then red-green confinement, equality, partial, and cap behavior independently so none can pass merely because registration/import is missing. Because Task 2C moved the contract to 1.8.0 and Task 4 moved it to 1.9.0, this additive tool-set change bumps it to 1.10.0 and updates every exact pin in `mcp_server.py`, MCP unit tests, stdio integration tests, contract-doc tests, and harness docs. The always-on population changes from 58/12 to 59/13; subprocess flag arms assert both exact sets. Real stdio tests must call `tools/list`, invoke `tg_workspace_prepare`, validate the error/success envelopes, and prove the legacy tools remain callable in full mode. Reuse `workspace_prepare` directly; never shell out to the CLI.

Mandatory adversarial MCP review and published-wheel stdio dogfood for full and lean surfaces are required.

## Task 14: ship bounded graph projection and change-impact context

This task starts only after Tasks 10–13 have merged, published, and been reverified. Task 14T first lands tracker and independent-oracle truth in a non-releasing PR. Tasks 14A and 14B then run as independent candidate PRs whose value gates execute before merge; a failed candidate closes unmerged and gets only a non-releasing evidence-retirement change. Task 14C is generated from the exact survivor branch table and is release-affecting only when at least one action survived. Do not stack work across an in-flight publish. Every merged PR rebases on the prior completed/published `origin/main`, unions shared registry assertions, reruns the affected suite, updates the two build rows honestly, and preserves the five research rows' demand-gated owners/triggers.

Task 14T lands oracle-side schemas/validators/hand-gold fixtures but no production graph module, CLI command, MCP registration, or contract bump. Every candidate must be buildable from the Task 14T base without the other candidate: its first commits carry the exact production foundation bundle `graph_contract.py`, `bounded_git.py`, `graph_fs.py`, foundation tests, and any candidate-specific fixture adapters before a public consumer is added. If 14A retires unmerged, 14B re-carries that whole bundle; if 14A ships, 14B reuses and mutation-pins the exact landed helpers. No public command or MCP registration lands merely to share this foundation.

**Files:**

- Create: `src/tensor_grep/cli/code_graph.py`
- Create: `src/tensor_grep/cli/graph_contract.py`
- Create: `src/tensor_grep/cli/change_impact.py`
- Create: `src/tensor_grep/cli/bounded_git.py`
- Create: `src/tensor_grep/cli/graph_fs.py`
- Modify: `src/tensor_grep/cli/repo_map.py`
- Modify: `src/tensor_grep/cli/main.py`
- Modify: `src/tensor_grep/cli/commands.py`
- Modify: `src/tensor_grep/cli/mcp_server.py`
- Modify: `src/tensor_grep/cli/runtime_paths.py`
- Modify: `rust_core/src/main.rs`
- Create: `tests/unit/test_code_graph_projection.py`
- Create: `tests/unit/test_change_impact.py`
- Create: `tests/unit/test_bounded_git.py`
- Create: `tests/unit/test_graph_fs.py`
- Create: `tests/unit/test_graph_value_oracle.py`
- Create: `tests/eval/graph_control.py`
- Create: hand-authored gold/counterexample manifests under `tests/fixtures/graph_v1/`
- Create: `tests/integration/test_change_impact_cli.py`
- Create: `tests/e2e/test_native_change_impact.py`
- Modify: `tests/e2e/test_native_map.py`
- Modify: `tests/e2e/test_native_wsl_path_domain.py`
- Modify: `tests/e2e/test_routing_parity.py`
- Modify: `tests/unit/test_mcp_server.py`
- Modify: `tests/integration/test_mcp_stdio_protocol.py`
- Modify: `tests/unit/test_mcp_contract_version_docs_are_pinned.py`
- Modify: `tests/unit/test_mcp_contract_fixes.py`
- Modify: `tests/unit/test_harness_api_docs.py`
- Modify: `tests/unit/test_wsl_path_domain.py`
- Modify: `tests/fixtures/path_domain_v1.json`
- Modify: `tests/eval/test_agent_accuracy.py`
- Create: `tests/eval/test_change_impact_value.py`
- Modify: `tests/unit/test_mcp_dependency_is_upper_bounded.py`
- Modify: `pyproject.toml` only in a survivor Task 14C branch
- Modify: `uv.lock` only in a survivor Task 14C branch
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/harness_api.md`
- Modify: `docs/tool_comparison.md`
- Create: `docs/research/graph_coding.md`
- Create: `docs/guides/graph_runtime_interop.md`
- Modify: `docs/TASK_BOARD.md`
- Modify: `docs/BACKLOG.md`
- Modify: `docs/SESSION_HANDOFF.md`
- Modify: `tests/unit/test_backlog_tracker_truth.py`

**Frozen v1 types:**

- `RelationName = "contains"|"defines"|"imports"|"validates"|"calls"|"references"`; `CoverageState = "materialized"|"query_only"|"unsupported"`; `ImpactRelation = "changed_files"|"symbols"|"callers"|"imports"|"tests"`; and every ordered subset follows the declaration order here.
- `GraphRepositoryV1 = {root:".", identity_sha256:LowerHex64, identity_authoritative:false}`. Resolve the confined Git top-level and `HEAD`, obtain the object format plus sorted root-commit OIDs reachable from that exact HEAD using the hardened adapter, and hash the compact UTF-8 JSON array `["tensor-grep-repository-v1", object_format, root_oids]`. Every string is NFC. The value is path/clone stable for the same reachable lineage, changes with lineage, includes no path/remote, and grants no authority. Each explicit impact base/head commit must resolve to exactly the same sorted root-commit set as current `HEAD`; an orphan/unrelated-history ref is `invalid_ref`, lower-bound, and exit 2.
- `GraphRevisionV1 = {object_format:"sha1"|"sha256", commit_sha:LowerHex40|LowerHex64, dirty_tree_sha256:LowerHex64|null, dirty:bool, capture_consistency:"stable_non_atomic", snapshot_atomic:false}`; object IDs agree with the format and dirty digest is non-null iff dirty. The dirty digest is SHA-256 of the compact canonical manifest built from the same safely captured index/worktree/nonignored-untracked records and never from a second Git status/diff read. Per-file stable reads do not claim an operating-system cross-file snapshot.
- `GraphNodeV1 = {id:LowerHex64, type:"repository"|"file"|"test"|"symbol", path:str|null, name:str|null, symbol_kind:str|null, language:str|null, start_line:int|null, end_line:int|null, provenance:"repository"|"parser"}`. Exactly one repository node has all nullable fields null and repository provenance. File/test nodes have only path non-null and repository provenance; every path occurs as exactly one of those types. Symbol nodes have every field from path through end line non-null, parser provenance, positive ordered lines, and an included file/test path. For a symbol, `name` is exactly the NFC canonical qualified name used in duplicate grouping and the ID preimage; a local/display-only spelling is never substituted.
- Node IDs hash compact NFC JSON arrays: repository `["tg-graph-node-v1",repo_id,"repository"]`; file/test `["tg-graph-node-v1",repo_id,type,path]`; symbol `["tg-graph-node-v1",repo_id,"symbol",path,language,symbol_kind,qualified_name,ordinal]`. `ordinal` is zero-based after sorting same `(path,language,kind,qualified_name)` definitions by `(start_line,end_line,qualified_name,kind,language)`. No absolute root, remote, or revision enters an ID.
- `GraphEdgeV1 = {id:LowerHex64, type:"contains"|"defines"|"imports"|"validates", source:LowerHex64, target:LowerHex64, provenance:"repository"|"parser"|"resolver"|"test-matcher", confidence:"exact"|"heuristic"}`. Valid matrix only: `contains` repository→file/test `repository/exact`; `defines` file/test→symbol `parser/exact`; `imports` file/test→file/test `resolver/exact`; `validates` test→file/symbol `test-matcher/heuristic`. Every file/test has exactly one inbound contains, every symbol exactly one inbound defines, and no self/dangling/reversed edge exists. Edge ID hashes `["tg-graph-edge-v1",repo_id,type,source,target,provenance,confidence]`.
- `GraphCoverageV1 = {path_scope:"repository_local_tracked_plus_nonignored_untracked", git_admin_source_channel:"excluded", worktree_census_started:bool, path_names_observed:bool, local_ignore_evaluation_started:bool, local_ignore_evaluation_completed:bool, worktree_untracked_evaluation_completed:bool, ignored_content_unread:true, external_git_config_unobserved:true, attributes_transforms_unapplied:true, capture_consistency:"stable_non_atomic", snapshot_atomic:false, relations:{contains:CoverageState, defines:CoverageState, imports:CoverageState, validates:CoverageState, calls:CoverageState, references:CoverageState}, languages:list[LanguageCoverageV1]}` with exact language records `{language:str, contains:CoverageState, defines:CoverageState, imports:CoverageState, validates:CoverageState, calls:CoverageState, references:CoverageState}`. Every supported stage-0 tracked path is in scope even if an ignore pattern matches; local ignore rules select only untracked paths. All phase booleans start false; census-start flips immediately before the first enumeration attempt, path-observed on the first returned entry, ignore-start immediately before root untracked-selection policy evaluation (including an empty set), ignore-completed only after all hierarchical selection, and untracked-completed only after stable capture of that set (including an empty set). Error/final-wire arms preserve reached phases. Every fixed disclosure exists even when revision is null and survives CLI/native/MCP adaptation.
- `GraphGapV1 = {code:"grammar_unavailable"|"parse_error"|"dynamic_unresolved"|"config_missing"|"mapping_unsupported"|"ambiguous_rename"|"unsafe_path"|"platform_no_follow_unavailable"|"input_limit"|"deadline"|"output_limit"|"lazy_fetch_refused"|"repository_format_unsupported", relation:RelationName|null, path:str|null, symbol:str|null, detail:str}`. `platform_no_follow_unavailable` maps to top-level `unsafe_path`/its fixed message. `detail` is selected from a test-pinned fixed template table by code; no raw parser detail, exception repr, ref, absolute path, username, remote, environment value, source text, or credential is interpolated.
- `OmissionCountV1 = {count:int|null, exact:bool}` where `exact=true` iff count is a nonnegative exact value; any early stop that makes the unseen population unknowable is `{count:null,exact:false}`. `GraphOmissionsV1 = {nodes:OmissionCountV1, edges:OmissionCountV1, gaps:OmissionCountV1}` and `GraphLimitsV1 = {max_root_commits:256, max_visited_entries:20000, max_config_bytes:1048576, max_control_bytes:4096, max_loose_refs:10000, max_ref_bytes:4096, max_ref_bytes_total:4194304, max_packed_refs_bytes:8388608, max_index_bytes:16777216, max_metadata_files:10256, max_metadata_bytes:67108864, max_metadata_read_bytes:134217728, max_pack_indexes:128, max_pack_index_bytes:33554432, max_pack_bytes_per_file:268435456, max_pack_verification_bytes:536870912, max_object_physical_bytes:67108864, max_object_logical_bytes:134217728, pack_read_chunk_bytes:65536, max_ref_hops:8, max_delta_depth:64, max_files:5000, max_path_bytes:32768, max_ignore_files:64, max_ignore_patterns:10000, max_ignore_bytes:2097152, max_ignore_match_operations:10000000, max_file_bytes:2097152, max_file_lines:100000, max_input_bytes:33554432, max_input_read_bytes:67108864, max_live_buffer_bytes:67108864, max_parser_nodes:2000000, max_nodes:5000, max_edges:4000, max_gaps:10000, max_output_bytes:8388608, deadline_seconds:float}`. Object physical bytes count actual loose-zlib/pack-entry compressed input consumed for semantic decoding; logical bytes count decompressed bodies, every repeated delta input/output, and diff/hunk intermediates, cumulatively without refunds. `GraphIncompleteReason` is the ordered subset `invalid_input,not_git,path_domain_mismatch,repository_format_unsupported,grammar_unavailable,parse_error,resolution_gap,node_limit,edge_limit,gap_limit,input_limit,lazy_fetch_refused,output_limit,deadline,unsafe_path,analysis_failed`.
- `GraphErrorV1 = {code:"invalid_input"|"not_git"|"invalid_ref"|"unsupported_repository"|"unsafe_path"|"path_domain_mismatch"|"analysis_failed", message:str}`. Messages are exactly: `invalid graph input`, `requested path is not a supported Git worktree`, `Git revision could not be resolved locally`, `repository storage format is unsupported safely`, `repository path could not be read safely`, `path domain could not be bridged safely`, or `graph analysis could not complete safely`, respectively.
- `DiffIdentityV1 = {base_commit:LowerHex40|LowerHex64, head_kind:"commit"|"worktree", head_commit:LowerHex40|LowerHex64|null, dirty_tree_sha256:LowerHex64|null}` with the last two fields null/non-null according to head kind. In worktree mode the dirty digest binds the exact captured stage-0 index, tracked-worktree, and nonignored-untracked manifests consumed by all three phase transitions; it is not a net base→worktree digest.
- `DiffHunkV1 = {old_start:int, old_count:int, new_start:int, new_count:int}` with nonnegative counts and one-based nonzero starts when that side contains lines.
- `ChangePhase = "commit"|"staged"|"unstaged"|"untracked"` in declaration order. `ChangedFileV1 = {id:LowerHex64, phase:ChangePhase, path:str, old_path:str|null, status:"added"|"modified"|"deleted"|"renamed"|"untracked"|"submodule"|"type_changed", change_types:list["added"|"deleted"|"renamed"|"content_modified"|"mode_modified"|"type_modified"|"untracked"], mode_before:"100644"|"100755"|"120000"|"160000"|null, mode_after:"100644"|"100755"|"120000"|"160000"|null, hunks:list[DiffHunkV1], sha256_before:LowerHex64|null, sha256_after:LowerHex64|null, gitlink_oid_before:LowerHex40|LowerHex64|null, gitlink_oid_after:LowerHex40|LowerHex64|null}`. Explicit-head permits only `commit`; omitted-head permits only `staged`, `unstaged`, and `untracked`. `change_types` is a nonempty declaration-ordered set. Old path is non-null only for rename; deleted has only before identity/mode; added/untracked only after; modified/renamed may have both. A side with mode `160000` has its repository-format gitlink OID non-null and SHA-256 null; every other present side has SHA-256 non-null and gitlink OID null. Byte-identical executable-bit changes carry `mode_modified`; regular-file/symlink/submodule transitions carry `type_modified` and preserve both normalized modes/identities. Submodule content is never traversed. Multiple events may share a normalized path only at distinct phases. File event ID hashes `["tg-change-file-v1",repo_id,base_oid,head_identity,phase,old_path,path,mode_before,mode_after,sha256_before,sha256_after,gitlink_oid_before,gitlink_oid_after,change_types]`; `head_identity` is the explicit head OID or `worktree:` plus the captured dirty digest.
- `ChangedSymbolV1 = {id:LowerHex64, file_event_id:LowerHex64, phase:ChangePhase, before_node_id:LowerHex64|null, after_node_id:LowerHex64|null, path:str, old_path:str|null, name:str, symbol_kind:str, language:str, change_types:list["added"|"deleted"|"renamed"|"content_modified"], start_line_before:int|null, end_line_before:int|null, start_line_after:int|null, end_line_after:int|null}`. Phase equals the retained parent file event's phase. `name` is the parser's qualified semantic name and `change_types` is a nonempty declaration-ordered set. Added has only after identity/lines and no old path; deleted only before; rename has both IDs and old path and may also contain `content_modified`; ordinary modification has equal before/after node IDs and only `content_modified`. Symbol event ID hashes `["tg-change-symbol-v1",repo_id,base_oid,head_identity,phase,file_event_id,before_node_id,after_node_id,change_types]`.
- `ImpactSourceV1` is exactly `{kind:"changed_file", changed_file_id:LowerHex64, symbol_event_id:null}` or `{kind:"changed_symbol", changed_file_id:null, symbol_event_id:LowerHex64}`. `AffectedFileV1 = {path:str, reason:"changed"|"caller"|"importer", sources:list[ImpactSourceV1], confidence:"exact"|"heuristic", provenance:"git"|"parser"|"resolver"}`; `AffectedTestV1` adds reason `test_match` and provenance `test-matcher`. Every source resolves to a retained changed transition; source order is phase declaration order then event ID, with exact duplicate sources removed. Records union across phases by `(record_kind,path,reason,provenance)`. Sources union canonically and confidence uses the fixed conservative lattice `exact < heuristic`, so any heuristic observation yields heuristic; the provenance/confidence valid matrix remains `git|parser→exact`, `resolver|test-matcher→exact|heuristic`. Different provenance remains a distinct record.
- `ValidationHintV1 = {display:str, reason:"changed_language"|"affected_test"|"repository_policy", sources:list[ImpactSourceV1], authorization:false, trust:"untrusted_inert"}`. Display is copied opaquely without tokenization, is 1..4,096 UTF-8 bytes, and there are at most 256 hints; no TG code executes or converts it to argv. Same `(display,reason,authorization,trust)` hints across phases union canonical sources; different displays/reasons remain distinct.
- `ImpactTrustV1 = {level:"complete"|"lower_bound", requested_head_kind:"commit"|"worktree", coverage:"repository_local_commit_diff"|"repository_local_base_index_worktree_plus_nonignored_untracked", git_admin_source_channel:"excluded", worktree_census_started:bool, path_names_observed:bool, local_ignore_evaluation_started:bool, local_ignore_evaluation_completed:bool, worktree_untracked_evaluation_completed:bool, ignored_content_unread:true, external_git_config_unobserved:true, attributes_transforms_unapplied:true, capture_consistency:"stable_non_atomic", snapshot_atomic:false, authorization:false, validation_hints_trust:"untrusted_inert", relations:{changed_files:ImpactCoverageState, symbols:ImpactCoverageState, callers:ImpactCoverageState, imports:ImpactCoverageState, tests:ImpactCoverageState}, lower_bound_relations:list[ImpactRelation], statement:"complete_for_evaluated_repository_local_relations"|"lower_bound_due_to_gaps"}` where `ImpactCoverageState = "complete"|"lower_bound"|"not_applicable"`. Type-valid omitted/`None`/MCP null head selects worktree; non-null string selects commit. Schema/type violations are owned before the service: CLI standard parser error, core programmer-misuse typed exception, and TG-owned direct/stdio MCP validation raises fixed protocol `-32602 invalid graph arguments` before FastMCP dispatch; they never construct ImpactTrust. Worktree phase milestones exactly match GraphCoverage, including ignore-start before root untracked-selection policy evaluation and successful completion for an empty selected set, and preserve partial progress; commit mode keeps all phase booleans false. `ignored_content_unread` is always true. Complete requires no lower-bound relation, empty lower-bound list, and the complete statement; any invoked relation without an exhaustive signal is lower-bound. Commit mode reads verified objects only. Omitted-head mode uses the immutable captured base/index/worktree providers; names may be observed but ignored/admin content never opens; external config/excludes and attribute transforms remain outside coverage. Stable capture is non-atomic across files.
- `ImpactOmissionsV1 = {changed_file_events:OmissionCountV1, changed_symbol_events:OmissionCountV1, affected_files:OmissionCountV1, affected_tests:OmissionCountV1, validation_hints:OmissionCountV1, gaps:OmissionCountV1}`. `ImpactLimitsV1 = {max_root_commits:256, max_visited_entries:20000, max_config_bytes:1048576, max_control_bytes:4096, max_loose_refs:10000, max_ref_bytes:4096, max_ref_bytes_total:4194304, max_packed_refs_bytes:8388608, max_index_bytes:16777216, max_metadata_files:10256, max_metadata_bytes:67108864, max_metadata_read_bytes:134217728, max_pack_indexes:128, max_pack_index_bytes:33554432, max_pack_bytes_per_file:268435456, max_pack_verification_bytes:536870912, max_object_physical_bytes:67108864, max_object_logical_bytes:134217728, pack_read_chunk_bytes:65536, max_ref_hops:8, max_delta_depth:64, max_tracked_paths:5000, max_path_bytes:32768, max_ignore_files:64, max_ignore_patterns:10000, max_ignore_bytes:2097152, max_ignore_match_operations:10000000, max_changed_file_events:256, max_changed_symbol_events:1024, max_file_bytes:2097152, max_file_lines:100000, max_input_bytes:33554432, max_input_read_bytes:67108864, max_live_buffer_bytes:67108864, max_parser_nodes:2000000, max_diff_operations:10000000, max_output_bytes:8388608, deadline_seconds:float}`. Changed limits and omission counts are phase-event record units across all phases, never unique paths; 129 paths with staged+unstaged changes consume 258 file-event units. The object ledgers have the same cumulative no-refund meaning as GraphLimits. `ImpactIncompleteReason` is ordered `invalid_input,not_git,invalid_ref,path_domain_mismatch,unsafe_path,repository_format_unsupported,unsupported_change,grammar_unavailable,parse_error,resolution_gap,changed_file_limit,changed_symbol_limit,input_limit,lazy_fetch_refused,output_limit,deadline,analysis_failed`.

All repository paths are strict UTF-8, at most 32,768 encoded bytes, forward-slash, NFC, relative values with no empty/`.`/`..`, absolute, drive, UNC, device, ADS, or NUL segments. All complete records are tuple-sorted; changed events sort by phase declaration order, path, old path, and ID. Only set-valued `change_types`, `sources`, lower-bound relation lists, and root OIDs are canonical-deduplicated; duplicate node/edge/gap/changed-event/affected/hint records or normalization collisions are incomplete errors. Distinct changed events at the same path are valid only across distinct phases. Strict duplicate-key decoding and exact cross-field validation precede serialization.

Analysis-cap truncation is referentially closed. Projection retains repository metadata and its repository node, then sorted file/test nodes, then symbols whose parent remains, then only edges whose endpoints remain. Impact retains repository metadata (it has no node list), the first `max_changed_file_events` phase-sorted file events, then the first `max_changed_symbol_events` phase-sorted symbols whose `file_event_id` remains. It filters each affected/hint source list to retained event IDs, retains the record iff at least one source remains, and drops/counts it in its affected/hint omission field only when no source remains; removed sources have no separate omission counter but force the applicable relations and whole result lower-bound with the fixed event-limit reason. A materialized population gives exact event/record omission counts; traversal/parser/deadline stops before the population is known use `{count:null,exact:false}`. Projection pre-identity errors have null repository and no nodes; after identity every projection arm retains exactly one repository node. Projection final-wire rebuilding clears every non-repository node and every edge. Impact final-wire rebuilding clears every evidence list and downgrades trust: all previously applicable relations become lower-bound, `lower_bound_relations` is their declaration-ordered list, `level="lower_bound"`, and statement `lower_bound_due_to_gaps`; not-applicable relations remain not-applicable. Both rebuilders add known pre-clear populations only to previously exact omissions (otherwise preserve unknown), preserve repository/revision/diff/coverage/limits/path-domain/error, add one fixed `output_limit` gap when it fits, and can never return complete. Tests assert every retained ID resolves.

The exhaustive trust/exit table is: empty commit diff under commit coverage or no staged/unstaged/untracked event across the complete repository-local three-snapshot comparison → all relation states `not_applicable`, complete trust, exit 0; nonempty fully classified transitions with every invoked relation explicitly exhaustive → applicable states complete, exit 0; worktree-only disclosures remain explicit and the non-atomic capture disclosure is always present; any invoked non-exhaustive relation or gap/cap/unsafe/unsupported condition—including final-wire output limiting—makes every affected/applicable state lower-bound, sets the ordered lower-bound list/statement consistently, marks incomplete, and exits 2; input/ref/repository/path-domain/analysis error → lower-bound full error envelope, exit 2. Exit 1 is unused.

### Task 14T: land tracker and independent-oracle truth

Create a non-releasing PR from the published Task 13 base. It adds the two build rows as `READY`, the five research rows as `DEMAND_GATED`, and exact closed-world ownership/status/trigger assertions. It also commits the frozen schema description, tiny hand-authored graph/impact gold manifests, counterexamples, and an oracle-side validator/scorer that has no import from production graph builders. The scorer must PASS known-good artifacts and FAIL empty, reversed-edge, dangling-ID, dropped-caller, inserted-decoy, wrong-trust, wrong-omission, and wrong-exit counterexamples.

Before any candidate output exists, Task 14T also lands `tests/eval/graph_control.py`, a test-only candidate-independent control harness. It freezes the exact merged/published Task-13 commit SHA plus SHA-256 digests of every baseline source file imported by the existing symbol-impact entry point. At setup it verifies those Git objects locally, creates a detached isolated baseline source tree from that exact commit, rejects any digest/import escape to the candidate checkout, and invokes the baseline service only in an isolated subprocess whose import root is the pinned artifact. A mutation selecting current `repo_map.py`, changing the pinned SHA/digest, or importing candidate production must fail before scoring.

From immutable hand-gold or historical manifests the harness materializes exact verified after-provider roots in isolated test temp directories—explicit head H, index B for staged events, and captured worktree C for unstaged/untracked events—then invokes the pinned Task-13 path-reading symbol-impact service only against the matching materialized root. It emits a canonical `(path,mode,sha256-or-gitlink)` input manifest; candidate tests must prove their provider manifest is byte-identical before scoring. Known-good/known-wrong REDs put the only caller/test in B for A/B/A and different B-only/C-only populations in A/B/C, proving a live A/C rescan, phase substitution, or candidate-modified control fails. Materialization, artifact verification, manifest verification, and candidate provider capture are setup and excluded from both timed arms; timing begins only after both immutable input providers exist, so neither arm gets free snapshot preparation. Tool-call counting likewise begins after preparation. This is a bounded test oracle, not production scratch authorization. Task 14T contains no `src/tensor_grep/cli/{code_graph,change_impact,bounded_git,graph_fs,graph_contract}.py`, no command/MCP registration, and no contract version change. Independently review, merge, and reverify its tracker/oracle tests before opening 14A.

The tracker test pins two lifecycle paths. A successful candidate is `READY` on the Task 14T base, `IN_FLIGHT` in its merged implementation PR, then `SHIPPED` only in a later merged-artifact closure PR. A failed candidate's `IN_FLIGHT` commit remains only in its closed unmerged PR; exactly `CODE-GRAPH-PROJECTION` and `CHANGE-IMPACT` may use a retirement PR that records that closed PR/value receipt and moves canonical main directly from `READY` to `RETIRED`. No other row receives this exception.

### Task 14F: build the candidate-local hardened foundation first

This is the first commit sequence inside each candidate branch, before a public shell or consumer. A behaviorless import/header RED may create the three module seams, but it authorizes no parser, storage, cap, or safety behavior. Open the draft candidate PR, immediately move its one canonical row to `IN_FLIGHT` with that real PR number, then run the following ordered microcycles: (1) schema/key/cross-field/canonicalization and fixed-error vectors; (2) platform root handles, ownership, main-worktree confinement, and beneath/no-link/no-mount primitives; (3) stable control capture and the complete config apply/ignore/refuse/duplicate/transition matrix; (4) REF grammar and stable loose/packed-ref capture; (5) index v2/v3 header/entries/stages/flags/extensions/caps plus explicit v4 refusal; (6) loose objects and OID verification; (7) pack index/pack/trailer/CRC/delta/integrity plus cumulative physical/logical object caps for both formats and the compat transition; (8) anchored census→hierarchical stable-ignore→selected-content capture, non-backtracking ignore budget, ignored-content unopened canary, and stable-non-atomic/raw-coverage disclosures; and (9) the shared metadata/object/logical+physical-input/memory/operation/deadline ledger plus zero-child/scratch/mutation behavior. Before implementing each named arm or boundary, add its independent vector/fixture, run it against the current branch, and require RED for that intended missing semantic—not import, shell sentinel, header, unrelated range, or an earlier cap. Implement only enough to green that arm, rerun every prior foundation test, and only then introduce the next RED. Cap−1/cap/cap+1, many-small-object/repeated-delta, accepted/refused config/ref/index/storage, stage/flag, swap, restored-metadata hybrid control/source/ignore, capture-order/privacy, integrity, SHA-1/SHA-256, and ordinary/wrong/absent/duplicate transition-config fixtures all live in these pre-behavior microcycles; each idx-v3 corruption must reach its intended parser arm. Post-green mutations remain an additional falsification layer, never their first execution. If 14A never merges, 14B repeats/carries this entire production foundation from the Task 14T base; it must not cherry-pick or import unmerged 14A code. If 14A merges, 14B begins with characterization plus mutation tests against the landed foundation. Foundation-only tests cannot pass via a behaviorless public shell, and no production foundation is merged by Task 14T merely to share code.

### Task 14A: `CodeGraphProjectionV1`

**Step 1: pin legacy behavior and create independent REDs**

On the merged Task 14T base, complete Task 14F (which already opened the candidate and moved only `CODE-GRAPH-PROJECTION` to `IN_FLIGHT`). Pin representative Python/TypeScript/Rust `build_repo_map` values and exact `tg map --json` stdout/exit/help bytes and run those controls green. Pin the inherited-parameter matrix before graph dispatch: omitted versus command-line occurrences for both file-cap options and deadline; the parser-default 512 versus graph-effective 5,000 distinction; no-deadline; lexical/type and inherited-below-min Click failures; and post-parse `0.1`, `300`, nonfinite, and above-300 behavior. Then run only the `--format graph-v1` registration RED. Add a behaviorless full-envelope format shell and make registration/help/legacy controls green. Only then add, observe for its intended reason, and green exactly one parameter-source/semantic RED at a time for graph post-parse validation, node types, endpoint direction, coverage, identity, order, privacy, duplicates, referential closure, nested-link/swap refusal, limits, zero mutation, and native forwarding; no semantic RED may cite unknown-format or the shell sentinel. Legacy pre-dispatch Click failures remain byte-identical; only errors that reach the graph branch use its full envelope.

Before production behavior, rerun Task 14T's already-merged hand-gold/counterexample oracle tests unchanged. Candidate tests may add fixtures but must not rewrite gold labels after observing production output; any necessary oracle correction requires its own reviewed non-releasing amendment before candidate scoring resumes.

**Step 2: implement a pure rebuildable projection**

Project from one captured pure/no-persist repo-map result; do not rescan per relation and do not create a graph database. V1 emits only exact `contains` and `defines` edges under the frozen node/endpoint/ID matrices. It leaves imports/validates/calls/references `query_only|unsupported` unless a future reviewed contract supplies an already-materialized receipt; it never resolves them opportunistically or by name equality. Every grammar/parse/resource condition becomes fixed coverage/gap evidence. Tests rebuild three times under randomized insertion order and across moved clones with the same history, and require byte-identical IDs/normalized JSON; separate histories containing identical paths must have distinct IDs.

With no `--format`, legacy `tg map` bytes and every legacy flag remain identical. `--format graph-v1` is valid only with `--json`. It obtains Click/Typer `ParameterSource` for inherited options: omitted deadline becomes exactly 60 seconds; a syntactically parsed explicit deadline must be finite and `[0.1,300]`; `--no-deadline`, any command-line `--max-files`, and any command-line `--max-repo-files` occurrence return the full invalid-input envelope/exit 2, while parser defaults are replaced by the frozen 5,000-file graph cap. Lexically invalid deadline text and the inherited Click below-0.1 check fail before dispatch with the pinned standard Click error, for graph and legacy alike; post-parse nonfinite/above-300 or incompatible format/text/cap combinations use the graph envelope. Complete output exits 0. Any gap affecting a declared materialized relation, analysis/cap/deadline failure, unsafe input, or output rebuild exits 2 with `result_incomplete=true`. A relation deliberately declared `query_only`/`unsupported` does not alone make an otherwise honest projection incomplete.

Require `PATH` to equal the confined main-worktree Git top-level; never discover/ascend, follow `.git` indirection, or open metadata outside caller/MCP authority. Traverse from one verified root handle under the frozen platform beneath/no-link/no-mount primitives and inject Event-gated parent/nested link, junction, bind-mount, volume, and component swaps. Thread the one 60-default/300-maximum deadline and every metadata/pack/entry/file/per-file/aggregate-byte/live-buffer/parser-node cap into capture, integrity verification, walk, and parse so cap+1 stops before materialization. Register native forwarding for the existing `map` command's new format option and positional `PATH` in the Python/native path-domain matrix; bridge failure occurs before map construction and returns the exact graph envelope with exit 2.

**Step 3: prove projection boundaries and non-mutation**

Rerun the already-red/greened Task 14F cap−1/cap/cap+1 suite for every checksum-free control/config/ref/packed-refs, stable-double-captured worktree/source/local-ignore file, index, logical+physical metadata and input, semantic-object physical/logical bytes, pack-index, per-pack+aggregate-pack-verification boundary, traversal/history/object entries, files, ignore-matcher operations, delta depth, live buffers, parser nodes, output nodes/edges/gaps, deadline, and actual CLI bytes including newline; candidate-only output/transport boundaries receive their own RED before serialization behavior. Include many-small-object and repeated-delta fixtures so per-object/live-buffer limits cannot mask the cumulative ledger. Python and compiled-native tests pin omitted deadline→60, `0.1/300` accepted, nonfinite/above rejected in the graph envelope, lexical/type and inherited-below-min values rejected by byte-identical Click output, `--no-deadline` rejected, every explicit legacy file-cap spelling rejected, parser-default cap replaced with exactly 5,000, and no-format legacy controls byte-identical. The edge fixture creates 4,001 valid non-repository nodes/contains-or-defines edges, crossing the 4,000-edge cap while remaining below the 5,000-node cap; truncation drops node/edge pairs together. Assert the closure-preserving algorithm, exact counts after known materialization, `{count:null,exact:false}` after early unknowable stops, the projection pre-identity empty-node arm, and the projection post-identity/final-wire exactly-one-repository-node invariant. Use source, absolute-path, user, remote, ref, parser-detail, exception, environment, and credential canaries across every error/timeout/cap transport. Snapshot repository files/Git metadata/index/untracked state, all tensor-grep persistent state, process creation, and temp namespaces before success/incomplete/error/timeout/CLI/native arms and require zero writes, children, or scratch. If the legacy map builder can write, graph-v1 must use a distinct pure mode. Mutation checks remove flag precedence, coverage/gaps, endpoint/nullability rules, qualified-name binding, repository scoping, tuple members/encoding, duplicate refusal, closure, omission exactness, normalization, beneath/no-mount enforcement, stable control/source/ignore capture, pack integrity, cumulative object accounting, bounded Git parsing, non-backtracking ignore/deadline charging, or cap enforcement independently.

The pre-merge projection value gate uses three frozen hand-labeled multi-question tasks covering containment, definition, and unsupported/gap answers. Each arm may build its initial artifact once but may not rescan source afterward. Compare against documented current `tg map --json`: record its baseline before implementation, then require graph-v1 to preserve every answer the legacy artifact gets right, answer 100% of gap-honesty questions, improve total correct answers by at least three across the frozen set, and use no more TG calls. Pre-register 20 paired repetitions on identical snapshots for cold/cold and warm/warm graph-v1/control arms, alternate AB/BA order, include process startup in both or neither, and record median and p95; both candidate statistics must be no worse than 1.5× the same-state legacy map control. If correctness, tool-call, median, or p95 gate fails, close 14A unmerged and land a non-releasing retirement PR that records the closed candidate/value evidence and moves canonical `CODE-GRAPH-PROJECTION` from `READY` to `RETIRED`; do not dogfood or expose `project`.

Only a value-passing 14A receives independent specification and adversarial privacy/path/resource-exhaustion review, merges alone, and is dogfooded from the published wheel/native front door before 14B starts. Run 16/16 accuracy plus deterministic/cap/value matrices in CI/cloud.

### Task 14B: `ChangeImpactV1`

**Step 1: pin existing impact and introduce the public shell**

Start from the merged Task 14T base plus published 14A only if 14A survived; otherwise repeat Task 14F in this branch before any consumer. Task 14F opens this candidate and moves only `CHANGE-IMPACT` to `IN_FLIGHT`. Pin current symbol-level blast/impact results byte-for-byte on base, including a legitimate-ranking pin, same-name decoys, and the 16/16 accuracy gate. Add the JSON-only `change-impact` command through all four CLI registration sites with omitted deadline=60 and explicit finite/non-boolean `[0.1,300]`; it exposes no no-deadline/file-cap override. The service head input is exactly `None|string`; CLI omission maps to `None`; MCP mapping is frozen later. First run independent registration/help/native-routing REDs, then add a behaviorless full-envelope shell. Before each production arm, add and observe one independent semantic RED for exact REF accepted/refused/ambiguity forms; vanilla init/clone and every config apply/ignore/refuse/duplicate case; index stage/flag/extension/storage cases; changed-file discovery; hunk→symbol mapping; object-backed caller/importer/test/hint recovery; deletions; additions; rename; content/mode/type change representation; explicit-commit versus omitted-head coverage/revision and actual phase booleans on pre-walk/mid-walk/post-walk errors; current-worktree mutation invariance/unopened canary in explicit-head mode; exact `A/A/A`, staged `A/B/B`, unstaged `A/A/C`, restored `A/B/A`, and divergent `A/B/C` base/index/worktree fixtures across service/Python/native plus later MCP, including phase IDs, digests, modes, hunks, event ordering, source resolution, and after-provider-only affected evidence; hierarchical ignore/negation/unopened-content behavior; docs/config-only changes; non-atomic disclosure; non-backtracking ignore-operation/deadline limits; cumulative object and all other caps; determinism; and empty diff. Restore the complete prior suite after minimally greening each behavior before adding the next; Task 14F owns shared foundation REDs, while this step owns only consumer-specific routing/diff/evidence behavior and never counts a previously green foundation fixture as a new RED.

**Step 2: build a safe bounded diff adapter**

Use Task 14F's in-process `BoundedGitReaderV1`; no Git executable, subprocess, temporary directory, or third-party repository API is permitted. V1 accepts only an in-root `.git` directory/main worktree. It stable-double-reads then refuses `.git` file indirection, absolute/`..`/external `commondir`, linked worktrees, UNC/device/network/cross-volume metadata, and any metadata root outside `PATH`/MCP `_mcp_root` before opening the referenced target. Linux uses `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS|RESOLVE_NO_XDEV)`; other Unix must prove equivalent handle-relative mount-ID/component enforcement before child-file open; Windows uses `NtCreateFile(RootDirectory=<verified-dir>, FILE_OPEN_REPARSE_POINT)` plus volume identity. An unavailable primitive returns `platform_no_follow_unavailable` with no reconstructed-path fallback. Directory/file chains must be owned by the current user or administrator/root and not writable by other principals.

Duplicate-aware bounded stable config capture uses this closed table after ASCII case-insensitive Git section/key canonicalization; singleton keys differing only in case are duplicates. The repository/hash matrix is exact: repository format 0 requires absent object/compat extensions and means SHA-1; format 1 permits absent/`sha1` object format with absent compat, `sha256` with absent compat, or `sha256` plus `extensions.compatobjectformat=sha1` for the transition format. Compat without SHA-256, any other value, and absent/duplicate/cross-field-invalid transition declarations are refused. Validate/apply `core.bare=false`; absent or exact-root `core.worktree`; and strict booleans for `core.filemode`, `core.symlinks`, `core.ignorecase`, and `core.precomposeunicode`. With filemode false, worktree executable-bit-only drift is suppressed but staged index/tree mode changes remain visible. With symlinks false, an index-mode `120000` path may be a safely opened regular file whose bytes are interpreted as the inert logical link payload; no target is followed. Ignorecase/precompose modes require exact spelling or one unique NFC/case-normalized identity and refuse collisions or ambiguous spelling changes. Parse and bounded-ignore without logging values only `core.logallrefupdates`; non-promisor `remote.<name>.{url,pushurl,fetch,push,mirror,tagopt}`; `branch.<name>.{remote,merge,rebase,description}`; `user.{name,email}`; and `commit.gpgsign|tag.gpgsign`. Refuse include/includeIf; sparse-checkout; autocrlf/eol/attributes/excludes/fsmonitor/untracked-cache transforms; hooks/helpers/filters/credentials/SSH/proxy/url rewrites; alternates/http-alternates; partial-clone/promisor; replace/grafts; reftable/worktreeConfig/refStorage; nonempty shallow state; command-valued keys; every other `extensions.*`; and every unlisted key. Because config is data to the in-process parser rather than input to Git, no helper/hook/filter/credential/SSH/proxy/remote/network route exists. Independent vanilla `git init`, normal clone, Windows platform-key, mixed-case duplicate, every table arm, ordinary SHA-1/SHA-256/transition repository, and wrong/absent/duplicate transition-key fixture must RED before that key's production handling.

Implement and mutation-test the frozen storage table independently from the official `gitformat-index`, `gitformat-pack`, and `hash-function-transition` specifications: format 0/1; SHA-1/SHA-256 plus the exact compat combination; exact public `HEAD|full OID|refs/heads/...|refs/tags/...` grammar; stable-captured direct/symbolic refs and packed-refs; eight-hop cycle-detected tag/ref peeling; index v2/v3 only; loose zlib objects; pack v2 plus SHA-1/SHA-256 idx v2 and SHA-256 transition idx v3; and bounded commit/tree/blob/tag plus OFS/REF delta decoding. V2/v3 entries use their fixed structure, strict UTF-8/NFC path capped at 32,768 bytes, NUL pathname termination, exact name-length field or `0xFFF` sentinel, and entry-start-relative 8-byte padding; index v4 is refused before any prefix-compressed pathname decoding. V2 requires `CE_EXTENDED` clear. V3 parses the extra word only when `CE_EXTENDED` is set and refuses skip-worktree, intent-to-add, zero/noncanonical extra words, and unknown/reserved extended bits. Both accept only stage 0 and recognized `100644|100755|120000|160000` modes and refuse stages 1/2/3, `CE_VALID`/assume-unchanged, sparse-directory mode, malformed padding/path length/UTF-8/normalization collisions, and unknown/reserved base flags; a non-sparse-index sparse checkout is therefore incomplete, not a set of deletions. Index extensions `TREE|REUC|EOIE|IEOT` are range/length-validated and ignored; `link|sdir|UNTR|FSMN`, sparse-directory entries, and unknown lowercase/mandatory extensions are refused; unknown uppercase optional extensions are range/length-validated then skipped. Add one independent pre-implementation fixture per accepted mode/version/path-framing arm, v4 refusal, named stage/flag/extension arm, non-sparse-index sparse checkout, and unknown optional/mandatory control. Open each metadata/object/pack/index file once handle-relative with beneath/no-link/no-mount regular-file checks and read only through that handle. Missing objects are `lazy_fetch_refused`; unsupported formats/flags/extensions are `unsupported_repository` plus `repository_format_unsupported`; neither is silently ignored.

One request ledger is checked before/after every fixed 64 KiB read/decompress chunk and before every open/allocation/traversal/diff iteration and never resets. Exact caps are the schema values: control/config/loose-ref/packed-ref/index per-file and aggregate bytes/files; 128 pack indexes/32 MiB indexes; 256 MiB per consumed pack/512 MiB streamed verification; 64 MiB actual compressed loose/pack-entry bytes decoded and 128 MiB cumulative decompressed object/delta/diff bytes; 20,000 combined filesystem/history/object entries; 64 delta depth; 32 MiB logical and 64 MiB physical double-captured included source/worktree/local-ignore bytes; 64 MiB live buffers; 2 MiB/100,000 lines per semantic object/source; 5,000 tracked paths; 64 local ignore files/10,000 patterns/2 MiB pattern bytes/10,000,000 matcher transitions; 256 root commits; `max_changed_file_events=256` and `max_changed_symbol_events=1,024` across all phases; 10,000,000 diff/rename operations; and one 60-default/300-maximum deadline. Object/delta re-reads and re-expansions charge again; release/cache eviction never refunds cumulative bytes. Ignore matching is a non-backtracking token/path-character state machine; charge every transition and check the absolute deadline inside its loop rather than only between paths or patterns. Reject known-oversized files from stable size metadata before content; otherwise use cap+1.

Checksum-free worktree capture is hierarchical and staged. Stable-double-capture in-root `.git/info/exclude`. Immediately before the first directory enumeration attempt set census-start; set path-observed after the first returned entry. At repository root, before ignore matching, require the `.git` directory entry to identity-match the already-authorized administrative handle and permanently exclude that identity/path from worktree/source descent under the fixed `git_admin_source_channel:"excluded"` policy; a channel-specific `.git/worktree-secret` canary must never open while required metadata remains readable only through `BoundedGitReaderV1`. Missing or mismatched administrative identity is unsupported/incomplete before any source descent. For each traversed directory, census only immediate capped names/types/identities—never regular-file content—then stable-double-capture that directory's `.gitignore`. Include every supported tracked path independent of ignore matching. Immediately before root untracked-selection policy evaluation, including an empty candidate set, set ignore-start; parse the immutable inherited/current stack, avoid enumeration below ignored directories when reinclusion is impossible, and set ignore-completed only after all selection. Stable-double-capture selected tracked/nonignored-untracked source files, including the empty set, then set untracked-completed. Finally revalidate every census/retained handle before semantic use. Controls/refs/config/packed-refs use their separate two-read metadata channel. Every stable capture uses one still-open handle, pre/post metadata, two identical bounded reads/digests, and charged physical bytes. Instability is incomplete; the protocol prevents per-file hybrids and reports stable-non-atomic cross-file consistency.

Validate REF before filesystem lookup: exactly `HEAD`, the current format's lowercase full OID, or fully qualified ASCII `refs/heads/...|refs/tags/...` under a 1,024-byte, slash-component grammar rejecting empty/`.`/`..`, `.lock`, leading/trailing slash/dot, control/space, the punctuation tilde/caret/colon/question-mark/asterisk/open-bracket/backslash, and `@{`; reject abbreviations, short names, other namespaces, reflog/date/path/revision operators, and option-looking strings. Pin accepted/refused forms and tag/branch ambiguity controls for SHA-1/SHA-256. Resolve only against the stable in-memory ref table, peel at most eight hops, and require every explicit base/head root-commit set to equal current HEAD's set. Traverse verified commit/tree objects, emit add/delete/modify/mode/type without rename inference, then apply the bounded mutual-unique ≥50% rename matcher independently within each phase. With explicit head, expose immutable base/head `path→(mode,verified bytes-or-gitlink)` providers, set `revision.commit_sha` to resolved head with `dirty=false`/null digest, use commit coverage, emit only `commit` events, and keep every worktree phase false. Base+head providers jointly own changed-file/symbol attribution, spans, and rename pairing; the head provider alone owns retained callers/importers/tests/config/policy/hints. With omitted head, freeze base, stage-0 index, and worktree providers once. Emit `staged` events from base→index, `unstaged` events from index→worktree for tracked paths, and `untracked` events from absence→worktree; never collapse them to a base→worktree net diff. Each phase's before/after providers jointly own its attribution and its after-provider alone owns retained affected relations/hints. Base/index and commit comparisons preserve opaque gitlink OIDs. V1 never opens a nested submodule repository, so any index gitlink in omitted-head mode makes its unstaged state unknowable, emits fixed `mapping_unsupported`/`unsupported_change` lower-bound evidence, and prevents a complete result rather than treating directory presence as clean. Run the staged walker, apply frozen mode rules, compute each phase's raw hunks, and flip each worktree-capture phase at its milestone. System/global excludes and attribute transforms remain outside disclosures.

Verify index/pack-index checksums. For every consumed pack, stream-hash the exact pre-trailer bytes through a 64 KiB buffer, require the computed main-format SHA-1/SHA-256 to equal both the pack trailer and index-recorded pack hash, and verify every consumed entry CRC before decompression; transition idx v3 main/compat lookup and trailer domains follow the official tables and are reachable only from the exact transition config. Then range-check offsets/sizes/delta bases/opcodes before allocation, charge compressed and expanded/delta/diff bytes before each chunk/allocation, and recompute every semantic object OID over `type + " " + decimal_size + NUL + content`. Exceeding integrity or object bytes is incomplete. Event-gated tests mutate `.git`/commondir controls, config, loose/packed refs, index, loose object, pack, pack index, object-directory entry, worktree/source parent/leaf, mode, and local ignores between discovery/open/read/use. Local, UNC, `..`, absolute, same-filesystem bind-mount, and cross-volume canaries assert the outside/ignored-content file-open hooks are never reached. Restored-size/mtime and hybrid-valid-ref/worktree/source/ignore mid-read mutations fail double-capture; cross-file changes pin the non-atomic disclosure; CRC, trailer, index-pack-hash, transition-domain, checksum, and OID mismatches each fail independently. Dedicated pre-behavior fixtures cover every config key class and transition cross-field arm; index v2/v3 stage/flag/extension plus v4 refusal; every cap including logical/physical input, semantic-object physical/logical, ignore operations, and deadline; many small compressible objects; repeated-delta expansion; pack/loose and delta/non-delta SHA-1/SHA-256; independently corrupt transition main/compat tables; corrupt/truncated/oversized/unsupported arms; byte-identical `100644↔100755` under filemode true/false; symlinks true/false; case/precompose unique/collision cases; regular↔symlink; regular↔submodule; staged/unstaged variants; no child/scratch; and zero mutation. Post-green mutations then prove each guard is causally load-bearing.

Map hunk ranges to parser-backed spans on both sides: head spans own additions, base spans own deletions, paired intersections own modifications, and rename records bind before/after graph IDs. Rename plus content change carries both ordered types. A file-level edit outside all symbols remains a changed-file fact. A same-file two-function fixture identifies only the intersected function. Unsupported grammar/dynamic import/unreadable content/ambiguous pairing/truncated object or analysis error emits fixed lower-bound evidence and exit 2, never empty-complete callers/tests.

Construct affected files/tests only from those immutable providers. Add provider-accepting pure/no-persist adapters for repository map, parser, resolver/config, test matcher, and validation-policy extraction; interfaces expose bytes/modes and never live paths. Each transition's before/after providers jointly drive changed-symbol attribution. Every retained caller/importer/test relation, resolver/config choice, and hint is after-provider-only: a caller/test present only before is excluded, one present only after is eligible, and a deleted symbol's before identity may seed a lookup but cannot retain a before-only target. In commit mode every byte originates in verified objects; a missing object-backed adapter omits that relation/hint, sets its relation lower-bound, and emits the fixed gap—never current-worktree fallback. In omitted-head mode, staged events use index as after-provider while unstaged/untracked events use the captured worktree; neither is rescanned or substituted for the other. Union affected targets by `(kind,path,reason,provenance)` and canonical-deduplicate their identity-bearing source lists so one target can retain staged and unstaged evidence. Cache identical provider queries. Every relation requires an exhaustive signal or becomes lower-bound. Provider-backed policy strings remain inert display. Preserve exact requested scope/phases/disclosures. Fixtures where caller/importer/test/config/hint populations differ across base/index/worktree pin this policy; arbitrary live worktree mutations leave explicit-head bytes identical and keep the content-open canary untouched. Complete results exit 0 only under the trust table; errors/lower-bound exit 2. Snapshot all repository/TG/process/temp state and require zero writes/children/scratch. MCP proof waits for Task 14C.

**Step 3: falsify accuracy and incremental value**

The fixed local matrix covers Python/TypeScript/Rust; same-file symbol isolation; exact callers and same-name decoys; test recovery; added/deleted/renamed-plus-edited files; executable-bit-only and regular/symlink/submodule type transitions; separate explicit-commit and omitted-head `A/A/A`, `A/B/B`, `A/A/C`, `A/B/A`, and divergent `A/B/C` base/index/worktree arms with exact phase events/digests/modes/hunks/IDs/order/dedup, requested kind/coverage, and actual attempt/observation/start/completion phases; B-only/C-only caller/test/hint controls plus an exact-versus-heuristic same-target disagreement proving heuristic dominance and hint-source union; 256/257 phase-event boundaries, including 129 staged+unstaged paths and an affected/hint record whose mixed-phase sources straddle the cutoff, proving retained-source filtering, empty-record drop, exact event/record omissions, and lower-bound propagation; service/CLI/native/MCP omitted/null/string head truth plus separate core-misuse/MCP-InvalidParams malformed-type controls; explicit-head base+head attribution with head-only caller/importer/test/config/hint populations, and omitted-head base/index/worktree attribution with phase-after-provider-only populations; arbitrary-live-worktree byte invariance and unopened canary in explicit-head mode; hierarchical local-ignore and negation behavior; ignored-content and `.git` source-channel unopened canaries while metadata remains readable; repository-local tracked plus nonignored-untracked scope; explicit global-exclude/attribute-transform noncoverage; projection and impact non-atomic wire disclosures; non-atomic cross-file disclosure; docs/config-only; binary/submodule/nested-link/mount/special; handle/control/config/alternates/ref/index/object/pack/worktree/source/local-ignore swap canaries; restored-metadata hybrid double-capture controls for each checksum-free class; vanilla init/clone plus every config/transition policy arm; every metadata/pack/object/logical+physical-input/ignore-operation cap including many-small-object and repeated-delta cap+1; all named index v2/v3 stages/flags/extensions plus v4 refusal, unknown optional/mandatory, and non-sparse-index sparse checkout; loose/pack/delta SHA-1/SHA-256; independently reached transition-v3 main/compat corruption; CRC/trailer/index-hash/transition-domain mismatches; every supported/unsupported repository-storage arm; exact REF grammar, current-HEAD, ambiguity, tag cycle, and orphan-root cases; empty commit/three-snapshot diffs; randomized order; cap/closure/deadline/error; no-child/no-scratch; and zero mutation. Mutation controls independently remove hunk intersection, base/index/worktree phase separation, phase-aware IDs/sources, conservative confidence/hint merge, event-unit caps/mixed-source filtering, head-kind/phase scoping, object-provider revision policy, untracked inclusion, decoy exclusion, typed-source resolution, rename+modified/mode/type representation, relation trust propagation, beneath/no-link/no-mount confinement, `.git` channel exclusion, staged ignore ordering/unopened-content, stable control/source/ignore capture, config/transition semantics, index version/stage/flag refusal, parser range/checksum/OID/CRC/pack verification, cumulative object accounting, non-backtracking ignore charging, shared resource ledger, safe-reader confinement, closure, impact output-limit trust downgrade, omission exactness, projection/impact disclosure fields, or wire accounting.

The correctness gate requires 100% changed-symbol/direct-caller/required-test recall, zero same-name decoys, every unsupported/truncated case exit 2, three-run byte identity, top-min(10,N) affected-test precision ≥80%, and unchanged 16/16 agent accuracy. Gold never comes from production helpers; scorer known-good/known-wrong tests run first.

Freeze the historical value sample before candidate output exists. Starting from the latest twelve non-merge `fix:` commits before the Task 14 branch whose patches touch parser-supported source and fit the caps, hand-label in reverse chronology while blinded to candidate output. Select the three most recent with legitimate external caller/test impact and the three most recent legitimate leaf/no-external-impact cases; if either stratum has fewer than three, the gate cannot approve until older qualifying commits fill it. Preserve every selected gold label, including changed symbols, callers, tests, decoys, and leaf truth. Compare candidate with Task 14T's fixed manual control: for explicit commit heads, bounded verified raw commit-diff discovery against materialized H; for omitted head, separately enumerate base→index, index→captured-worktree, and untracked transitions, then run one existing symbol-impact call per gold changed-symbol phase event against materialized B for staged or C for unstaged/untracked. Require byte-identical mode/content/gitlink manifests between each candidate provider and its control root before scoring. Candidate must match gold at least as well, correctly return complete leaf cases, and reduce median post-preparation tool calls by ≥25% across the six; otherwise retire before merge.

Pre-register 20 paired repetitions for cold/cold and warm/warm candidate/control runs on manifest-identical prebuilt providers, alternate AB/BA order, exclude provider capture/materialization/verification from both arms, include analysis process startup in both or neither, and record median plus p95. Candidate median and p95 must each be no worse than 1.5× the same-state one-symbol control and must beat the same-state sum of three symbol calls for the three-symbol fixture. These are analysis-only repository-fixture gates, not end-to-end or universal latency claims.

Register positional `PATH` in the Python/native path-domain owner matrix while `--base`/`--head` remain opaque revision strings with lookalike controls. Bridge failure precedes bounded-reader/filesystem dispatch and returns the full impact envelope, lower-bound trust, `path_domain_mismatch`, exit 2, and byte-identical repository state. Add the compiled binary to native smoke with success, empty, invalid-ref, unsupported-repository, incomplete, and WSL treatment/control cases.

Run correctness/value/latency gates in PR CI before merge. If any value gate fails, close 14B unmerged and land only a non-releasing retirement PR that records the closed candidate/value evidence and moves canonical `CHANGE-IMPACT` directly from `READY` to `RETIRED` under Task 14T's exact exception; never publish the CLI or MCP action. Only a passing PR receives independent specification plus adversarial command/path/resource/privacy review, merges alone, is structurally verified on `main`, and is dogfooded from the exact published wheel/native command before 14C.

### Task 14C: fixed-schema MCP graph context and interoperability docs

**Step 1: freeze the survivor branch, then register at most one read-only tool**

After 14A/14B dispositions are merged, status-stamp exactly one branch before any MCP RED: both survive → input action enum `project|change`; projection only → enum `project`; impact only → enum `change`; neither → skip MCP production entirely. In a survivor branch, before touching dependency production files, add separate governance and built-wheel-metadata tests requiring the declared requirement and wheel `Requires-Dist` to equal `mcp==1.28.1`; run both against the current `mcp>=1.27.2,<2` metadata and record their intended mismatch RED. A known-wrong broad-range wheel fixture must continue to fail even if the unlocked resolver happens to choose 1.28.1. Only then change the shipped requirement to exactly `mcp==1.28.1`, update `uv.lock` plus the dependency-governance comment, rebuild, and green the metadata tests. The private handler boundary below is reviewed only for 1.28.1; any MCP upgrade now requires an explicit boundary re-audit instead of entering through the resolver range. Finally, a clean venv installing the rebuilt wheel with no repository lock must resolve/import exactly 1.28.1 and pass legacy plus graph stdio smokes before the graph PR may merge. Neither-survives leaves the existing dependency range untouched because no private graph boundary ships.

Any survivor branch adds one always-on `tg_graph` tool in full and lean with an exact advertised JSON Schema: object; `additionalProperties:false`; required `action,path`; optional `base,head,deadline`; action is the survivor enum; path is string; base/head are string-or-null; deadline is number-or-null; booleans are not numbers. Do not trust FastMCP's generated schema or `ToolManager` validation: MCP 1.28.1 registers `call_tool(validate_input=False)` and its low-level catch converts validation exceptions into canary-bearing `CallToolResult` errors.

Install TG-owned graph validation at all three actual entry layers. First, extend the existing bounded `_stdio_server_accepting_content_length.stdin_reader` before its current `types.JSONRPCMessage.model_validate_json` call. The duplicate-preserving/no-log tokenizer is an iterative single-pass scanner over the already byte-capped raw JSON-RPC frame: maximum nesting 64, maximum 65,536 structural tokens, and maximum 1,024 decoded UTF-8 bytes for a response-eligible string ID. It strictly decodes JSON escapes and Unicode scalars incrementally before comparing every routing/correlation key, method value, and tool name to its fixed semantic value. The exact pairs are `method`/`meth\u006fd`, `tools/call`/`tools\/call`/`tools\u002fcall`, `params`/`par\u0061ms`, `name`/`na\u006de`, `tg_graph`/`tg_\u0067raph`, `arguments`/`argu\u006dents`, and `id`/`\u0069d`; raw and escaped spellings of the same semantic key share one duplicate/ambiguity state. It stores no arbitrary token values, uses a fixed 64-entry context stack, and keeps semantic top-level ID state as `absent | sole-bounded-candidate | duplicate`; raw-plus-escaped same-value and conflicting ID keys both saturate to duplicate in either order. Lone-surrogate, malformed-escape, depth, or token excess sets an error flag but does not stop scanning. After a resource cap it continues a nonallocating saturating lexical/resynchronization pass through the existing 64 MiB byte bound, preserving enough fixed context to resolve semantic `method`, `params.name`, every semantic top-level `id` occurrence, and argument-container kind regardless of whether name/ID appears before or after the capped value; nested keys and quoted raw/escaped `tg_graph`/`id` text remain decoys. After the full scan, cap/error disposition applies only to an exact or ambiguous semantic graph target. A definitively non-graph frame—including a depth-65 or token-65,537 legacy call with escaped semantic method/name—enters the original validation/exception-forwarding path byte-for-byte unchanged. Any unexpected scanner exception that prevents definitive classification is caught at this no-log raw boundary and silently discards with zero writer/model/forwarding calls. Unrecoverable framing before any target can be resolved retains legacy handling only when the scanner itself has neither identified/ambiguously identified graph nor failed internally. Response eligibility is checked first for exact/ambiguous graph targets: the frame must contain exactly one semantic top-level `id` key whose value is either a strict integer in `[-9223372036854775808, 9223372036854775807]` or a string within 1,024 decoded UTF-8 bytes that independently passes strict JSON-escape decoding and Unicode-scalar validation. Integer sign/digit count/range checks occur before bounded conversion inside the caught no-log eligibility gate. Absent, same-value duplicate, conflicting duplicate, malformed-escape/lone-surrogate/overlength string, overrange/unconvertible integer, null, boolean, fractional, object, or array IDs silently discard the graph frame before tokenizer/model/error-writer handling. For an eligible exact/ambiguous graph target, an explicit semantic `arguments` anything other than an ordinary object—including list, string, number, boolean, or null—or non-ID duplicate/Unicode/escape/depth/token error makes the reader send one `JSONRPCError` directly to its existing write stream with that sole safe request ID and exactly `{code:-32602,message:"invalid graph arguments"}`; serialization omits `data`, preserves the decoded semantic string ID, and the frame is never sent to the MCP read stream/Pydantic/session logger. An ineligible graph ID always silently discards even when crossed with a resource cap.

For every identifiable graph frame, the exactly-one-valid-ID gate above precedes every error response and model call. For an eligible tokenizer-valid graph mapping, the same no-log reader boundary invokes MCP 1.28.1's exact `types.JSONRPCMessage.model_validate_json(payload)`, requires a typed `JSONRPCRequest`, then mirrors the session's second step exactly with `types.ClientRequest.model_validate(message.root.model_dump(by_alias=True, mode="json", exclude_none=True))` inside graph-specific tries. Any model/client-request failure becomes the fixed direct error with no exception forwarding. Only the successfully validated `SessionMessage` enters the MCP read stream. A valid-ID graph-shaped frame with the wrong JSON-RPC version independently reaches the first catch; a valid JSON-RPC `tools/call` graph request whose `_meta.progressToken` is a list independently passes the first model and fails the exact `ClientRequest` model; a complete valid graph request passes both and reaches the outer graph handler. Unambiguous legacy frames and legacy notifications keep the existing single downstream validation/exception-forwarding behavior. Frames that fail unrecoverably before any exact/ambiguous graph target can be structurally identified retain legacy protocol handling. The tokenizer and exact downstream checks share the existing 64 MiB frame cap; their MCP-1.28.1 constructors, writer/read-stream seams, request-ID uniqueness/type rules, and target-state rules are structural guards.

Second, a small `TensorGrepFastMCP(FastMCP)` subclass overrides `call_tool` and `list_tools`: direct `call_tool("tg_graph", raw)` invokes the pure mapping validator before `super()`, while `list_tools` copies only the `tg_graph` definition with the frozen schema and leaves every legacy definition byte-identical. Third, installation captures the already-registered low-level `request_handlers[CallToolRequest]` callable and replaces only that entry with an outer wrapper that validates mapped `tg_graph` arguments before invoking the captured FastMCP handler; raising from this outer wrapper reaches the protocol dispatcher instead of FastMCP's catch/stringify block. The installed MCP version, exact raw-reader writer seam, handler key/callability, single-wrap marker, message constructors, and superclass method signatures are structural startup preconditions; an incompatible runtime raises one sanitized initialization error and registers no unguarded graph tool rather than falling back. The same pure `_validate_tg_graph_arguments(raw_mapping)` runs in the subclass, outer request wrapper, and direct-function adapter. It has no coercion, accepts only the exact key set/types/survivor enum, explicitly rejects bool/nonfinite direct numbers, and never includes a value, key, exception, or validation-library detail in errors/logs. Any schema/action/type/finiteness/unknown-field failure raises an MCP protocol error outside FastMCP's catching handler with exactly `{code:-32602,message:"invalid graph arguments"}`, absent `data`, no `CallToolResult`, no graph envelope, and no dispatch. Omitted/null deadline means 60; a type-valid finite number must be `[0.1,300]` or the selected action's semantic invalid-input envelope; no unlimited arm exists. Project semantically requires null refs. Change semantically requires non-null string base under REF grammar; omitted/null head maps to worktree and a string to commit. The graph adapter injects only `mcp_contract_version`. Only a TG-schema-valid request reaches action-specific semantic validation and its full graph envelope. Core service accepts only its typed `None|string` head and raises a pinned programmer-misuse exception before envelope construction for other Python values. No variant accepts generic query, mutation, execution, persistence, remote/multi-root graph, output, workflow, or replay.

For a survivor, write the exact `tools/list` population/version/action-enum/required/additionalProperties RED, add a behaviorless live tool, green registration, then independently add/observe/green the pre-Pydantic raw-stdio tokenizer/JSON-RPC/ClientRequest boundary, subclass/direct boundary, outer request boundary, and each direct-function and real-stdio schema, top-level-equality, confinement, path-domain, nested-link/mount/swap, zero-mutation, cap, deadline, and equality RED. Before boundary production, raw stdio REDs explicitly exercise `method`/`meth\u006fd`, `tools/call`/`tools\/call`/`tools\u002fcall`, `params`/`par\u0061ms`, `name`/`na\u006de`, `tg_graph`/`tg_\u0067raph`, `arguments`/`argu\u006dents`, and `id`/`\u0069d` in capped and uncapped frames. For each key field, a raw-plus-escaped duplicate in both orders is an ambiguity case; for the method/name values, each semantic escaped positive reaches graph classification. Cross every escaped semantic graph target with canary-bearing malformed arguments and require the fixed no-log/no-model/no-forward response. Sole escaped top-level ID-key fixtures cover a valid request reaching both models/dispatch and an early-error request preserving its ID. Raw-plus-escaped top-level ID keys with same and conflicting values, in both orders and capped/uncapped, must saturate duplicate and silently discard with zero response/model/forward/log calls. Also send name both before and after list/string/number/boolean/null `arguments` canaries, duplicate-key graph ambiguities, lone-surrogate canaries in a value, key, and unique string ID, and recoverable malformed-escape canaries including a unique string ID. Run each early-error and wrong-version/model-catch class with valid integer ID `7`, plain string ID `"req-7"`, ordinary escaped string ID `"\u0072eq-7"`, and valid surrogate-pair ID `"\uD83D\uDE00"`. Early-error responses must preserve the decoded semantic IDs (`"req-7"` and the scalar emoji), and valid escaped-ID requests must pass both models and dispatch. A separate valid-ID/wrong JSON-RPC version must reach the first model; a valid JSON-RPC graph request with `_meta:{"progressToken":[]}` must reach only the second failing model; and one fully valid graph request must pass both then reach the outer handler. Enumerate absent, null, boolean, fractional, object, array, malformed-escape/lone-surrogate string, string ID decoded to 1,023/1,024/1,025 UTF-8 bytes, each exact signed-64-bit overflow (`-9223372036854775809`, `9223372036854775808`), an exact 4,301-digit raw numeric ID, same-value duplicate, and conflicting duplicate top-level IDs as silent-discard REDs; accept both signed-64-bit endpoints and the first two string sizes. A converter-call seam asserts the 4,301-digit fixture reaches zero conversion calls, responses, logs, model calls, or forwarding and raises no uncaught exception. Add independent depth 63/64/65 and structural-token 65,535/65,536/65,537 fixtures; cap−1/cap valid graph frames pass both models and dispatch, while cap+1 sets the fixed graph error state. Place every raw/escaped method key/value and the sole raw/escaped ID key independently before and after each capped value, cross cap+1 with eligible integer/string IDs for exactly one fixed response, and cross it with absent/bool/raw-plus-escaped-duplicate/overlength IDs for silent discard; every graph cap+1 arm has zero model/forward/log calls and no uncaught exception. Separately send valid legacy `tg_prepare` depth-65 and token-65,537 calls using escaped semantic method key/value and name before/after the capped value, with nested-key plus quoted-string raw/escaped graph/ID decoys on both sides; assert the exact original downstream model/call/output behavior. Cross absent, boolean, both malformed-string cases, each numeric overflow side, the 4,301-digit case, and both duplicate cases with a separate lone-surrogate tokenizer error; cross one malformed string, one numeric overflow, the 4,301-digit case, and one duplicate case with wrong JSON-RPC version to prove ID gating wins. Mapping/direct REDs send unknown/retired actions, extra canary-named fields, canary-bearing object/array/wrong-scalar field values, booleans/nonfinite direct numbers, missing required fields, and unknown fields. Each must currently demonstrate its unsafe/logging/forwarding behavior for the intended missing layer. After the boundaries land, tokenizer-error fixtures with eligible IDs assert zero downstream model calls plus one fixed response; the wrong-version fixture asserts one caught JSON-RPC model call/zero client calls; the invalid-progress-token fixture asserts one JSON-RPC plus one caught client-model call; the valid fixture asserts both model calls plus outer dispatch; ineligible-ID fixtures assert no stdout response, log, model call, or forwarding even with crossed tokenizer/model errors. Eligible-ID failures yield one protocol error with code/message only and no `result`, `CallToolResult`, `data`, canary in stdout/stderr/log capture, MCP read-stream forwarding, graph service call, repository read, or process/temp/persistent mutation. Separate field-local mutations switch only one of method-key, method-value, params-key, name-key, name-value, arguments-key, or ID-key comparison from decoded semantics to raw spelling; fail to collapse only one field's raw/escaped duplicate aliases; replace the iterative scanner with recursion; retain ID occurrences; allocate arbitrary token values; remove fixed context/depth/token/string caps; stop at cap/error before later target/ID resolution; globally discard capped legacy frames; promote a nested/quoted post-cap decoy to graph target/ID; let scanner exceptions escape; remove recoverable-error continuation, target-state retention, Unicode/escape rejection, JSON-RPC catch, alias-preserving JSON dump, ClientRequest type/catch, ID uniqueness/type/escape/scalar/range gating; move numeric conversion before lexical length/range checks; reject all escapes; reject all surrogate code units; remove the raw error writer, subclass validator, outer request wrapper, exact list schema, single-wrap guard, or structural compatibility check one at a time. Each matching call-count/output/startup control must fail. Controls prove ordinary/invalid legacy frames and notifications in both field orders, legacy tool list/calls, initialization, and server availability are byte-identical. Direct and stdio tests pin deadline omitted/null→60; `0.1/300`; below/above semantic envelopes; TG-owned boolean/wrong-type/nonfinite InvalidParams; and no unlimited arm. Change tests pin type-valid omitted/null/string head across service/CLI/native/direct MCP/stdio plus pre-/mid-/post-walk phases and the `A/A/A`, `A/B/B`, `A/A/C`, `A/B/A`, and `A/B/C` event sets; malformed service values pin the typed misuse exception, while malformed MCP JSON pins the TG boundaries and intentionally has no ImpactTrust. Translate/confine path beneath `_mcp_root()`, resolve only an in-root main-worktree Git directory, re-confine it, and require equality; prohibit ascent/linked/sibling access. The walker refuses nested links/mounts/swaps and hard-excludes the authorized `.git` identity from its source channel. Refs are opaque until bounded REF validation. Same-fixture service/Python/native/MCP values match after removing only MCP version.

Task 13 leaves MCP `1.10.0` and full/lean `59/13`. Either one- or two-action survivor branch bumps every pin to `1.11.0` and `60/14`; action count does not change tool count. Neither-survives stays exactly `1.10.0`/`59/13`. For a survivor, measure actual final tool string after version injection at 8,388,608 bytes; cap−1/cap/cap+1 proves closure-preserving rebuild. Real stdio lists/invokes every surviving action in full/lean and proves legacy tools remain callable both in the locked real venv and the clean unlocked-wheel venv. Mutation checks remove the exact distribution pin, wheel-metadata assertion, runtime version/structure guard, registration, survivor-enum refusal, top-level equality, nested confinement, read-only enforcement, version/path-domain injection, zero-mutation, or cap accounting independently.

**Step 2: document the boundary and research decisions**

`docs/research/graph_coding.md` records the dated Exa primary sources, distinguishes code/workflow/causal graphs, lists concrete current user-demand issues, and labels popularity/vendor claims as observations rather than consensus. `docs/guides/graph_runtime_interop.md` provides non-executing examples only for surviving actions and maps them alongside `prepare`, edit-ready/verify-edit, evidence receipts, and ledger claims. It states that V1 intentionally supports only in-root main worktrees (linked-worktree/commondir/reftable/shallow/alternate/promisor cases return incomplete rather than widening read authority), identity is cooperative, evidence authenticity needs an external trusted key, graph output and validation hints are untrusted evidence with `authorization=false`, ignored paths are outside impact coverage, and tensor-grep neither executes hints nor schedules/replays/forks workflows.

Bank explicit demand-gated research rows for shortest A→B symbol trace, cross-repository graph, workflow/event graph execution, program-dependence graph, and graph mutation. Each row has owner, reason, and reopen trigger. Shortest trace requires trustworthy repository-wide call-edge materialization plus three real debugging tasks; cross-repo follows the federated-root evidence; PDG requires a measured quality gain that justifies its edge/index cost; execution/replay/mutation require separate threat models and reviewed plans.

**Step 3: gate, publish, and close**

For any survivor this touches MCP/path handling, so obtain fresh adversarial and specification/TDD `SHIP` reviews and post them. Run focused local checks and full Ruff/format/mypy/pytest/Cargo/native/WSL/MCP/accuracy/value/determinism/cap matrices in CI/cloud, including built-wheel metadata and a clean unlocked install resolving exactly MCP 1.28.1. Merge one survivor-aware PR through an open publish gate, reverify `main`, and dogfood every surviving action—not a retired one—from the exact published wheel in full/lean; record the installed MCP version and repeat the invalid-canary protocol proof. If neither survives, land research/docs/tracker retirement without contract bump, dependency pin change, or release feature.

After 14C (or neither-survives docs) lands, use a separate non-releasing closure PR. A value-passing shipped row records implementation/MCP/merged/published receipts; a failed row was already retired before merge and records the closed unmerged PR plus evidence. Preserve ordered PR lists, final shipped implementation attribution, closure PR, SHAs, CI runs, versions, and raw proof locations without inventing a published receipt for retired code.

## Task 15: close, retire, or escalate remaining known items

**Files:**

- Modify: `docs/TASK_BOARD.md`
- Modify: `docs/BACKLOG.md`
- Modify: `docs/SESSION_HANDOFF.md`
- Create/update: decision records under `docs/investigations/`

Record:

- DD-004 raw `RuntimeError`: retain `DEMAND_GATED` in this campaign. Task 5 may document a stable typed boundary and a future TDD trigger, but Task 15 does not authorize conditional production work without its own reviewed code/test plan.
- DD-006 daemon semaphore: retain demand gate until measured concurrent load/DoS evidence exists.
- F10 MaxSim: retain `DEMAND_GATED`. Perform only a caller/config/public-contract census and write a future activation-or-removal trigger/decision record; Task 15 does not authorize production removal without a separate reviewed TDD plan, compatibility check, PR lifecycle, and receipts.
- C++ macro structural limitation: retain explicit limitation unless Task 10E obtains a preprocessor-aware oracle.
- #255 many-pattern dedup: preserve the guard and prepare a CEO decision record with a minimal parity fix experiment; do not spend GPU/cloud or promote native routing without approval.
- `RUST-REPLACE-SYMLINK`: retain `DEMAND_GATED`; document that the public Rust direct-file path currently follows a leaf symlink, identify compatibility/security consumers, and require a separate reviewed no-follow TDD/API plan before changing it. Owner: Task 15 disposition steward. Reopen trigger: a concrete untrusted-destination threat model or downstream compatibility decision.
- `GRAPH-TRACE`: retain `DEMAND_GATED`; owner is the relation-registry steward, and the trigger is trustworthy materialized repository-wide call edges plus three real A→B debugging tasks.
- `GRAPH-CROSS-REPO`: retain `DEMAND_GATED`; owner is the workspace-prepare steward, and the trigger is three real federated repositories where single-root change impact misses required evidence.
- `GRAPH-WORKFLOW-RUNTIME`: retain `DEMAND_GATED`; owner is the agent-context steward, and the trigger is a separately approved orchestration/event/authentication threat model plus evidence that external runtimes cannot consume the read-only contracts.
- `GRAPH-PDG`: retain `DEMAND_GATED`; owner is the retrieval-evaluation steward, and the trigger is a bounded experiment demonstrating a material quality gain worth the measured edge/index/latency cost.
- `GRAPH-MUTATION`: retain `DEMAND_GATED`; owner is the edit-verification steward, and the trigger is a separately reviewed mutation API with authorization, rollback, and verification contracts.
- #48, #72, #77, #131/#169: update evidence and decision prerequisites only.

No deferred entry may use “later” without an owner, trigger, and reason.

## Task 16: final independent audit, merge drain, and published-artifact dogfood

**Files:**

- Modify: `docs/TASK_BOARD.md`
- Modify: `docs/BACKLOG.md`
- Modify: `docs/SESSION_HANDOFF.md`
- Modify: `MEMORY.md`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md` if a new durable process law was learned
- Modify: the relevant repository/global skill and agent guidance when a verified lesson changes future orchestration; add a focused regression test or sync check when the guidance is machine-readable
- Modify: `tests/unit/test_backlog_tracker_truth.py`
- Modify: `docs/audits/2026-08-01-backlog-verification-receipts.md`
- Create: `docs/audits/2026-08-02-backlog-closeout-final.md`
- Create: `docs/audits/2026-08-02-backlog-closeout-dogfood.md`

**Step 1: final plan-to-diff audit**

Dispatch fresh Codex reviewers for:

- complete specification traceability;
- adversarial security;
- tests and regression coverage;
- public/API compatibility;
- documentation and tracker truth.

Fix every finding and repeat until all return `SHIP`. Attach every verdict to its PR artifact.

**Step 2: full CI gates**

CI must run:

```text
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
cargo test / cargo check matrices
native-front-door smoke
agent accuracy and retrieval-quality gates when affected
```

Do not run CPU-heavy full matrices locally.

**Step 3: drain one release at a time**

For each implementation or closure PR, wait for newest main CI `completed`; if it released, wait until PyPI serves the version. Merge through GitHub with squash and branch deletion. A closure PR must pass its own CI, merge under this same gate, then be followed by `git fetch origin main` and an exact merged-closure-SHA rerun of `test_backlog_tracker_truth.py`. After every code merge, structurally verify the intended code and rerun the critical fixture against `main`.

**Step 4: published-artifact verdict table**

Create three explicitly attributed families: `published-wheel`, `published-installer`/`release-asset`, and `merged-source-ci`. For wheel-visible contracts, use a clean temporary environment, derive the published version from PyPI, run that exact wheel, and record raw JSON plus PASS/FAIL:

```powershell
$tgPublishedVersion = (Invoke-RestMethod -Uri "https://pypi.org/pypi/tensor-grep/json").info.version
uvx --from "tensor-grep@$tgPublishedVersion" tg --version
```

- MCP `full` and `lean` surfaces with exact contract progression `1.8.0` WSL routes, `1.9.0` tool-surface disclosure, `1.10.0` workspace tool, and—only if at least one graph candidate survived—built-wheel `Requires-Dist: mcp==1.28.1`, clean unlocked resolution to 1.28.1, contract `1.11.0`, and the exact surviving `tg_graph` action enum;
- user-visible writer/symlink refusal behavior exposed through shipped commands;
- `verify-edit` PASS, exact WARN, BLOCK, and INCOMPLETE through both Python and the compiled native front door;
- separate `verify-edit` rows for baseline digest match plus one-byte/schema-valid digest mismatch, executable-mode mutation, `MM` staged-index mutation, unmerged index, nested untracked mutation, assume-unchanged/skip-worktree refusal, 5 MiB baseline boundary, Windows leaf reparse refusal, Windows parent-junction refusal, and opened-handle swap refusal;
- redirected Python/native/evidence-ingestion rows at verification-result final-wire cap−1/cap/cap+1, proving newline-inclusive `result_byte_limit` fallback compatibility;
- separate signed and keyless rows for correctly generated receipts with missing/contradictory trust disclosures and duplicate nested JSON keys, plus valid legacy/component controls;
- production `tg evidence emit --edit-verification` keyless/signed/coexistence/malformed/legacy rows through Python and compiled-native front doors;
- real `verify-edit --json` → captured-stdout → `evidence emit --edit-verification -` signed and keyless round-trip rows through Python and compiled-native front doors: PASS 0→0, WARN 1→0, BLOCK 1→0, digest-valid `result_byte_limit` INCOMPLETE 2→0, and malformed/null/invalid-digest consumer 2/no receipt, each with producer and consumer exits recorded separately and no repository result file;
- evidence cross-repo, post-result revision/dirty drift, and Event-gated pre-builder subject-mutation refusal rows in signed and keyless modes;
- separate signed and keyless rows proving an older result-producing `verifier_version` is preserved verbatim by a newer evidence emitter;
- separate signed and keyless rows proving `verification_result_sha256` binds the exact verification result, including a one-field result mutation/digest-disconnect refusal;
- `edit-ready` named success, anonymous refusal, same-ID overlap refusal, and native routing;
- exact Python/native `verify-edit` and `edit-ready` help/argv parity, path normalization, and baseline-request rows;
- separate claims-fence rows for same-root legacy/strict/release exclusion, different-root independence, timed contention, killed-holder crash release with unchanged lease metadata, exact final index contents, and two-process intermediate-directory swaps before fence creation, after lock acquisition, and before handle-relative index publication with no split-brain success or external-tree change;
- separate baseline no-clobber rows for sequential/concurrent same-NAME, pre-existing leaf/reparse, Unix parent swap, mandatory Windows parent-junction swap, and exact loser-claim rollback;
- each language's in-file parser-backed positive/decoy/grammar-missing triplet;
- each Task 11 language's separate cross-file resolved/decoy/unresolved triplet;
- each Task 11 language's bounded configuration-reader absolute/`..`/symlink-junction escape, malformed, per-file/aggregate/count boundary, and Event-gated leaf/parent identity-swap rows proving outside targets were not read;
- `workspace-prepare` CLI 1/2/8-root completeness, invalid-root exit 2, output-cap exit 2, and compiled-native routing;
- separate same-fixture service/Python CLI/native CLI/MCP value-equality rows for success and partial results;
- per-transport final-wire cap−1/cap/cap+1 rows, including CLI newline and MCP contract-field overhead;
- `tg_workspace_prepare` full/lean `tools/list`, success, confinement error, partial result, and final-wire 8 MiB cap behavior;
- when projection survived: `tg map --format graph-v1 --json` complete/incomplete/pre-revision/final-wire projections through Python/native with deterministic repository-scoped IDs, endpoint/coverage/gap honesty, fixed stable-non-atomic coverage fields, privacy/hardening canaries, zero state mutation, closure/omission proof, and final-wire cap−1/cap/cap+1;
- when impact survived: `tg change-impact` separate empty/nonempty explicit-commit and omitted-head arms with exact revision/requested-kind/coverage and attempt/observation/start/completion phases; direct/stdio TG-boundary malformed-type/extra-field/no-canary InvalidParams; object-backed base+head attribution with head-only evidence invariant under arbitrary current-worktree changes; omitted-head `A/A/A`, `A/B/B`, `A/A/C`, `A/B/A`, and `A/B/C` phase events with index/worktree after-provider evidence; complete Python/TypeScript/Rust edits; added/deleted/renamed-plus-edited/untracked cases; same-file isolation; decoy exclusion; Git-hardening canaries; hierarchical ignore/negation plus ignored-content and `.git` source-channel unopened proof; global-exclude/attribute/non-atomic disclosures; cumulative many-object/repeated-delta caps; invalid ref; lower-bound honesty; inert validation hints; zero mutation; and final-wire cap−1/cap/cap+1 through Python/native;
- when any graph action survived: `tg_graph` full/lean `tools/list`, every exact surviving action, success/lower-bound/confinement/path-domain cases, nested-link/top-level-equality refusal, raw invalid-canary no-data/no-result/no-log protocol errors, legacy coexistence, zero mutation, service/CLI/native equality, and 8 MiB final-tool-string behavior under the clean wheel's exact MCP 1.28.1; retired actions must be absent;

For installer/native-asset-visible WSL contracts, download the exact tagged public `install.ps1` and selected release asset into a clean location, verify and record each URL/SHA-256 before execution, generate the shim from that artifact, and record the shim digest plus selected binary path/version/hash. Exclude source-tree/global installations. Cover fresh install and managed upgrade regeneration. Use mixed reserved/unrelated `WSLENV` flags and record:

- #89 search match exit 0, no-match exit 1, positional roots plus `-f/--file` and `--ignore-file` spelling families, exact `PathDomainEvidenceV1`, and failure exit 2/zero protected-data read/zero downstream child through bootstrap/full/direct-native ownership arms; pre-handle failures have zero bridge child and post-handle failures prove bounded bridge child and descendant kill/reap;
- #90 scan's exact six-match import-rule control, complete JSON/text/SARIF failure contracts, missing-explicit-root exit 2, and zero baseline/suppression output on bridge failure;
- Python/Typer positional-root local/Windows-child `tg run` plus direct-native root/batch/audit/key `tg run`; direct `tg_index_search`; `tg_query(action="index")` path plus `workspace_roots[]`; direct rewrite plan/apply/diff; and `tg_rewrite` plan/apply/diff with path/policy/audit/key fields; disposable-repository failures keep repository/index/policy/key/audit trees byte-identical;
- Task 6 evidence WSL rows for `REPO`, manifest, capsule, cost-json/env fallback, explicit/env signing key, previous, out, and edit-verification file, with stdin/query/model/agent/checkpoint/default-key controls, canonical-alias refusal, signed/keyless success, and no-read/no-sign/no-write failure;
- Tasks 7–8 WSL fields (verify-edit repo/validation; edit-ready repo/validation) with exact path-domain result/ticket schemas and opaque baseline/out/query/agent/repo-relative-scope controls, including absolute-scope rejection;
- Tasks 12–13 WSL anchor/roots through Python/native and full/lean MCP, including target-domain confinement and no-dispatch failures.
- every surviving Task 14 action's WSL `PATH` through Python/native/full/lean MCP, with opaque ref lookalike controls and pre-dispatch/zero-mutation bridge failures; retired actions are absent rather than dogfooded fictitiously.

For non-wheel-visible internal contracts, record separately labeled source-tree CI receipts tied to the exact merged SHA; never attribute them to the wheel:

- canonical tracker parser/version controls, each Task 2 semantic reconciliation node, every successful Tasks 3–14 owning row's real-PR `READY` → merged `IN_FLIGHT` transition, the seven graph rows' unique statuses/owners/triggers, the exact two-ID graph exception whose candidate `IN_FLIGHT` commit remains in a closed unmerged PR and whose main-branch retirement moves `READY` → `RETIRED`, and the separate `SHIPPED`/`RETIRED` closure transitions;
- the complete closed-world typed-path/owner census for Tasks 2A–2C, 6–8, 12–14; every direct/meta/stdio route and MCP workspace/policy/graph field; every opaque control; shared Python/Rust vectors; trusted System32/no-reparse resolution; 16 KiB/256-input/32 KiB/2-second/10-second boundaries; duplicate cache; fixed no-shell argv; concurrent cap+1 pipe draining; invalid UTF-8/device/NT/ADS grammar; Job-Object descendant kill/reap; configured non-`/mnt` automount; absent/partial provenance; exact normal/error key snapshots; unchanged-test production mutations; zero protected target-data reads/writes and zero downstream children on failure, plus the explicit pre-handle zero-bridge versus post-handle bounded-bridge kill/reap taxonomy;
- writer-census historical control, ordinary and generated unsafe-writer mutation controls, aliased-sink controls, each of the three exact production RED/green nodes, and current-tree zero-violation census;
- SHA-1/SHA-256 repository and index-object round trips;
- exact baseline/path/prepare/receipt-component/result/ticket schema key sets, cross-field invariants, exhaustive reason-to-verdict/status/exit partition, and malformed-input envelopes;
- claims `WRITE` publication versus `NO_WRITE` preservation, including absent-index no-state and existing-index/no-match byte/inode/mtime stability;
- Rust `CpuBackend.replace_in_place` exact public-signature assertion plus separate walk, literal-child, and regex-child fault tests;
- canonical `RUST-REPLACE-SYMLINK` decision-record/tracker assertion proving the direct-leaf behavior was retained visibly rather than silently attributed to the shipped `CPU-BACKEND` row;
- Python CPU adapter's separate simple-fixed-inverted and word-regexp-inverted internal-`TypeError` tests plus the zero-retry AST census;
- graph schema exact-key/cross-field validation, stable-ID/order/qualified-name mutation controls, explicit non-materialized relation coverage, in-process Git-format parser/checksum/OID/delta/range/resource-ledger mutations, handle-relative no-follow and local/UNC unopened-canary swaps, no-child/no-scratch proof, output-limit trust downgrade, historical-patch incremental-value gate, 16/16 accuracy non-regression, and repository non-mutation receipts;
- platform-specific Windows opened-handle and claims-fence process tests when the published-wheel environment is not that platform.

Create `docs/audits/2026-08-02-backlog-closeout-dogfood.md` with one row per shipped contract. Wheel rows record the exact published version; installer/release-asset rows record tag, public URL, SHA-256, generated shim digest, and selected binary identity; internal rows record exact merged SHA and CI run. Every row records command/test ID, expected fields/order, expected verdict/status/exit, sanitized committed artifact path, attribution (`published-wheel`, `published-installer`, `release-asset`, or `merged-source-ci`), and PASS/FAIL. Raw machine evidence containing local host/user/distro paths remains in an ignored access-controlled audit location; committed docs use stable canaries/placeholders and their digests. A category summary cannot replace individual rows.

Read raw JSON before scoring and preserve the command's exit code without pipe masking.

**Step 5: close documentation truth**

Update current release/version, shipped receipts, remaining CEO/financial decisions, and all new findings. For every successfully merged program row, perform the separate non-releasing post-merge closure pattern defined for #859: fetch current `origin/main`, rerun its exact merged treatment arm, record implementation PR/merge SHA, preserve the final implementation PR in the canonical PR field, record the closure PR in the trigger/audit, update the row to checked `SHIPPED`, pass closure-PR CI, merge it under Step 3, fetch `origin/main`, and rerun `test_backlog_tracker_truth.py` on the exact merged closure SHA. For either value-retired graph candidate, preserve its closed unmerged candidate PR and value evidence, record no merge SHA/published version/wheel dogfood receipt, merge only the non-releasing retirement/docs PR, and require the canonical row to remain `RETIRED`—never relabel it `SHIPPED`. The final invariant requires zero AI-actionable canonical rows in `READY` or `IN_FLIGHT`; only `BLOCKED`, `CEO_GATED`, `DEMAND_GATED`, `SHIPPED`, and `RETIRED` may remain. The final audit document includes implementation and closure commit SHAs/PRs, CI run IDs, published version/raw dogfood artifacts only for shipped code, closed-PR/value receipts for retired candidates, and any explicitly retained limitations.

Write the dumbed-down CEO update from the closed-world canonical index: what worked, every remaining backlog row grouped by status (not a top-N sample), every research/decision item, and at least five evidence-backed lessons since the preceding CEO update. For each genuinely new durable lesson, synchronize `AGENTS.md`, `CLAUDE.md`, `MEMORY.md`, `docs/SESSION_HANDOFF.md`, the applicable skill, and any agent template/routing guidance; do not edit unrelated global guidance just to satisfy a checklist. Add every newly discovered bug or research trigger to the canonical board and `docs/BACKLOG.md` before declaring closeout.
