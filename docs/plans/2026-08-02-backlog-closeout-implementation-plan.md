# Backlog Closeout Campaign Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to execute this plan task-by-task. Every implementation task requires a fresh implementer, specification review, and quality/security review. Use `superpowers:test-driven-development` and `superpowers:verification-before-completion` for every code slice.

**Goal:** close every AI-actionable backlog item and tracker contradiction with evidence, while preserving explicit CEO/financial gates.
**Architecture:** sequential release-safe programs built in isolated worktrees; shared preparation and verification services sit behind thin CLI/MCP adapters; language navigation dispatch becomes registry-driven before adding five extractors.
**Tech stack:** Python 3.11+, Typer, pytest, Ruff, mypy, Rust/Clap native front door, tree-sitter grammars, FastMCP, GitHub Actions, PyPI.
**Design:** `docs/plans/2026-08-02-backlog-closeout-design.md`

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

Each seat returns exactly `APPROVE` or `FIX-FIRST` and, for every finding, `file:line`, a reproduction/counterexample, and the smallest acceptable plan change.

**Step 2: apply every must-fix**

Edit both documents with `apply_patch`. Record the review round in the design's thinktank section. Do not resolve a finding by deleting or weakening its acceptance test.

**Step 3: re-review from a fresh framing**

Repeat all three seats until all return `APPROVE`. A no-verdict seat is failed and is replaced; it is not approval and not a blocker.

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
- Modify if process law changed: `AGENTS.md`
- Test/receipt input: `docs/audits/2026-08-01-backlog-verification-receipts.md`
- Create: `docs/audits/2026-08-02-backlog-reconciliation.md`

**Step 1: write deterministic document-invariant tests**

Add `tests/unit/test_backlog_tracker_truth.py` with assertions that:

- every shipping entry has exactly one parseable PR reference and one status token from the documented closed vocabulary;
- F1/#22 matches `docs/CONTRACTS.md` exit semantics;
- F2 matches `ledger_store.resolve_agent_id`'s documented legacy decision;
- #90/#109/#36/#37 have shipped receipts and are not active;
- #859 is present as an actionable class-level writer-ratchet task;
- every CEO-gated ID is under exactly one CEO-gated heading;
- `SESSION_HANDOFF` current version equals the tracker handoff version.

The test must parse headings and entries rather than assert one raw full-file snapshot. Include positive controls using a minimal valid synthetic document and malformed-heading/duplicate-section negative controls. It must not call GitHub or claim that a static fixture proves a PR is still open.

Run:

```powershell
uv run --no-sync pytest tests/unit/test_backlog_tracker_truth.py -q --timeout=15
```

Expected: fail on the known cross-document status/version and malformed-section invariants.

**Step 2: record one-shot GitHub truth separately from CI**

```powershell
gh pr list --state open --limit 100 --json number,title,isDraft,headRefOid,statusCheckRollup
gh issue list --state open --limit 100 --json number,title,labels,state
gh run list --branch main --workflow ci.yml --limit 3 --json databaseId,status,conclusion,headSha,updatedAt
gh release list --limit 3
```

Copy the raw JSON/text output with timestamp and commands into `docs/audits/2026-08-02-backlog-reconciliation.md` using `apply_patch`. This is a dated reconciliation receipt, not a timeless pytest oracle.

**Step 3: update tracker truth**

Record source/PR/release receipts for every retirement. Preserve historical narrative but clearly mark it historical. Add a re-open trigger for every parked item.

For #89, run only a bounded WSL probe if WSL is available; never restart/shutdown WSL:

```powershell
wsl.exe -e sh -lc 'timeout 30 tg --version && timeout 60 tg search tensor_grep /mnt/c/dev/projects/tensor-grep/src --json >/tmp/tg-89.json; rc=$?; printf "%s\n" "$rc"; python3 - <<"PY"
import json
print(json.load(open("/tmp/tg-89.json"))["result_incomplete"])
PY'
```

