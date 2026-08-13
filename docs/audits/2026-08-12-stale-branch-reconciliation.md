# Stale-branch reconciliation + BLOCKED-row premise recheck — 2026-08-12

Read-only reconciliation of the main checkout's stale state, so no future session re-chases
shipped work (campaign: `docs/plans/2026-08-12-backlog-closeout-campaign.md`, rev 4). Every row
is re-derivable with the quoted command. Independently verified by a ground-truth seat and the
codex Sol seat (both re-ran the receipts; codex additionally confirmed "branch reconciliation is
substantially correct" with H6 patch-distinct/content-shipped).

## 1. The checkout `audit/h6-cudf-backend` @ `d9e477b` contains ZERO unlanded product work

Base `bb4fdae` (pre-#968); `origin/main` = `b6dc0a6` (v1.110.14).

| receipt | command | result |
|---|---|---|
| H3 commit upstream | `git cherry origin/main audit/h6-cudf-backend` | `- d9e477b` (patch-id equivalent on main) |
| H6 commits patch-distinct but content SHIPPED | same (`+ f1e888c`, `+ 928e9b2`) + `git grep -n BackendExecutionError origin/main -- src/tensor_grep/backends/cudf_backend.py` | normalization at `:328-336`; `git grep -n "H6 audit" origin/main -- tests/unit/test_cudf_backend.py` → `:699` |
| 6 of 10 dirty code files byte-identical to main | `git diff origin/main --stat -- <file>` | `backend_cpu.rs`, `evidence_signing.py`, `test_ast_wrapper_backend.py`, `test_evidence_signing.py`, `test_native_walk_error_ratchet.py`, `test_release_workflow_configuration.py`: empty diff |
| 4 differing files are BEHIND main (no novel content) | worktree blob identity: `git hash-object <file>` vs `git log origin/main --find-object=<blob>` | `ast_backend.py` blob `356c9a85` and `ast_wrapper_backend.py` blob `46a344e8` are exact historical main blobs (introduced #976, replaced #987); `repo_map.py` only lacks main's #969 H4 additions; `test_cli_modes.py` `+` hunks are all pickaxe-dated pre-#968/#987/#1000 forms; gross 63+/554− vs main, net −491 |
| 11 dirty docs/skill files are stale 2026-08-06-era snapshots | spot-check `docs/SESSION_HANDOFF.md` (worktree says "Last updated: 2026-08-06", narrates pre-merge #968/H5) | superseded by main's 2026-08-11 state; BEHIND, not novel |
| untracked `tests/unit/test_ast_invert_match_fail_closed.py` is an obsolete byte-identical duplicate | `git hash-object` == `git rev-parse origin/main:tests/unit/test_ast_invert_match_fail_closed.py` == `5eaa676c…` (tracked since #976) | safe-delete candidate (PROPOSED; not executed) |

**Disposition:** tree preserved untouched (21 modified + pre-existing untracked entries). Sole
executed mutation: removed the 0-byte `nul` Windows redirect artifact (AGENTS.md-sanctioned,
via Git-Bash `rm -f ./nul`; `Test-Path`/plain `Remove-Item` cannot address the device name).
Remaining cleanup (branch delete after A30 ancestor-proof, `src/.tg_index` 6.9MB regenerable
index, `subdir/` 25-byte probe dir, the duplicate test file, ~44 worktree husks) is PROPOSED
only — none executed without operator ack.

## 2. BLOCKED-row premise recheck (codex F4/M3 fold)

| row | premise re-derived 2026-08-12 | verdict |
|---|---|---|
| #89 | owned by Task 2A→2B typed-path program | ADVANCED this campaign: #966 CONFLICTING→MERGEABLE at `fbe1128`→`a294daa`; first-ever Actions evidence chain opened; still NOT GREEN (Sol SHIP + Windows CI outstanding) |
| #90 | same Task 2A→2B/2C program (scan half) | same as #89; doctor half remains shipped (#571) |
| F5 | Task 8 Steps 3–5 on `rust_core/**` + `tests/e2e/**` → shared-box cargo/e2e ban → CI/cloud | HOLDS (W3 ban unchanged; CI-routed builds remain the path) |
| F6 | **MIXED (A41), corrected this campaign (codex M3):** roadmap ground-truth says NOT purely rust/e2e-blocked — Python/schema/evidence-signing slices (S1 de-block move) are buildable-first; native verify-edit + e2e halves stay CI/cloud-routed | premise CORRECTED; board trigger text to carry both halves (WS3) |
| F8 | Tasks 12–13 on `main.rs` + `path_domain.rs` + e2e routing parity → CI/cloud | HOLDS |
| MCP-SURFACE | Task 4 blocked on Task 2C; live contract version must still be `1.7.0` | HOLDS: `git show origin/main:src/tensor_grep/cli/mcp_server.py \| grep _TG_MCP_SERVER_CONTRACT_VERSION` → `"1.7.0"` |

## 3. Task 2A campaign receipts (WS1, this session)

- Baseline (pre-merge, `8f1fc30`, WSL py3.12 managed venv — system WSL python3.13 stdlib is
  BROKEN: `/usr/lib/python3.13/shutil.py` absent; venv rebuilt `--python-preference only-managed`):
  per-node maps in `.orchestrator/t2a_baseline_8f1fc30/` (local scratch). Suite split:
  54P / 13F-21P-5S / 25F-12P / 2F-13P-12S / 8P / 1F-7P. The #9d receipt's counts did NOT
  reproduce — **ERRATUM-1**: the receipt is a `c550a84`-era snapshot; deltas trace to R2–R5
  commits (`git show --stat 4c2c300 28f50e5 88a9ed1 8f1fc30`).
- Union-merge `3e2fe17` (merge, not rebase — fast-forward-pushable, no force): only
  `cli/main.py` conflicted; union keeps main's H5 timeout contract (run+timeout, 124/2 exits;
  `test_native_delegation_timeout.py` 5/5) AND the branch's A62 emit-after-start (hook fires
  only on started-child arms; documented at the site).
- Post-merge per-node oracle: **158 nodes across 6 suites, 0 outcome deltas**; the single
  wobble (`test_manifest_command_digests_recompute…` 14.18s > 12.0s collect bound) reproduced
  GREEN solo (13.59s) — load jitter on /mnt/c, not a defect. `mypy src/tensor_grep` clean
  (96 files). Branch-touched files preview-formatted (`fbe1128`).
- First-ever CI run on the branch (run `31619898062`, tested head `fbe1128`): smoke FAILED at
  `Build Rust core` — `rust_core/src/main.rs` **unclosed `mod tests` delimiter, introduced at
  `c550a84`** (brace-balance scan: `ac68e62`=0, `c550a84`..`8f1fc30`=1) — the scaffold deleted
  the module's closing brace and no compile oracle existed until now (A87). Fixed + rustfmt'd
  in `a294daa` (balance 0, `rustfmt --check` clean on the 5 branch rust files).
- Live census at R5+ head (codex H1): manifest = **169 nodes (157 python + 12 rust; job split
  test-python 103 / native-build-smoke 66)** — the receipt's "157 (148+9)" is the stale
  c550a84 figure.
- Known next repair (codex H3, pending CI confirmation on `a294daa`): blanket `pytest tests`
  precedes the Task 2A collector in the unioned ci.yml; intended REDs fail the blanket step and
  skip the collector → R1 = deselect owned RED nodes from the blanket run (or reorder) + a
  reachability/ordering ratchet in `test_task2a_ci_wiring_contract.py`.

**Hard stop unchanged:** no #89/#90/Task-2A GREEN claim — Sol exact-byte SHIP + real Windows CI
evidence (head+base+merge-ref SHAs, 169-node census) are still outstanding.

## 4. WS1 repair-round ledger (appended as rounds land; per-round CI per M5')

| round | head | change | trigger receipt |
|---|---|---|---|
| R0 (union) | `3e2fe17`→`fbe1128` | A22 union-merge of `b6dc0a6` + preview-format of branch-touched py; per-node oracle 0-delta | PR #966 CONFLICTING→MERGEABLE |
| R0a | `a294daa` | restore `mod tests` closing brace (unclosed since `c550a84`) + rustfmt 5 branch rust files | run `31619898062` smoke: unclosed delimiter `main.rs:2997` |
| R0b | `8b8272c` | `raw_args: Vec<OsString>` E0282 annotation | run `31620673714` smoke |
| R-lint | `9f6ef47` | import test helpers into `python_sidecar` tests module (11x E0425/E0433 on lib-test target) + ruff sweep (14 errors: RUF100/C420/RUF012/RUF034) | run `31631927863` cuda-check + test-rust-core + Formatting |
| R1 | `419b7d6` | exclude the 4 census-owned suites from blanket `Run Pytest` (collector was unreachable — codex H3) + 3-arm ratchet (ignore-list==manifest bidirectional, collector order/condition, live-collection⇔manifest closed world), RED-proven pre-splice | run `31631927863` test-python shape |

Known non-defect flake: `test_manifest_command_digests_recompute_and_closed_world_nodes` asserts
batched collect <12.0s and measures 13.6–14.2s on the loaded /mnt/c box (passes solo at 13.59s;
bound is CI-sized). Not touched; noted for the Sol round.

## 5. Sol exact-byte audit, round 1 (head `8181762`) — FIX-FIRST, 4/6 repaired

Codex Sol (`gpt-5.6-sol`, read-only, `model_reasoning_effort=high`) returned
`VERDICT: FIX-FIRST — F1,F2,F3,F4,F5,F6` on the union+repair head. Disposition:

| ID | sev | subject | disposition |
|---|---|---|---|
| F1 | HIGH | runner classifies every JUnit `<failure>` as behavioral RED (A61) | **FIXED** `bcf2c06` — `<failure type=…>` must end in `AssertionError` else `crash_or_setup`; mutation control added (`NotImplementedError` → not RED) |
| F2 | HIGH | one valid node can clear the whole manifest | **DEFERRED** to workflow-level receipt aggregation — a verifier-level exact-population match breaks the single-receipt positive control; the durable fix verifies every per-node receipt beneath `current_run_dir` and requires their union == the job's manifest population. Tracked as a remaining RED item |
| F3 | HIGH | blanket excludes Win32 suite on every OS but collector is Windows-only; Linux-only node orphaned | **FIXED** `bcf2c06` — exclusion is now Windows-only (`if: runner.os == 'Windows'`); a non-Windows blanket lane runs the suites in-blanket; the Linux-only node restored with `required_non_skip: false`; ratchet asserts both lanes |
| F4 | HIGH | A67 — public `-f/--file` completes a search before SearchInputLedger admission | **FIXED** `bcf2c06` — `-f/--file` fails closed (exit 2, `search_input_limit`, names the ledger, zero child/matcher starts) until the ledger is installed; the below-cap success node repurposed as the fail-closed control |
| F5 | HIGH | A38 — atomic `write_receipt` lacks parent-handle anchoring | **REMAINS** — deep RED-by-design security item (Event-gated parent-swap + identity-verified parent handle); part of the Task 2A program, not a repair-round fix |
| F6 | MED | collector-condition ratchet used substring membership (`&& false` passes) | **FIXED** `bcf2c06` — exact-equality on `runner.os == 'Windows'` |

Sol's disproven-concerns list independently confirmed the union preserved main's H5 timeout /
exit-124 / spawn-OSError-exit-2 / emit-only-after-start contract, the `_run_native_tg_command`
binary prefix, the ignore-list/order/collection ratchet arms, the bounded `-f` reader, the static
heartbeat payload, and the win32 mypy seam. **Board stays BLOCKED** — the scaffold is RED by
design and F2/F5 remain. Round 2 (re-audit of `8181762`) is the next gate before any GREEN claim.

## 6. ERRATUM-2 (2026-08-12, appended by the session-retention audit; sections 1-5 untouched)

Section 1's row "11 dirty docs/skill files are stale 2026-08-06-era snapshots … BEHIND, not
novel" was WRONG for two of the eleven. The spot-check method (one file — SESSION_HANDOFF.md's
header/date — generalized to all eleven) missed NOVEL, never-committed content:

| dirty file | novel content | proof it never landed |
|---|---|---|
| `AGENTS.md` | `## Session Lessons (2026-08-07, campaign continuation)` + `## CI Cost Discipline (2026-08-07, from a real account-cutoff incident)` | `git log --all -S "CI Cost Discipline" -- AGENTS.md` → zero commits; `git diff 568065a -- AGENTS.md` shows both as `+` sections |
| `docs/SESSION_HANDOFF.md` | `## Session Lessons (2026-08-07, campaign continuation)` (16-item detail block that the AGENTS.md section references) | `git log --all -S "Session Lessons (2026-08-07"` → zero commits |

The remaining nine files' "stale snapshot" classification stands. Disposition: the retention PR
landed all three sections VERBATIM (with provenance notes) BEFORE any cleanup of the dirty tree
executes. The "ZERO unlanded product work" headline remains correct for code/tests; it was never
established for lesson prose, and this erratum is the correction. Class lesson: a spot-check
census of N files is a claim about the ONE file checked (AGENTS.md: "the population is the
defect").
