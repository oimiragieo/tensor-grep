# Stranded-work premise recheck (2026-08-13): H3/H6 audit fixes are already on `origin/main`

> Re-derived 2026-08-13 against `origin/main` `9738134c7772bd30e4cd51fba9aa7ebe2efcedfa`
> (public product v1.110.14). Every command below was run in a fresh worktree off that
> exact SHA; the dirty main checkout was never read.

## 1. `git cherry` output, verbatim

```text
+ f1e888c2276f78a7c5c7108157c65ae377d7f391
+ 928e9b270d2b21602a18708183eb2f748554d389
- d9e477b7a8a7b47b3357e7b732d67ef2631279ea
```

`+` means patch-DISTINCT, **not absent**: a squash-merge or rebase changes the patch-id even
for identical content. `-` means patch-id equivalent upstream. None of the three lines by
itself proves anything about whether the FIX is on main; only the content receipts below do.

## 2. Per-commit disposition

| commit | subject | disposition | content receipt on `origin/main` |
|---|---|---|---|
| `d9e477b` | fix: remove Cmd/BatBadBut batch-shim wrap in python_sidecar (H3 audit) | **SHIPPED** (patch-id equivalent upstream) | `rust_core/src/python_sidecar.rs` `command_for_executable` returns plain `Command::new(program)` with the H3 audit comment; tests `command_for_executable_never_wraps_batch_shim_in_cmd` and `command_for_executable_plain_program_untouched` present |
| `f1e888c` | fix: normalize CuDFBackend engine failures to BackendExecutionError (H6 audit) | **SHIPPED-BY-CONTENT** (patch-distinct, content present) | `src/tensor_grep/backends/cudf_backend.py` imports `BackendExecutionError` and wraps the engine entry; the H6 audit comment and per-file CPU-fallback rationale are verbatim |
| `928e9b2` | test: restore pre-existing cudf_backend test file, append H6 fail-closed tests | **SHIPPED-BY-CONTENT** | `tests/unit/test_cudf_backend.py` carries the `H6 audit` docstring on the fail-closed test |

## 3. The RED arm (what these commands would print if the premise were TRUE)

If the fixes really were stranded: `git cherry` would print `+` for `d9e477b` (not `-`), and
`git show origin/main:rust_core/src/python_sidecar.rs` would contain no H3-audit
`Command::new(program)` guard; `git show origin/main:src/tensor_grep/backends/cudf_backend.py`
would contain no `BackendExecutionError` normalization. Both arms were checked and show the
opposite, so the campaign's wave-1 re-ship was cancelled.

## 4. Independent corroboration, one day earlier

`docs/audits/2026-08-12-stale-branch-reconciliation.md` (tracked on `origin/main`) reached the
same conclusion on 2026-08-12 - **one day** before this receipt. The corroboration's strength
is that the two audits used different methods: that one read the branch ledger, this one reads
`origin/main` content directly. No inflated interval is claimed; the independence is
methodological, not temporal.

## 5. What this cost / what it saved

Two release-bearing PR cycles were budgeted for the stranded-fix rescue and are not needed.
The freed capacity was reallocated in the campaign design (section 9): the wave-1 PR slot
goes to the A101 probe-retry fix (W2A), and the H3/H6 branch itself stays untouched with its
uncommitted work preserved.

## 6. Proposed branch/worktree cleanup (recorded, NOT executed)

Enumerated 2026-08-13: 145 local branches, 43 worktrees, 4 dirty worktrees. Each branch got
its own `git merge-base --is-ancestor <branch> origin/main` run with exit codes discriminated
(`0` LANDED, `1` NOT-LANDED, other CANNOT-MEASURE). A30's caveat applies: after squash-merges
`--is-ancestor` under-reports, so NOT-LANDED is **not** proof a branch holds unique work -
a closed PR is not a merged PR, and any future delete needs `gh pr list --head <branch>`
verification first. LANDED, by contrast, IS a proof: an ancestor of `origin/main` has every
commit in main, so deleting it loses nothing.

| disposition | count | meaning |
|---|---|---|
| PROPOSED-DELETE | 17 | ancestor of origin/main; commits provably in main |
| KEEP | 128 | not proven in main; may hold WIP (squash false-read possible) |
| CANNOT-MEASURE | 0 | git errored; never merged into KEEP |