If unavailable or non-reproducible, document the raw result and close/reclassify; do not invent a fix.

**Step 4: make the test pass**

```powershell
uv run --no-sync pytest tests/unit/test_backlog_tracker_truth.py -q --timeout=15
uv run --no-sync ruff check tests/unit/test_backlog_tracker_truth.py
uv run --no-sync ruff format --check --preview tests/unit/test_backlog_tracker_truth.py
```

**Step 5: commit as non-release documentation/test work**

```powershell
git add AGENTS.md docs/TASK_BOARD.md docs/BACKLOG.md docs/SESSION_HANDOFF.md docs/audits/2026-08-02-backlog-reconciliation.md tests/unit/test_backlog_tracker_truth.py
git commit -m "test: pin live backlog truth"
```

## Task 3: restore #859's class-level atomic-writer ratchet

**Files:**

- Create: `tests/unit/test_cli_atomic_writer_ratchet.py`
- Modify only if census finds a bypass: the named file under `src/tensor_grep/cli/`
- Modify: `docs/TASK_BOARD.md`
- Modify: `docs/BACKLOG.md`

**Step 1: build the AST detector and historical positive controls**

The test parses Python under `src/tensor_grep/cli/`, resolves imports plus simple assignment aliases, and classifies functions that:

- write directly to a caller-selected destination through `open(..., write-mode)`, `Path.open(..., write-mode)`, `Path.write_text`, or `Path.write_bytes`;
- create/write a temporary file and then publish it through `os.replace`, `os.rename`, `Path.replace`, `Path.rename`, `shutil.move`, `shutil.copy`, `shutil.copyfile`, or `shutil.copy2`;
- call an approved shared atomic helper;
- publish through `replace_with_retry`, including imported/renamed aliases;
- perform a separately sanctioned runtime/directory swap.

Create `tests/fixtures/audits/codemap_pre_859.py` from the actual pre-fix codemap blob that preceded #869 and record its source commit plus SHA-256 in the fixture header. Require the historical `_atomic_write_text` symbol to be classified as a violation while the current fixed `codemap.py` classifies cleanly. Add positive controls for renamed `os.replace`, renamed `shutil.move`, renamed `shutil.copy`/`copy2`, an imported/assignment-aliased `replace_with_retry`, direct writers bound under another name, variable write modes, `io.open`, `os.open`, `Path.open`, `Path.write_text`, `Path.write_bytes`, and tempfile-to-publish flows. Add safe negative controls for writes confined to a caller-created temporary fixture. Build an independently derived candidate inventory from imports/calls and raw sink-name syntax; every candidate must resolve to `sanctioned`, `helper-backed`, or `violating`, and every unresolved call fails the test instead of disappearing from the population.

Pin the complete current population by `module:function:classification`, not by line number.

```powershell
uv run --no-sync pytest tests/unit/test_cli_atomic_writer_ratchet.py -q --timeout=15
```

First add a behaviorless detector shell so collection/import is green. Then make each positive/negative control red independently; the first required red is the renamed-`os.replace` assertion returning no sink, not an absent module. Implement resolution one sink family at a time. Historical-fixture controls remain green permanently by expecting the known violation; a mutation test injects the same unsafe writer shape into a copy of the current production tree and proves the production census changes from zero violations to nonzero. The final current-tree census must be green with zero unresolved/violating candidates. A failure caused only by a missing expected-population list is not an acceptable red arm.

**Step 2: inspect every reported production site**

Expected direct `os.replace` sites initially include:

- `src/tensor_grep/cli/main.py`
- `src/tensor_grep/cli/lsp_provider_setup.py`
- `src/tensor_grep/cli/_index_lock.py`
- `src/tensor_grep/cli/session_daemon.py::_write_daemon_metadata` via `replace_with_retry`

Classify artifact writers separately from launcher/native-runtime/directory swaps. Do not force runtime swaps through `atomic_write_bytes`.

**Step 3: fix real bypasses, if any**