| branch | ancestor of `origin/main`? | uncommitted entries in its worktree | disposition |
|---|---|---|---|
| `audit/h1-backend-cpu-failclosed` | NOT-LANDED | 0 | KEEP |
| `audit/h2-native-json-count-refuse` | NOT-LANDED | 0 | KEEP |
| `audit/h6-cudf-backend` | NOT-LANDED | 43 | KEEP |
| `audit/m1-checkpoint-create-containment` | NOT-LANDED | 0 | KEEP |
| `audit/m10-ast-wrapper-incomplete` | NOT-LANDED | n/a | KEEP |
| `audit/m13-query-language-aliases` | NOT-LANDED | n/a | KEEP |
| `audit/m14-mcp-contract-stamp-ratchet` | NOT-LANDED | 0 | KEEP |
| `audit/m16-rust-scan-composite` | NOT-LANDED | 0 | KEEP |
| `audit/m17-index-root-check` | NOT-LANDED | 0 | KEEP |
| `audit/m3-lsp-fileops-confinement` | NOT-LANDED | 0 | KEEP |
| `audit/m7-verify-never-raises` | NOT-LANDED | n/a | KEEP |
| `audit/m8-ast-invert-failclosed` | NOT-LANDED | n/a | KEEP |
| `bench/find-centrality-golden` | NOT-LANDED | n/a | KEEP |
| `campaign/backlog-closeout-2026-08-02` | NOT-LANDED | n/a | KEEP |
| `ci/audit-policy-repair` | NOT-LANDED | n/a | KEEP |
| `ci/cost-smart-gate` | NOT-LANDED | n/a | KEEP |
| `ci/cost-smart-gate-rebase` | NOT-LANDED | 0 | KEEP |
| `ci/smoke-test-reports-its-own-failure` | NOT-LANDED | n/a | KEEP |
| `docs-task-board` | NOT-LANDED | n/a | KEEP |
| `docs/1.101.19-stamps-and-review-laws` | NOT-LANDED | n/a | KEEP |
| `docs/2026-08-13-premise-recheck` | LANDED | 0 | PROPOSED-DELETE |
| `docs/backlog-2026-08-10-campaign-note` | NOT-LANDED | 0 | KEEP |
| `docs/backlog-closeout-plan` | LANDED | n/a | PROPOSED-DELETE |
| `docs/backlog-refresh-a3` | NOT-LANDED | n/a | KEEP |
| `docs/board-ready-blocked-stamp` | NOT-LANDED | 0 | KEEP |
| `docs/board-reconcile-2026-08-08` | NOT-LANDED | n/a | KEEP |
| `docs/campaign-findings-2026-08-08` | LANDED | 0 | PROPOSED-DELETE |
| `docs/ceo-2026-08-06-update` | NOT-LANDED | 0 | KEEP |
| `docs/ceo-update-2026-08-06pm` | NOT-LANDED | 0 | KEEP |
| `docs/ceo-update-2026-08-11` | NOT-LANDED | n/a | KEEP |
| `docs/closeout-findings-2026-08-06` | NOT-LANDED | 0 | KEEP |
| `docs/completion-plan-2026-08-08` | NOT-LANDED | 0 | KEEP |
| `docs/dogfood-110131-findings` | NOT-LANDED | n/a | KEEP |
| `docs/f1-exit-contract-artifacts-new` | LANDED | n/a | PROPOSED-DELETE |
| `docs/f10-maxsim-disposition` | NOT-LANDED | n/a | KEEP |
| `docs/fresh-context-is-different-not-better` | LANDED | n/a | PROPOSED-DELETE |
| `docs/laws-retention-2026-08-09d` | NOT-LANDED | 0 | KEEP |
| `docs/lessons-2026-08-02` | NOT-LANDED | n/a | KEEP |
| `docs/lessons-retention-2026-08-09b` | NOT-LANDED | n/a | KEEP |
| `docs/orthogonal-review-layers` | NOT-LANDED | n/a | KEEP |
| `docs/phase01-launch-receipt` | NOT-LANDED | 0 | KEEP |
| `docs/pin-mcp-contract-version` | NOT-LANDED | n/a | KEEP |
| `docs/plan-is-least-audited-law` | NOT-LANDED | n/a | KEEP |
| `docs/probe-discards-error-law` | NOT-LANDED | n/a | KEEP |
| `docs/reconcile-post-fanout` | NOT-LANDED | n/a | KEEP |
| `docs/reconcile-stamp-v1102` | NOT-LANDED | n/a | KEEP |
| `docs/reconcile-task-board-2026-08-05` | NOT-LANDED | n/a | KEEP |
| `docs/reconcile-task-board-v1.110.12` | NOT-LANDED | 0 | KEEP |
| `docs/review-layer-laws` | NOT-LANDED | n/a | KEEP |
| `docs/session-capture-2026-08-09c` | NOT-LANDED | 0 | KEEP |
| `docs/session-close-laws` | LANDED | n/a | PROPOSED-DELETE |
| `docs/session-receipts-2026-08-08` | NOT-LANDED | 0 | KEEP |
| `docs/signal-path-and-canary-laws` | NOT-LANDED | n/a | KEEP |
| `docs/skill-accuracy-2026-08-09` | NOT-LANDED | 0 | KEEP |
| `docs/skill-accuracy-sweep` | NOT-LANDED | n/a | KEEP |
| `docs/skill-audit-2026-08-11` | NOT-LANDED | n/a | KEEP |
| `docs/skill-coverage-2026-08-11` | NOT-LANDED | n/a | KEEP |
| `docs/skill-dogfood-1.110.10` | NOT-LANDED | 0 | KEEP |
| `docs/skill-dogfood-1.110.12` | NOT-LANDED | 0 | KEEP |
| `docs/skill-dogfood-1.110.13` | NOT-LANDED | 0 | KEEP |
| `docs/skills-1.101.17-dogfood` | NOT-LANDED | n/a | KEEP |
| `docs/surface-tg-prepare` | NOT-LANDED | n/a | KEEP |
| `docs/w5-enterprise-dogfood` | NOT-LANDED | 0 | KEEP |
| `docs/worldclass-roadmap-2026-08-09` | NOT-LANDED | 0 | KEEP |
| `feat/dogfood-feature-tail-160` | NOT-LANDED | n/a | KEEP |
| `feat/edit-ready-python-half` | NOT-LANDED | n/a | KEEP |
| `feat/enterprise-backlog-closeout` | LANDED | 1 | PROPOSED-DELETE |
| `feat/f7-task11-c-cpp-cross-file` | NOT-LANDED | 0 | KEEP |
| `feat/f7-task11-java-cross-file` | NOT-LANDED | n/a | KEEP |
| `feat/phase01-launch` | NOT-LANDED | n/a | KEEP |
| `feat/pr-s2-channelized-rrf` | NOT-LANDED | n/a | KEEP |
| `feat/tg-session` | NOT-LANDED | n/a | KEEP |
| `fix-303-large-stdout-clock-flake` | NOT-LANDED | n/a | KEEP |
| `fix-m17` | NOT-LANDED | 1 | KEEP |
| `fix/a90-unknown-command-fail-closed` | NOT-LANDED | 0 | KEEP |
| `fix/ast-apply-symlink-write` | NOT-LANDED | n/a | KEEP |
| `fix/ast-classifier-drift` | NOT-LANDED | n/a | KEEP |
| `fix/ast-fallback-visibility` | NOT-LANDED | n/a | KEEP |
| `fix/atomic-locked-index` | NOT-LANDED | n/a | KEEP |
| `fix/bare-search-names-its-scope` | NOT-LANDED | n/a | KEEP |
| `fix/cap-mcp-below-2` | NOT-LANDED | n/a | KEEP |
| `fix/classify-violating-writer-sites` | NOT-LANDED | n/a | KEEP |
| `fix/doctor-path-honesty` | NOT-LANDED | 0 | KEEP |
| `fix/findings-ledger-repo-scoped` | NOT-LANDED | n/a | KEEP |
| `fix/freshness-gate-minor-bump` | NOT-LANDED | n/a | KEEP |
| `fix/frontdoor-download-held-fd` | NOT-LANDED | n/a | KEEP |
| `fix/full-cli-route-scope-note` | NOT-LANDED | n/a | KEEP |
| `fix/imports-and-orient-never-silent` | NOT-LANDED | n/a | KEEP |
| `fix/large-root-refusal-json-envelope` | NOT-LANDED | n/a | KEEP |
| `fix/native-argv-end-of-options` | NOT-LANDED | n/a | KEEP |
| `fix/opus-gate-low-followups` | NOT-LANDED | n/a | KEEP |
| `fix/stale-staging-symlink-cleanup` | NOT-LANDED | n/a | KEEP |
| `fix/wsl-path-domain` | NOT-LANDED | n/a | KEEP |
| `h2-draft` | NOT-LANDED | n/a | KEEP |
| `inspect-m16` | NOT-LANDED | n/a | KEEP |
| `inspect-m17` | NOT-LANDED | n/a | KEEP |
| `local-bootstrap-88-parity-fix` | NOT-LANDED | n/a | KEEP |
| `main` | LANDED | 0 | PROPOSED-DELETE |
| `pr-597-gate` | NOT-LANDED | n/a | KEEP |
| `pr-628` | NOT-LANDED | n/a | KEEP |
| `pr-638` | NOT-LANDED | n/a | KEEP |
| `pr-911` | NOT-LANDED | n/a | KEEP |
| `pr19-merge-repro` | NOT-LANDED | n/a | KEEP |
| `pr744` | NOT-LANDED | n/a | KEEP |
| `pr744check` | NOT-LANDED | n/a | KEEP |
| `pr746-7a89a0b` | NOT-LANDED | n/a | KEEP |
| `probe/classifier-feasibility` | NOT-LANDED | n/a | KEEP |
| `refactor/extract-prepare-service` | NOT-LANDED | n/a | KEEP |
| `replay-lock-parity-on-main` | NOT-LANDED | n/a | KEEP |
| `replay/parity-closeout-20260417` | NOT-LANDED | n/a | KEEP |
| `rescue/lazy-wave-stash-2026-08-01` | NOT-LANDED | n/a | KEEP |
| `research/ast-dsl-divergence` | NOT-LANDED | n/a | KEEP |
| `research/mcp-tool-consolidation` | NOT-LANDED | n/a | KEEP |
| `research/policy-layer-positioning` | NOT-LANDED | n/a | KEEP |
| `skills-dogfood-11019` | NOT-LANDED | n/a | KEEP |
| `task2a-round60-red` | NOT-LANDED | 0 | KEEP |
| `test/enterprise-cuj-chain` | NOT-LANDED | 1 | KEEP |
| `test/release-asset-lock-parity` | NOT-LANDED | n/a | KEEP |
| `test/release-lock-parity-post-audit` | NOT-LANDED | n/a | KEEP |
| `test/repo-retrieval-benchmark-contract` | NOT-LANDED | n/a | KEEP |
| `test/retrieval-benchmark-post-audit` | NOT-LANDED | n/a | KEEP |
| `wave2/cudf-device-bind` | NOT-LANDED | n/a | KEEP |
| `wave2/gpu-cpu-oracle` | NOT-LANDED | n/a | KEEP |
| `wave2/gpu-loud-fallback` | NOT-LANDED | n/a | KEEP |
| `wave2/gpu-observability-doctor` | NOT-LANDED | n/a | KEEP |
| `wave2/gpu-proof-audit` | NOT-LANDED | n/a | KEEP |
| `wave2/installer-uv-sha` | NOT-LANDED | n/a | KEEP |
| `wip/diff-docs-precision` | NOT-LANDED | n/a | KEEP |
| `worker/auto-extract-rg-benchmark-clean2` | NOT-LANDED | n/a | KEEP |
| `worker/auto-extract-rg-benchmark-clean5` | NOT-LANDED | n/a | KEEP |
| `worker/cli-parity-test-suite-bd4777f4` | NOT-LANDED | n/a | KEEP |
| `worker/crossover-calibration-ad6c8279` | NOT-LANDED | n/a | KEEP |
| `worker/fix-pytest-editable-path-clean` | NOT-LANDED | n/a | KEEP |
| `worker/fix-ruff-git-exclude-clean` | NOT-LANDED | n/a | KEEP |
| `worktree-agent-a0adb0ef6c48a1ea8` | LANDED | n/a | PROPOSED-DELETE |
| `worktree-agent-a56d208d7e2c66714` | NOT-LANDED | n/a | KEEP |
| `worktree-agent-a6fb15c59488036ef` | LANDED | n/a | PROPOSED-DELETE |
| `worktree-agent-a840e9d28baa5002f` | NOT-LANDED | n/a | KEEP |
| `worktree-agent-a93618ab3b35326f9` | LANDED | n/a | PROPOSED-DELETE |
| `worktree-agent-aad6794295c7375c4` | LANDED | 0 | PROPOSED-DELETE |
| `worktree-agent-afd97febbb09c6aad` | LANDED | n/a | PROPOSED-DELETE |
| `worktree-wf_abcb9753-a3a-1` | LANDED | n/a | PROPOSED-DELETE |
| `worktree-wf_abcb9753-a3a-2` | LANDED | n/a | PROPOSED-DELETE |
| `worktree-wf_abcb9753-a3a-3` | LANDED | n/a | PROPOSED-DELETE |
| `worktree-wf_abcb9753-a3a-4` | LANDED | n/a | PROPOSED-DELETE |

The 4 dirty worktrees recorded above (main checkout on `audit/h6-cudf-backend` with 43
uncommitted entries, `enterprise-closeout-plan`, `w2b-enterprise-cuj`, `tg-backlog-m17`) are
pre-existing in-flight work and were not modified, staged, or stashed by this campaign.

**No branch, worktree, or file listed above was deleted by this campaign. Execution requires
an explicit operator acknowledgement.**