Route user-facing byte/text/JSON artifact publishing through `src/tensor_grep/cli/_index_lock.py::atomic_write_bytes` or its JSON wrapper. Add a behavior test that pre-existing and dangling symlink destinations are refused before resolution.

**Step 4: verify the class contract**

```powershell
uv run --no-sync pytest tests/unit/test_cli_atomic_writer_ratchet.py tests/unit/test_codemap_write_refuses_symlink.py tests/unit/test_evidence_bundle_atomic_write.py -q --timeout=15
uv run --no-sync ruff check tests/unit/test_cli_atomic_writer_ratchet.py
uv run --no-sync ruff format --check --preview tests/unit/test_cli_atomic_writer_ratchet.py
```

**Step 5: mandatory independent security gate**

The reviewer must attempt a symlink bypass, a dangling-symlink bypass, an existing-directory destination, and a precheck-to-replace race against any changed writer. Verdict must be `SHIP` before merge.

## Task 4: disclose the MCP tool surface and bump contract 1.7.0 → 1.8.0

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

Set `_TG_MCP_SERVER_CONTRACT_VERSION = "1.8.0"` with a history comment. Update exact version pins and docs.

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

## Task 5: retain and harden the public `CPUBackend.replace_in_place` API

**Files:**

- Inspect/possibly modify: `rust_core/src/backend_cpu.rs`
- Inspect/possibly modify: `rust_core/tests/test_replace.rs`
- Modify: `docs/BACKLOG.md`
- Create: `docs/investigations/2026-08-02-replace-in-place-surface.md`

**Step 1: prove the search instrument**

Run exact identifier, public export, FFI, string/dynamic registry, documentation, and test searches. Include a known called sibling method as a positive control. Do not run cold Cargo locally.

```powershell
rg -n -w "replace_in_place" rust_core src tests docs
rg -n "replace_in_place|PyO3|pymethods|pub use|extern.*C|match.*replace" rust_core/src src
```

**Step 2: write red public-API error tests**

The public `backend_cpu` module, public `CpuBackend`, public method, and crate `rlib` mean an in-repo zero-caller result cannot authorize deletion. Preserve the exact public `anyhow::Result<()>` signature. Introduce narrow injectable test seams for directory-entry iteration and per-file replacement/write, then add hostile arms proving the seam fired. On the pre-fix implementation both hostile arms return `Ok(())`; after the fix they return `Err(...)` with stable context substrings naming the operation and path. Also retain successful zero-match and successful replacement controls. Do not rely on OS permission bits, which can pass vacuously under CI identities.

**Step 3: implement typed propagation**

Propagate errors through the existing `anyhow::Result<()>` with stable contextual messages at the public method boundary. Do not introduce a new public error type, remove, or rename the method. Any future removal requires an explicit breaking-API decision, a deprecation release, downstream migration guidance, and a major-version compatibility plan.

**Step 4: use CI for Rust verification**

Run `cargo fmt --check` locally only if Rust changes. Push the branch and use GitHub Actions for Cargo tests/checks under the shared-machine rule.

## Task 6: create the versioned pure edit-verification service

**Files:**

- Create: `src/tensor_grep/cli/edit_verification.py`
- Create: `tests/unit/test_edit_verification.py`
- Modify: `src/tensor_grep/cli/evidence_receipt.py`
- Modify: `src/tensor_grep/cli/evidence_signing.py`
- Modify: `src/tensor_grep/cli/main.py` for additive `tg evidence emit --edit-verification FILE|-`
- Modify: `tests/unit/test_evidence_receipt.py`
- Modify: `tests/unit/test_evidence_signing.py`
- Create: `tests/integration/test_evidence_command.py`
- Create: `tests/e2e/test_native_evidence_edit_verification.py`
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/harness_api.md`

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

First pin existing no-option `tg evidence emit` stdout/receipt bytes green on base. Then add a registration/help RED for `--edit-verification`, register a behaviorless option shell returning only sentinel `edit_verification_not_implemented`, and make help/routing plus legacy no-option behavior green. Only after that add each behavior node by itself and require its component/payload/exit-specific RED; no node may cite the unknown-option or sentinel response. Read repo-confined `FILE` only through the shared opened-handle safe JSON reader at cap−1/cap/cap+1 around 5 MiB. Freeze `FILE="-"` as the production handoff: read stdin exactly once to EOF or `cap+1`, apply the identical exact-byte cap and duplicate-rejecting decoder, reject coexistence with another stdin consumer, and never persist the result in the repository. Require exact `EditVerificationResultV1` with non-null baseline/policy digests, nonempty result-producing `verifier_version`, valid result `receipt_sha256`, and internally consistent verdict/reasons/trust. The receipt component copies that version verbatim and binds the exact result digest as `verification_result_sha256`; it never substitutes the running emitter version. Extend the receipt builder so it captures canonical root/repository identity/object format/commit/dirty digest once using the verifier's exact revision helper/exclusions, compares the result inside the builder, then places and signs that same immutable capture in the outer receipt—no adapter precheck plus later reread and no caller override. It may coexist with existing capsule/manifest inputs when none consumes stdin. Cross-repo, post-result clean/dirty/revision drift, old-verifier/current-emitter relabel attempts, one-field result mutation/digest disconnect, an Event-gated mutation before builder capture, outside-root, leaf/parent link/reparse, FIFO/special, swap, malformed, duplicate-key, or inconsistent input exits 2 and writes no receipt. A mutation after capture cannot alter the signed captured subject. Run every arm in both keyless and trusted-signed modes by node ID, plus coexistence and legacy controls. Add real subprocess round trips that run `verify-edit`, assert the producer exit directly, supply captured stdout bytes to `evidence emit --edit-verification -` without a shell pipeline, and assert evidence subject/result digest plus consumer exit in signed and keyless modes. Required arms are PASS 0→0; valid WARN and BLOCK 1→0 with the harness retaining producer=1 and the receipt preserving the verdict; digest-valid `result_byte_limit` INCOMPLETE 2→0 with producer=2 retained and the receipt preserving INCOMPLETE; and malformed/null/invalid-digest consumer=2 with no receipt. Direct shell piping is unsupported unless both statuses are preserved. These tests must not materialize a result file or allow a consumer zero to mask producer 1/2. `tests/e2e/test_native_evidence_edit_verification.py` itself pins compiled help/argv, stdin handoff and all four status classes, malformed, duplicate, version preservation, result-digest binding, cross-repo, revision/dirty drift, redirected cap−1/cap/cap+1, coexistence, legacy no-option, keyless, and signed cases with `TG_REQUIRE_RG_PARITY=1`; missing native binary or a skipped node is a CI failure.

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
- Modify: `docs/CONTRACTS.md`
- Modify: `docs/harness_api.md`

**Step 1: pin registration failure first**

Freeze exact Python/native argv `tg verify-edit REPO --baseline NAME --baseline-sha256 DIGEST --validation-file FILE [--deadline SECONDS] --json`. `NAME` is only the owned-state basename from the design; `DIGEST` is exact lowercase-64-hex external trust state; and the loader accepts only `EditBaselineV1`—never a capsule. Both baseline and validation inputs must flow through the shared safe bounded JSON reader, and exact opened bytes are hashed/compared before JSON use. Add `verify-edit` to the expected public set test before production registrations and capture that registration RED alone. Then add all four registrations with a behaviorless adapter that emits the exact result shape using sentinel reason `missing_required_field`; make routing tests green before behavior mapping. Add Python/native help snapshots, argv-parity, missing/63/65/nonhex/uppercase/well-formed-mismatch digest nodes, one-byte and schema-valid scope-expanding baseline mutations, validation-file cap/link/reparse/FIFO/swap, invalid basename, in-owned-dir baseline leaf/swap, outside-owned-state escape, duplicate-key, wrong-schema capsule, same-file target-symbol deletion, unchanged blast set, widening inside declared review-only WARN, widening outside BLOCK, exact descriptor drift, deadline partial, redirected final-wire cap−1/cap/cap+1 (newline included) with `result_byte_limit`, PASS exit 0, and JSON-before-exit tests. Every real INCOMPLETE node expects its own non-sentinel reason. Run each new test by exact node ID before implementing its mapping because the repository's global `-x` can otherwise hide later red arms.

```powershell
uv run --no-sync pytest tests/unit/test_cli_bootstrap.py tests/e2e/test_routing_parity.py tests/integration/test_verify_edit_command.py -q --timeout=15
```

Expected sequence: registration node fails; registration becomes green with the shell; then each PASS/WARN/BLOCK/INCOMPLETE/JSON node independently fails on its specific payload or exit assertion and is made green one mapping at a time. No behavior test may cite the earlier missing-command failure as its RED.

**Step 2: complete the thin Typer adapter after every mapping has a targeted RED**

The Typer command implements exactly `tg verify-edit REPO --baseline NAME --baseline-sha256 DIGEST --validation-file FILE [--deadline SECONDS] --json`, validates only adapter syntax/path confinement/digest grammar, calls `edit_verification`, prints the complete capped JSON payload, and maps verdict to exit code. It does not contain comparison or capsule-conversion policy.

**Step 3: verify Python and real native front door**

```powershell
uv run --no-sync pytest tests/unit/test_cli_bootstrap.py tests/e2e/test_routing_parity.py tests/integration/test_verify_edit_command.py -q --timeout=15
uv run --no-sync ruff check src/tensor_grep/cli/commands.py src/tensor_grep/cli/main.py src/tensor_grep/cli/edit_verification.py tests/integration/test_verify_edit_command.py
uv run --no-sync ruff format --check --preview src/tensor_grep/cli/commands.py src/tensor_grep/cli/main.py src/tensor_grep/cli/edit_verification.py tests/integration/test_verify_edit_command.py
uv run --no-sync mypy src/tensor_grep/cli/edit_verification.py src/tensor_grep/cli/main.py
cargo fmt --manifest-path rust_core/Cargo.toml --check
```

Add `tests/e2e/test_native_verify_edit.py` so the existing `native-build-smoke` `test_native_*.py` census executes the compiled binary with `TG_REQUIRE_RG_PARITY=1`. Missing compiled binary or a skipped node is a failure. The native file itself—not only routing parity—pins exact help, argv, deadline/input bounds, malformed/schema/path cases, baseline and validation safe-reader arms, target/blast/descriptor drift, PASS/WARN/BLOCK/each INCOMPLETE reason, stdout JSON, and real exit codes; it includes a control proving the Python service was reached through Rust dispatch. Then dogfood the built executable with all four verdicts.

## Task 8: implement strict `tg edit-ready` without changing legacy prepare/ledger

**Files:**

- Create: `src/tensor_grep/cli/prepare_service.py`
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

**Step 1: pin legacy prepare byte behavior**

Add/confirm fixtures proving ordinary `prepare` output and anonymous claim behavior remain unchanged after service extraction. Run them green on base before any refactor.

**Step 2: extract shared preparation service with zero behavior change**

Move composition logic behind a typed service API while leaving the existing Typer adapter's output identical. Run all prepare tests before adding strict behavior.

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
- symlinked/reparse-point fence files and parent-directory swaps fail closed before any claim mutation;
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

Register `edit-ready` through all four CLI sites with only the frozen argv above. The shared service owns the exact prepare projection, scope normalization, safe-reader descriptor validation, no-clobber baseline publication, and baseline request; thin Python/native adapters cannot synthesize fields. Open and identity-verify the owned baseline directory once, then publish only relative to that handle: Unix `openat`/`linkat(dirfd,...,dirfd,..., no-replace)`/`unlinkat` plus file/directory fsync; Windows relative `NtCreateFile` plus `FileRenameInfoEx`/`FILE_RENAME_INFO` with the verified directory as `RootDirectory` and replacement disabled. Never fall back to a path-based publish; unsupported primitives, parent/leaf swaps, or existing/same-NAME races return `baseline_write_failed`, remove only the temp, and roll back only this invocation's exact claim. Preserve release's existing pre-fence missing-index fast path so it creates neither ledger directories nor a fence; every actual RMW goes through callback-style `mutate_claims_index(index_path, callback, *, poll_interval_s=0.02, timeout_s=12.0)`. That helper acquires the claims fence, reads, invokes the callback, and accepts exactly `WRITE(records, result)` or `NO_WRITE(result)`. Only `WRITE` atomically publishes before release; `NO_WRITE` preserves existing bytes/inode/mtime, and callers never receive an independently publishable snapshot. The helper locks a stable per-root `<claims-index>.fence` artifact that normal operation never unlinks or atomically replaces, so all contenders lock the same inode/file object. Confine the canonical root/state path, reject symlink/reparse-point parents, open the fence no-follow, and verify the opened handle's identity/type before locking; parent/fence swaps fail closed. Unix opens `O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW` mode `0600` and attempts `flock(LOCK_EX|LOCK_NB)`. Windows opens with `CreateFileW(OPEN_ALWAYS, FILE_FLAG_OPEN_REPARSE_POINT)`, permits read/write sharing but denies delete sharing, and attempts `LockFileEx(LOCKFILE_EXCLUSIVE_LOCK|LOCKFILE_FAIL_IMMEDIATELY)` on byte `[0,1)`. `ClaimsFenceTimeoutError` subclasses `IndexLockTimeoutError`, preserving every legacy CLI's current exit-2 mapping; the strict adapter emits full `EditReadyTicketV1` with `status="incomplete"`, reason `claim_fence_timeout`, and exit 2. Other open/lock faults map to `claim_fence_error`/exit 2. Do not change the shared stale-reclaimable `IndexLock` behavior for checkpoint, session, or finding consumers. If claims lease metadata is retained, always acquire the OS fence first; lease expiry/reclaim is diagnostic only and can never authorize a concurrent writer. The strict callback treats every pre-existing overlapping claim as a conflict regardless of self-asserted `agent_id` and returns `NO_WRITE` with the full blocked ticket. Retain the opaque returned `claim_id` and rollback exclusively with `release_claim(claim_id=that_exact_id)`. Assert the released ID equals the captured ID; do not introduce a second nonce or claim-identity schema. Result fields state `authorization=false` and `identity_trust=self-asserted`.

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

**Step 1: red schema/aggregation tests**

First add a behaviorless `workspace_prepare` service plus CLI/native registrations that emit the exact schema with `result_incomplete=true`; this closes only the missing-API/registration red. Then red-green schema, validation, ordering, deadline, aggregation, and wire-cap slices independently. Pin the exact argv `tg workspace-prepare ANCHOR QUERY --root ROOT [--root ROOT ...] [--deadline SECONDS] --json`. Typer collects absent `--root` as an empty list so 0-root input reaches the service JSON arm. Cover 0/1/2/8/9 roots, duplicate/nested roots, relative roots resolved against the anchor, absolute roots inside/outside the anchor, symlink escapes, nonexistent roots, mixed success/failure, shared-deadline exhaustion, omitted roots, canonical path ordering, aggregate `result_incomplete` truth, query UTF-8 bytes at 0/1/16,384/16,385, anchor/root bytes at 32,768/32,769, nonfinite/nonpositive/>300-second deadlines, and the final-wire 8,388,608-byte cap at cap - 1/cap/cap + 1.

Pin the shared service/CLI workspace schema version 1 exactly: `{version, schema_version, workspace_prepare_version, routing_backend, routing_reason, anchor, query, roots, root_count, completed_root_count, omitted_roots, result_incomplete, incomplete_reasons, error}` with per-root `{root, status, payload, error, incomplete_reason}`. Success and partial results set top-level `error=null`. Zero/duplicate/nested/out-of-anchor/>8-root input returns the same full key set with canonical `anchor` or null, `roots=[]`, counts zero, `result_incomplete=true`, `incomplete_reasons=["invalid_input"]`, and `error={"code":"invalid_input","message":...}`, then exits 2. Any partial/omitted/failed root after dispatch emits JSON then exits 2. One root still returns the workspace schema; legacy `tg prepare` remains byte-identical. Exact key-set and same-fixture value-equality tests compare service, Typer CLI, and compiled native CLI for both success and partial arms.

Choose sequential execution in canonical root order under one shared absolute deadline. Remove root-parallelism and lock-overlap claims; tests spy the exact dispatch order and prove that root N+1 receives only the remaining deadline. The 1/2/8-root CI benchmark is a scaling observation for this sequential contract, not a concurrency claim.

**Step 2: implement explicit bounded aggregation**

Accept only explicit roots. Canonicalize/confine all inputs before dispatch. Allocate one shared absolute deadline and report every omitted root. Use one compact UTF-8 serializer with transport fields/suffix supplied before the 8,388,608-byte inclusive measurement; CLI's trailing newline is inside its cap. Payload omission produces the same minimal envelope, and tests assert it fits at maximum valid query/path/error inputs. Do not create cross-root ledger enforcement.

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

Expose exact always-on task tool `tg_workspace_prepare`, available in both full and lean surfaces. It accepts `{anchor: str, query: str, roots: list[str], deadline: float | null}` and returns the exact shared workspace service envelope from Task 12, injecting only the standard `mcp_contract_version` field. Success, partial, and invalid-input arms preserve the service key set and meanings; the adapter does not invent a second error envelope. The shared serializer injects `mcp_contract_version` before enforcing the 8,388,608-byte inclusive final tool-string cap and reserves that exact overhead when deciding whether payloads fit.

The adapter first resolves `anchor` through `_confine_mcp_path(anchor, ...)` under `_mcp_root()`, then requires every canonical root to be contained by both the resolved anchor and `_mcp_root()`. Add absolute, `..`, symlink/junction escape, 0/1/8/9-root, shared-deadline, omitted-root, and final-tool-string cap - 1/cap/cap + 1 tests. Same-fixture value-equality tests compare service, Typer CLI, compiled native CLI, and real MCP stdio for success and partial arms after removing only `mcp_contract_version`; boundary tests compare actual bytes for each transport rather than object size.

Write the registration red first, add a behaviorless live tool, then red-green confinement, equality, partial, and cap behavior independently so none can pass merely because registration/import is missing. Because Task 4 already moved the contract to 1.8.0, this additive tool-set change bumps it to 1.9.0 and updates every exact pin in `mcp_server.py`, MCP unit tests, stdio integration tests, contract-doc tests, and harness docs. The always-on population changes from 58/12 to 59/13; subprocess flag arms assert both exact sets. Real stdio tests must call `tools/list`, invoke `tg_workspace_prepare`, validate the error/success envelopes, and prove the legacy tools remain callable in full mode. Reuse `workspace_prepare` directly; never shell out to the CLI.

Mandatory adversarial MCP review and published-wheel stdio dogfood for full and lean surfaces are required.

## Task 14: close, retire, or escalate remaining known items

**Files:**

- Modify: `docs/TASK_BOARD.md`
- Modify: `docs/BACKLOG.md`
- Modify: `docs/SESSION_HANDOFF.md`
- Create/update: decision records under `docs/investigations/`

Record:

- DD-004 raw `RuntimeError`: implement typed wrapping only if the Task 5/backend audit proves a stable boundary; otherwise retain INFO with trigger.
- DD-006 daemon semaphore: retain demand gate until measured concurrent load/DoS evidence exists.
- F10 MaxSim: perform a caller/config/public-contract census. Remove only if unreachable and non-public; otherwise keep default-off with an explicit activation/retirement trigger.
- C++ macro structural limitation: retain explicit limitation unless Task 10E obtains a preprocessor-aware oracle.
- #255 many-pattern dedup: preserve the guard and prepare a CEO decision record with a minimal parity fix experiment; do not spend GPU/cloud or promote native routing without approval.
- #48, #72, #77, #131/#169: update evidence and decision prerequisites only.

No deferred entry may use “later” without an owner, trigger, and reason.

## Task 15: final independent audit, merge drain, and published-artifact dogfood

**Files:**

- Modify: `docs/TASK_BOARD.md`
- Modify: `docs/BACKLOG.md`
- Modify: `docs/SESSION_HANDOFF.md`
- Modify: `AGENTS.md` if a new durable process law was learned
- Create: `docs/audits/2026-08-02-backlog-closeout-final.md`

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

For each PR, wait for newest main CI `completed`; if it released, wait until PyPI serves the version. Merge through GitHub with squash and branch deletion. After merge, structurally verify the intended code and rerun the critical fixture against `main`.

**Step 4: published-wheel verdict table**

Create two explicitly attributed sections. For user-visible contracts, use a clean temporary environment, derive the published version from PyPI, run that exact wheel, and record raw JSON plus PASS/FAIL:

```powershell
$tgPublishedVersion = (Invoke-RestMethod -Uri "https://pypi.org/pypi/tensor-grep/json").info.version
uvx --from "tensor-grep@$tgPublishedVersion" tg --version
```

- MCP `full` and `lean` surfaces;
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
- separate claims-fence rows for same-root legacy/strict/release exclusion, different-root independence, timed contention, killed-holder crash release with unchanged lease metadata, and exact final index contents;
- separate baseline no-clobber rows for sequential/concurrent same-NAME, pre-existing leaf/reparse, Unix parent swap, mandatory Windows parent-junction swap, and exact loser-claim rollback;
- each language's in-file parser-backed positive/decoy/grammar-missing triplet;
- each Task 11 language's separate cross-file resolved/decoy/unresolved triplet;
- `workspace-prepare` CLI 1/2/8-root completeness, invalid-root exit 2, output-cap exit 2, and compiled-native routing;
- separate same-fixture service/Python CLI/native CLI/MCP value-equality rows for success and partial results;
- per-transport final-wire cap−1/cap/cap+1 rows, including CLI newline and MCP contract-field overhead;
- `tg_workspace_prepare` full/lean `tools/list`, success, confinement error, partial result, and final-wire 8 MiB cap behavior;

For non-wheel-visible internal contracts, record separately labeled source-tree CI receipts tied to the exact merged SHA; never attribute them to the wheel:

- writer-census historical control, injected unsafe-writer mutation control, aliased-sink controls, and current-tree zero-violation census;
- SHA-1/SHA-256 repository and index-object round trips;
- exact baseline/path/prepare/receipt-component/result/ticket schema key sets, cross-field invariants, exhaustive reason-to-verdict/status/exit partition, and malformed-input envelopes;
- claims `WRITE` publication versus `NO_WRITE` preservation, including absent-index no-state and existing-index/no-match byte/inode/mtime stability;
- `CPUBackend.replace_in_place` walk-fault and replacement-fault tests;
- platform-specific Windows opened-handle and claims-fence process tests when the published-wheel environment is not that platform.

Create `docs/audits/2026-08-02-backlog-closeout-dogfood.md` with one row per shipped contract. Wheel rows record the exact published version; internal rows record the exact merged commit SHA and CI run. Every row also records command/test ID, expected fields/order, expected verdict/status/exit where applicable, raw artifact/log path, attribution (`published-wheel` or `merged-source-ci`), and PASS/FAIL. A category-level summary row cannot substitute for the individual cases above.

Read raw JSON before scoring and preserve the command's exit code without pipe masking.

**Step 5: close documentation truth**

Update current release/version, shipped receipts, remaining CEO/financial decisions, and all new findings. The final audit document includes commit SHAs, PRs, CI run IDs, published version, raw dogfood artifact paths, and any explicitly retained limitations.
