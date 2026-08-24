# tensor-grep — Task Board

> **The operational one-pager.** `docs/BACKLOG.md` is the historical ledger (long, append-only,
> release-by-release); THIS file is the live queue a session or subagent works down, one item at a
> time. Keep it in sync with the CLI task store (`TaskList` / `TaskUpdate`) and with
> `gh pr list` — GitHub is the source of truth for PR state, this file is the source of truth for
> WHAT IS NEXT AND WHY.
>
> **Rules for anyone (human or agent) working this board:**
> 1. Take the top unblocked item in the highest-priority section. Do not cherry-pick easy ones.
> 2. Every item needs a **bidirectional oracle** before it is done — state what the test shows on
>    the PRE-FIX baseline. A test that passes in both arms is not evidence (AGENTS.md, oracle forms).
> 3. Move an item to DONE only with a PR number AND a merged commit. "Verified locally" is not done.
> 4. If an item turns out to be a non-defect, move it to **RETIRED** with the reason — a documented
>    retirement is worth as much as a fix, because it stops the next session re-chasing it.
> 5. `fix:`/`feat:` PRs RELEASE. Merge one per publish and wait for it. `docs:`/`test:`/`chore:`
>    do not release and may batch. **The merge gate is "no runs in flight on main", full stop** —
>    `tag == PyPI` cannot distinguish *released* from *not started* from *died* and cost a release
>    on 2026-07-28.

## Freshness correction (2026-08-23)

**Public product is now `v1.113.5`** (verified two independent ways: PyPI JSON API for
`tensor-grep/1.113.5` lists exactly 4 files — both wheels, `win_amd64`, and the sdist — and a
live `pip download tensor-grep==1.113.5 --platform win_amd64` actually pulled the win_amd64
wheel). The size-cap crisis described below (2 of 4 artifacts, Windows/sdist missing) does
**not** reproduce on this release: all 4 artifacts are real and downloadable. This note does not
confirm WHY (whether Policy A remediation actually ran, or the cap simply wasn't hit yet this
release) — only that the CURRENT release is clean. Do not re-run the size-cap decision packet's
authorization step without first re-measuring the project's total size; the campaign note below
is left as-is for history, not deleted, per this board's own rule 4 (retire with reason, don't
erase).

## Campaign note (2026-08-21) — HISTORICAL, see freshness correction above

Public product `v1.111.1` — but read the next paragraph before treating that as shipped.

**Release pipeline is BROKEN and the failure is platform-skewed.** PyPI project `tensor-grep`
is at **10.734 GB against PyPI's 10 GB per-project cap** (713 releases / 2,847 files,
re-measured 2026-08-21 and agreeing with the 2026-08-20 measurement). `v1.111.1` published
**2 of its 4 artifacts** before the cap 400'd the rest: the macOS-arm64 and manylinux wheels
exist; the **win_amd64 wheel and the sdist do not**. So `pip install tensor-grep` gives
v1.111.1 on Mac/Linux and falls back to **v1.111.0** on Windows and for source installs.
Two further releases are also incomplete (`1.13.44` missing its sdist; `0.1.0` predates the
native-wheel shape). **Law: verify a release PER-ARTIFACT against the expected filename set,
never by the version merely appearing.** The standalone native binaries DO exist as GitHub
release assets for v1.111.1, so a Windows user has a working non-pip path meanwhile.

Remediation is CEO-GATED and irreversible (PyPI has **no delete API** — Warehouse issue
#12934 is open, deletion is web-UI only, and hCaptcha broke the community automation tool).
Decision packet with three costed retention policies:
`docs/audits/2026-08-21-pypi-size-cap-decision-packet.md`. **Policy A authorized 2026-08-21**
(keep last 5 minor lines + every `X.Y.0`; deletes 548, frees 8.20 GB, ~439 releases of
headroom). Execution is blocked on an authenticated PyPI browser session — an agent must not
enter credentials.

Do NOT count PR #1067 (`5423b4b`, strip debug symbols) as partial remediation: measured, it
did **not** shrink artifacts. Last-50 average is 18.67 MB, equal to the last-20 average, and
the largest release in project history is v1.111.0 at 19.1 MB, published the day before the
incident.

**Merge-eligibility consequence, in force until the cap clears:** a `fix:`/`feat:` merge tags a
version and then fails its PyPI upload, leaving another committed-not-shipped version.
`refactor:`/`test:`/`docs:`/`chore:` publish nothing and batch freely. Derive the class from the
COMMIT that will land — on a single-commit PR GitHub squashes using the commit subject, not the
PR title.

### New law — STACKED-PR-CI-BLINDSPOT (2026-08-21)

**A PR whose base is a feature branch gets ZERO CI, and the absence renders as "skipping".**
`ci.yml` filters `pull_request: branches: [ "main" ]`, and that filter matches the **base** ref.
Measured: #1068 and #1070 each had exactly one check across their whole life
(`Dependabot Automation` / `skipped`) while `gh` reported them `MERGEABLE`. Control: #1065, same
`test/` branch prefix but base `main`, had `SUCCESS=39` — so it is the base ref, not the branch
name, and not a runner outage. **Both went RED the moment real CI ran.** They had been sitting
merge-ready, carrying error-handling hardening, with no evidence at all.

- Before merging anything, assert `baseRefName == "main"`:
  `gh pr list --state open --json number,baseRefName`.
- A "skipping"-only rollup is an ABSENT gate, not a pass.
- `gh pr edit --base main` alone does NOT restore CI (it fires `edited`, not a default trigger
  type). Close/reopen does.
- After a squash-merge of the parent, a stacked child conflicts: rebase it with
  `git rebase --onto origin/main <parent-tip>` to drop the commits the squash absorbed.

### Second law — a split must reproduce its baseline pass/skip counts

A drafted split of `test_mcp_server.py` reported "484 passed, 5 skipped" and looked green. The
pre-split baseline is **489 passed, 0 skipped**: it had invented three
`pytest.skip("embedded native rewrite unavailable in this environment")` guards, permanently
disabling three tests that pass in CI. The three were then confirmed to fail in the worktree even
in isolation, because **a bare git worktree has no compiled native extension** — an environment
artifact, not a defect. Capture the baseline passed AND skipped counts before any split, and never
silence a post-split failure with an environment probe.

### Third note — a Windows `ruff format --preview` sweep is mostly a lie

On a Windows checkout `ruff format --preview .` reports 14–18 files needing reformatting even on
clean `main`. That is `[tool.ruff.format] line-ending = "lf"` meeting a CRLF working tree; those
files are clean on Linux CI. Format only the files your branch actually owns, and diff before
committing — a repo-wide sweep here produces a large spurious diff.

## Campaign note (2026-08-15)

CEO update: `docs/audits/2026-08-15-ceo-backlog-update.md`. Public product `v1.110.16`
(unchanged). DD-006 design packet merged (#1015 / `0710219`); Fable waived for that docs
packet only (A117); product build still needs a deliberate go. Index stays `2026-08-13.1`.
STOP unchanged: Task 2A (#966 parked RED), W3 rust/e2e shared-box ban, MCP wire fence, #169,
CEO_GATED. New laws A117-A122.

## Campaign note (2026-08-14)

Public product is **`v1.110.16`** (unchanged; no release this campaign). The 2026-08-14 W5-W8
closeout executed the tail of `docs/plans/2026-08-13-backlog-completion-plan.md`: W5 demand
dispositions (DD-006's demand condition is now SATISFIED by a control-validated bounded
20-client probe - the reproduction rides the row trigger; #255 and AST-DSL-PARITY LEAVE;
MCP-LEAN-DEFAULT still Task-2C-fenced; CONTINUOUS-REFRESH scoping-pass reopen strengthened) -
`docs/audits/2026-08-13-demand-gated-dispositions.md`; W6 six BLOCKED rows re-derived, zero
flips; W7 CEO packet deltas with an 8-seat council (7/7 HYBRID-ACCEPTED / ADVISORY-ONLY) -
`docs/audits/2026-08-13-ceo-gated-packets.md`. No release, no spend, no product code.

## Campaign note (2026-08-12)

Public product is **`v1.110.14`** (unchanged; no release this campaign). The 2026-08-12
backlog-closeout campaign (plan `docs/plans/2026-08-12-backlog-closeout-campaign.md`, rev 6,
codex-APPROVED after 4 audit rounds + adversarial-seat APPROVED) landed: (1) **Task 2A resumed**
— A22 union-merge of `b6dc0a6` into `task2a-round60-red` flipped PR #966 CONFLICTING→MERGEABLE
and opened the first-ever Actions evidence chain; five repair rounds pushed (mod-tests brace
unclosed since `c550a84`, E0282, lib-test imports + ruff sweep, R1 collector-reachability with a
RED-proven 3-arm ratchet) — receipts in `docs/audits/2026-08-12-stale-branch-reconciliation.md`
§3–4; **still NOT GREEN** (Sol exact-byte SHIP + Windows census evidence outstanding). (2)
**Stale-checkout reconciliation**: the `audit/h6-cudf-backend` branch + 21-file dirty tree
contain ZERO unlanded work (all shipped via #975/#976/#987 etc.). (3) **Fresh Exa research
receipts** (`docs/audits/2026-08-12-research-receipts.md`, 47 sources): frontier scan CONFIRMS
the edit-control-plane thesis; demand rows re-dispositioned below — RUST-REPLACE-SYMLINK flips
to READY on 2026 CVE-class evidence. STOP set unchanged otherwise: W3 rust/e2e shared-box ban,
MCP wire fence, #169, CEO_GATED. R0 packets:
`docs/audits/2026-08-06-ceo-gated-recommendation-packets.md`.

## Campaign note (2026-08-11, historical)

Public product is **`v1.110.14`** (tag + PyPI + GitHub assets, 2026-08-11). This session shipped the
rest of the closeout audit wave (H2/M1/M3/M14), M16/M17 (v1.110.11/.12), A90 unknown-command fail-closed
(v1.110.13, #997), doctor PATH-honesty (v1.110.14, #1000), spend-smart CI (#977), and the skill-library
evolution waves (#999/#1001/#1002 — audit + coverage-gap, new release-drift-check + worldclass-roadmap
skills). CEO packet: `docs/audits/2026-08-11-ceo-backlog-update.md`. Index `2026-08-11.1`. STOP
unchanged: Task 2A not GREEN (#966 drafted, RED by design), W3 rust/e2e shared-box ban, MCP wire fence,
#169, CEO_GATED — 6 BLOCKED / 5 CEO_GATED / 6 DEMAND_GATED unchanged, no new rows. R0 packets:
`docs/audits/2026-08-06-ceo-gated-recommendation-packets.md`,
`docs/audits/2026-08-06-demand-gated-research-receipts.md`.

## Canonical status index

Canonical status index version: 2026-08-13.1
- [x] **#22** — Status: RETIRED; PR: none; Trigger: exit 0 is complete with matches; exit 1 is complete with no match; exit 2 is incomplete; gpu_request_unhonoured stays in-band and does not independently force exit 2
- [x] **F2** — Status: RETIRED; PR: none; Trigger: legacy anonymous-agent compatibility deliberately retains the sentinel; reopen only with a caller-supplied stable identity contract and migration plan
- [x] **#36** — Status: SHIPPED; PR: PR #903; Trigger: all 27 topic skills audited and drift corrections merged; reopen on a new failing skill-drift receipt
- [x] **#37** — Status: SHIPPED; PR: PR #908; Trigger: grammar-dependent Windows test marked and merged; reopen on a current supported-environment failure
- [ ] **#48** — Status: CEO_GATED; PR: none; Trigger: CEO decision on native-front-door startup architecture
- [ ] **#72** — Status: CEO_GATED; PR: none; Trigger: CEO approval for a fresh public benchmark claim
- [ ] **#77** — Status: CEO_GATED; PR: none; Trigger: CEO decision on the #77/F9 ledger-enforcement scope
- [ ] **#89** — Status: BLOCKED; PR: none; Trigger: WSL-to-Windows path-domain reproduction (path_not_found on existing /mnt/c); needs a real WSL host, still genuinely unmet (re-verified 2026-08-23). **CORRECTION 2026-08-24:** the "owned by Task 2A→2B typed-path" / draft PR #966 attribution below is STALE. A 4-agent parallel analysis of branch `task2a-round60-red` (the scaffold PR #966 was built from) found its actual ~15,000-line content is Windows installer/trust hardening (Job Object containment, CNG signing, Authenticode/WinTrust, SDDL/DACL, an installer receipt schema, a CI-evidence-escrow receipt schema) -- it does not implement or test WSL-to-Windows path translation anywhere in its 26-file diff. PR #966 was already closed as stale by the CEO on 2026-08-20; the branch is RETAINED but was never advancing #89's actual blocker. Do not resume work on #89 by reviving this branch -- it needs a fresh, correctly-scoped plan against a real WSL host instead.
- [ ] **#90** — Status: BLOCKED; PR: none; Trigger: WSL raw-path scan matched_rules=0 while translated-path control total_matches=6; doctor half shipped PR #571; scan half waits typed-path + real WSL CI. **CORRECTION 2026-08-24:** same stale "same Task 2A→2B/2C program as #89 via draft PR #966" attribution removed -- see #89's correction note above, applies identically here.
- [x] **#109** — Status: SHIPPED; PR: PR #605; Trigger: CUDA implicit-walk ceiling merged; reopen on a current parity regression
- [ ] **#131** — Status: CEO_GATED; PR: none; Trigger: CEO decision on publishing GPU-flavor native assets
- [ ] **#169** — Status: CEO_GATED; PR: none; Trigger: CEO approval for physical GPU proof or spend
- [ ] **#255** — Status: DEMAND_GATED; PR: none; Trigger: demand for a bounded many-pattern dedup parity experiment or approved compression/native investment; re-derived 2026-08-14 (docs/audits/2026-08-13-demand-gated-dispositions.md W5A — max single-pack anchors 35 of 129 total across six never-unionable packs, no named 100+-pattern user; both reopen arms unmet; LEAVE)
- [x] **#859** — Status: SHIPPED; PR: PR #913; Trigger: Task 3 class-level AST writer census and anchored publication fix; instance fix and class-level ratchet both merged, and the census found a 4th violation beyond the expected 3 (fixed in PR #918); Implementation PRs: PR #913; Closure PR: PR #920; Merged SHA: 211d850c7484f6e18d74e2d8ac712f118f3b82cf
- [ ] **F5** — Status: BLOCKED; PR: none; Trigger: Task 8 edit-ready Steps 3–5 blocked on rust_core/** + tests/e2e/** (shared-box cargo/e2e ban → CI/cloud); Step 2 shipped #943
- [ ] **F6** — Status: BLOCKED; PR: none; Trigger: Tasks 6-7 edit-verification remainder BLOCKED with a MIXED disposition (A41, corrected 2026-08-12 per the roadmap ground-truth): Python/schema/evidence-signing slices are buildable-first (S1 de-block move), while native verify-edit + e2e halves stay blocked to CI/cloud under the shared-box ban; Step 0 shipped #939
- [x] **F7** — Status: SHIPPED; PR: PR #957; Trigger: Tasks 10-11 language-registry and cross-file resolution; Task 10 complete (Java/C#/PHP/C/C++ in-file refs); Task 11 waves 1-3 shipped (Java #950, PHP #952, C# #955, C/C++ #957); Implementation PRs: PR #950, PR #952, PR #955, PR #957; Closure PR: PR #963; Merged SHA: 9f854d49c7625ec19a5c469f43544b980a0cddff
- [ ] **F8** — Status: BLOCKED; PR: none; Trigger: Tasks 12-13 workspace program blocked on rust_core/src/main.rs + path_domain.rs + tests/e2e routing parity (shared-box ban → CI/cloud) **[SUPERSEDED 2026-08-23]** This receipt cites `rust_core/src/path_domain.rs`, which does NOT exist on origin/main (`git cat-file -e origin/main:rust_core/src/path_domain.rs` fails; `grep -rln "path_domain\|PathDomain" rust_core/src/` returns nothing, with a positive control confirming the grep works). The shared-box cargo/e2e ban may still block this row, but the EVIDENCE as written is stale -- re-derive the real touch points before treating this row as scoped.
- [ ] **MCP-SURFACE** — Status: BLOCKED; PR: none; Trigger: Task 4 MCP surface disclosure blocked on Task 2C; live `_TG_MCP_SERVER_CONTRACT_VERSION` is 1.7.0; Task 4 plans 1.8.0→1.9.0 and must not bump from a nonexistent base **[SUPERSEDED 2026-08-23]** Re-derived: the contract version is a PYTHON one-liner at `src/tensor_grep/cli/mcp_server.py:188` (`_TG_MCP_SERVER_CONTRACT_VERSION = "1.7.0"`), and `rust_core/src/` carries ZERO references to it (control: the same grep finds `fn main` in `rust_core/src/main.rs`, so it is not a dead probe). The 1.7.0 base and the Task-2C SEQUENCING may still hold, but this row is NOT rust/shared-box blocked.
- [x] **CPU-BACKEND** — Status: SHIPPED; PR: PR #925; Trigger: Task 5 Rust and Python backend hardening; Python half removed the TypeError retry that silently dropped invert_match (#923); Rust half hardened public replace_in_place (#925); Implementation PRs: PR #923, PR #925; Closure PR: PR #963; Merged SHA: f29c948440d8039aceb12cd32a4d539730b0161d
- [x] **REF-CALL-REGISTRY** — Status: SHIPPED; PR: PR #940; Trigger: Task 9 registry-driven refs/callers; implementation #915 plus seam pin #940; Implementation PRs: PR #915, PR #940; Closure PR: PR #963; Merged SHA: 3dbe85b127003ee0464ac3cbe1ba6831bd49fdcc
- [x] **F10** — Status: RETIRED; PR: none; Trigger: 2026-08-05 caller/installability census (docs/BACKLOG.md dated entry) found MaxSim reachable only via an undocumented `TG_LATE_RERANK=1` env var + a manual `python -m tensor_grep.core.retrieval_late --fetch` (no `tg` command provisions it) AND decisively negative on the golden set even after the role-aware encoding fix (ndcg@10 0.068 vs 0.305 RRF, root cause model capacity); reopen only on both a `tg`-command install path and a stronger encoder clearing the golden-set gate
- [x] **DD-004** — Status: RETIRED; PR: none; Trigger: 2026-08-05 retirement receipt (docs/BACKLOG.md dated entry) — INFO/WEAKENED loud `RuntimeError` re-raise at `cpu_backend.py:811` is not empty-success; typed boundary already banked in AGENTS.md Backend Fail-Closed Contract; reopen only if a backend path returns clean empty success on real failure or a consumer requires uniform `BackendExecutionError` typing across that site
- [ ] **DD-006** — Status: DEMAND_GATED; PR: none; Trigger: measured concurrent daemon load or denial-of-service evidence; DEMAND CONDITION NOW SATISFIED by the 2026-08-14 bounded probe (20 clients x 60s, control-valid single-shot arm 20/0; 5 of 5 live arms showed timeouts at the CLI's own 0.5s budget, the one discriminated arm classified them connect-timeout, 0 refusals/drops, 0 failures at a 2.0s budget and single-client; soft accept-path latency under concurrency, mechanism hypothesis = default request_queue_size=5 backlog; see docs/audits/2026-08-13-demand-gated-dispositions.md W5B). Design packet merged PR #1015 (`0710219`; Sol APPROVE; Fable waived for docs only). Reopen on: deliberate authorization for product DD-006-PERF + DD-006-HONESTY build (TDD + A3); per A122 docs alone do not SHIP the parent row; reproduction remains the demand trigger
- [ ] **AST-DSL-PARITY** — Status: DEMAND_GATED; PR: none; Trigger: demand for full structural DSL parity and a preprocessor-aware oracle; Exa delta 2026-08-14 (ast-grep Rust tree-sitter rewrite shipped ~22% end-to-end faster; NL-to-DSL arXiv 2507.02107; peers reach for DSL/parity, not metavariable performance; zero perf-blocked consumer; LEAVE)
- [ ] **MCP-LEAN-DEFAULT** — Status: DEMAND_GATED; PR: none; Trigger: client demand and compatibility evidence for changing the default surface; Exa delta 2026-08-14 (lean-by-default is now SPEC-LEVEL — official MCP client-best-practices codifies progressive discovery/catalog-inspect-execute and programmatic tool calling, up to 85% token reduction; AWS 8-param guidance); direction confirmed but STILL SEQUENCED AFTER Task 2C per the MCP-SURFACE ladder (contract version 1.7.0 re-verified on origin/main)
- [ ] **CONTINUOUS-REFRESH** — Status: DEMAND_GATED; PR: none; Trigger: approved scoping/design pass for a warm search-index service (not a build); Exa delta 2026-08-14 (warm serving now demonstrably table stakes — TriSeek v0.4.2 multi-harness warm daemon + memo layer, cgh watch-first code graph, seekr, Cursor cloud-agent warm builds; the one-global-daemon-many-roots shape is the peer pattern; scoping-pass reopen stands)
- [x] **RUST-REPLACE-SYMLINK** — Status: SHIPPED; PR: PR #1010; Trigger: the untrusted-destination threat model is evidenced by fresh 2026 CVEs (sed CVE-2026-5958; uutils GHSA-239g-2685-54x3; Capgo CVE-2026-56236; receipts in docs/audits/2026-08-12-research-receipts.md); design council (3 rounds, GATE-W3A-1 = (a) REFUSE via bounded toolchain probe) resolved the downstream compatibility decision no-follow-by-default; PR #1010 shipped the fail-closed symlink_metadata guard (covers symlinks and Windows junctions) + root refusal + residual-race characterization pin; Merged SHA: d31a051f7b3affa67fa1021c2380538d59cce2df; released v1.110.16; guard + pin verified on origin/main post-merge (A29)
- [ ] **RUST-REPLACE-TOCTOU** — Status: DEMAND_GATED; PR: none; Trigger: residual `replace_in_place` races and a static no-race ancestor bypass: (1) the leaf stat-vs-open swap window (pinned OPEN by the characterization pin in `backend_cpu.rs` — that pin INVERTING is this row's acceptance signal), (2) walk-time child swap between enumeration and pathname re-open, (3) a symlink in a non-leaf path component (leaf-only `symlink_metadata`), (4) the directory-ROOT swap window (guard stat to `is_dir()` — the widest blast radius; needs a SECOND swap-gate fire point between the guard and `is_dir()` when built, since the existing pin covers the leaf only); candidate machinery: `O_NOFOLLOW` / `FILE_FLAG_OPEN_REPARSE_POINT` (already implemented in `rust_core/src/safe_write.rs`; applying it at the two open sites converts a raced child follow into an `Err` and is safe for legitimate use because walkdir already filters static symlink children) applied at the replace open sites, or component-wise handle-relative opens

## Live campaign snapshot

Last reconciled: **2026-08-20** (backlog-closeout campaign waves W1-W4: plan council-approved 7/7; W1 receipt + W2 A101 probe-retry + W3A threat model + W3B symlink guard shipped across v1.110.15 / v1.110.16; W4 Task 2A repair round 1 parked with receipt on #966). canonical index `2026-08-13.1`. Task 2A RED remains correctly blocked (advanced, not GREEN). (2026-08-19/20 size-and-hygiene campaign: 10 PRs merged — wave 1 and wave 4 splits, the file-size and bare-call ratchets, the drain-guard and ripgrep-hang CI fixes, the split-floor costing correction, plus Route A step 2 on mcp_server (#1040, in flight). Allowlisted oversized files 35 → 30. Released v1.110.19 and v1.111.0. Repo hygiene: worktrees 58 → 2, local branches 174 → 2, remote branches 13 → 4 — everything removed was archived first to `~/.tensor-grep-worktree-archive/2026-08-20` as patches, untracked-file copies, and two verified git bundles.) **[SUPERSEDED 2026-08-23]** Canonical index `2026-08-13.1` is stamped four minors behind (tag now v1.113.0) and asserts **0 IN_FLIGHT** while PRs #1102 and #1105 are open. It also counts 5 CEO_GATED while this file's own 2026-08-22 campaign note names SIX (`#48 #72 #77 #131 #169` + RULESETS). Re-derive the index from live state before relying on any of its three numbers.
research receipts). canonical index `2026-08-12.1`. Task 2A RED remains correctly blocked
(advanced, not GREEN).
Execution plans (historical, all shipped): `docs/plans/2026-08-10-a90-unknown-command-fail-closed.md`
(#997 → v1.110.13) and `docs/plans/2026-08-08-backlog-completion-plan.md` (M16/M17 wave).

**Public product:** `v1.110.16` on PyPI/GitHub (2026-08-13; v1.110.15 = A101 probe retry #1009, v1.110.16 = RUST-REPLACE-SYMLINK #1010; both tag + release assets + PyPI verified). **[SUPERSEDED 2026-08-23]** The current published tag is **v1.113.0** (`git describe --tags --abbrev=0 origin/main`), four minors on from the v1.110.16 stated here. This line is a dated receipt and is left intact per the append-only rule; trust the tag.
The closeout audit wave, Rust first-CI drain, A90, and doctor PATH-honesty all landed: H2 #979 → v1.110.10,
M14 #984 → v1.110.10, #987 M16 → v1.110.11, #988 M17 → v1.110.12, #997 A90 → v1.110.13, #1000 doctor →
v1.110.14. Docs: #993 (roadmap), #994 (A90–A93), #992 (A87–A89 capture), #995 (M16/M17 receipts),
#999/#1001/#1002 (skill evolution + new skills). Open PRs derived live 2026-08-20 (post-fan-out): NONE — #1040/#1042/#1043 (Route A, all three giants) and the 11-PR subagent campaign (#1044–#1053) all MERGED; giants now main.py 13,523 / repo_map.py 15,243 / mcp_server.py 5,341, tri-split union GREEN on main. #966 was CLOSED as a stale PR at CEO request; its branch `task2a-round60-red` is retained on origin and the scaffold is unchanged -- the Task 2A rows below still gate #89/#90.
**CEO packet:** `docs/audits/2026-08-15-ceo-backlog-update.md` (live, 2026-08-15; dumbed-down). Prior: `docs/audits/2026-08-13-ceo-backlog-update.md` (campaign closeout) and `docs/audits/2026-08-11-ceo-backlog-update.md` (historical).
morning packets retained as historical (A77–A82 receipts + pre-ship counts).

**Unfinished (buildable queue):** **0 READY rows**; 0 IN_FLIGHT (RUST-REPLACE-SYMLINK SHIPPED via PR #1010 / v1.110.16). Next buildable: none on the board (A82 holds; roadmap spine remains the queue).
(PR #1010, design-council pass
first; flipped 2026-08-12 on CVE-class evidence carried in the row). The rest of the forward work
is the world-class roadmap spine per `docs/plans/2026-08-09-worldclass-roadmap.md` — F6-scoped,
with the 2026-08-12 correction that F6 is MIXED (Python/schema/signing slices buildable-first;
rust/e2e halves CI/cloud). Blocked/CEO/demand rows: 6 BLOCKED (#89 #90 F5 F6 F8 MCP-SURFACE),
5 CEO_GATED (#48 #72 #77 #131 #169), 6 DEMAND_GATED (#255 DD-006 AST-DSL-PARITY
MCP-LEAN-DEFAULT CONTINUOUS-REFRESH RUST-REPLACE-TOCTOU). Board READY is not a build license when BACKLOG reconcile
says BLOCKED (A71/A76).

**Hard stops:** Task 2A not merge-ready (#966 RED by design); no #169 spend; no silent CEO-gate flips;
MCP wire-contract fence; no local `rust_core` cargo on the shared box for W3 halves.

**Unfinished 17:** 0 READY, 0 IN_FLIGHT, 6 BLOCKED (#89 #90 F5 F6
F8 MCP-SURFACE), 5 CEO_GATED (#48 #72 #77 #131 #169), 6 DEMAND_GATED (#255 DD-006 AST-DSL-PARITY
MCP-LEAN-DEFAULT CONTINUOUS-REFRESH RUST-REPLACE-TOCTOU). SHIPPED now stands at 8 (RUST-REPLACE-SYMLINK d31a051, v1.110.16). (#966 is a parked RED scaffold - advanced
2026-08-12 to MERGEABLE with a live evidence chain, still not an implementation in flight). Board
READY is not a build license when BACKLOG reconcile says BLOCKED (A71/A76/A82).

post-**v1.113.3**, PyPI-verified 2026-08-23 (4/4 BY FILENAME: `macosx_11_0_arm64` + `manylinux_2_39_x86_64` + `win_amd64` wheels + sdist; the 1.113.2 control returned 4/4 through the same parser BEFORE the target was queried, so the verdict was falsifiable rather than a bare version-presence check). Reconciles the 2026-08-23 session: **v1.113.1 / v1.113.2 / v1.113.3 published**, release run `32647488726` completed/success with every job green including `release-tag-smoke`. Merged: #1100, #1103-#1109 earlier, then #1110 (`90993f4`, cursor scratch off main), #1111 (`1ef8dd4`, the `_add` trap measurement), #1112 (`c48d6bd`, the #72 premise correction) and #1102 (`7e06a16`, blast-radius-render deadline parity -- a `fix:` that publishes v1.113.4). #1113 (report-only governance-doc size visibility) open. **#72 IS RESOLVED AS A PREMISE ERROR:** the board asserted "public wording still needs approval", but the benchmark number is on NO public surface -- root README, `rust_core/README.md` (the PyPI long description, where `pyproject`'s `readme =` actually points, NOT the root README), `pyproject.toml` and the GitHub About blurb are all clean, and `include = ["LICENSE", "NOTICE"]` means `docs/` never ships. Nothing public to withdraw; the gate is on PUBLISHING a number, not retracting one. Also corrected: F8's receipt cited a file that does not exist, and MCP-SURFACE is Python-only rather than rust-blocked. Skill library audited across all 37 skills (gates 9 passed; every stale version stamp carries a caveat, none re-stamped without a re-run). Re-derived rather than hand-counted: 10 parser-backed / 0 foundational languages, 10 distinct tree-sitter packages, mcp maintenance probe `MAINTAINED` (T1/T6 clear, pin-and-defer holds). CEO gates #48/#72/#77/#131/#169 + RULESETS remain **CEO_GATED** -- no status flips.

post-**v1.111.6**, PyPI-verified 2026-08-22 (`tensor-grep 1.111.6` via `uvx --refresh-package`, and dogfooded both arms against the published wheel: `tg defs src missing_scan_paths --json` resolves 1 symbol to `cli/scan_guardrails.py`, and `tg refs src ZzzNotARealSymbolXyz --json` returns `no_match=true, not_found=true, files=[]`). Reconciles the 2026-08-21/22 session: **v1.111.3 → v1.111.6 published, each verified per-artifact (4/4: 3 wheels incl. `win_amd64` + sdist)**, closing the tagged-but-unpublished gap that left `v1.111.2` with ZERO PyPI files and `v1.111.1` with 2 of 4. PYPI-SIZE-CAP cleared (713 → 287 releases, 10.734 → 4.747 GB). Product fixes shipped: scan fail-closed on a missing root, ast-grep remediation text, the inline-rules `engine:` key, `tg find --json` routing fields, the `cli_modes` globals shim, the ruleset-availability disclosure, and the index-lock wall-clock flake that was skipping whole release chains. CI fixes: the `code` path filter now watches `docs/audits` (a ledger-only PR was skipping every `test-python` lane — including the test that reads that ledger), and the docs-governance gate no longer requires a sentence that was false. CEO gates #48/#72/#77/#131/#169 + RULESETS remain **CEO_GATED** — council-recommended and Exa-grounded only, no status flips.

post-**v1.111.0**, PyPI-verified 2026-08-20 (`tensor-grep 1.111.0`, installed from the index with `--refresh` and dogfooded both arms: a real symbol resolves, a fabricated one returns `no_match=True`).

post-**v1.110.12**, PyPI-verified 2026-08-10 (`tensor-grep 1.110.12`).

## IN FLIGHT (PRs open right now — derived from `gh pr list`, 2026-08-13)

| PR | Title | Type | State |
|---|---|---|---|
| #966 | `test: Task 2A FIX-FIRST Sol R3 (not GREEN)` | test | CLOSED 2026-08-20 as a stale PR; branch `task2a-round60-red` RETAINED on origin. RED by design, never merged. Task 2A remains BLOCKED. |
| #1010 | `fix: replace_in_place refuses to follow a symlinked target` | fix | DRAFT → OPEN when CI green (RUST-REPLACE-SYMLINK implementation; RED proven, A3 opus gate 13 rounds SHIP, codex AUDIT-clear pending final re-check) |

*(Derive live `gh pr list` before treating this table as current. #997 (A90 → v1.110.13) and #1000
(doctor → v1.110.14) MERGED; #992/#993/#994/#995/#999/#1001/#1002 docs merged. Open PRs 2026-08-20 post-fan-out: NONE (#1040–#1053 all merged). #966 CLOSED (branch retained; Task 2A still BLOCKED, not GREEN); #1010 (RUST-REPLACE-SYMLINK) MERGED, v1.110.16.)*

*(#872, #871 and #868 all MERGED — #871 on 2026-07-31, #872 and #868 on 2026-08-01. They sat in
this table as "CI running" / "BLOCKED — do not merge" after landing, which is the exact failure mode
described above: a board that says BLOCKED about shipped code will eventually stop someone from
merging something correct.)*

### Task 2A plan gate (owned by #89/#90; not shipped / not merge-ready)

Round-60 plan approval stands. Local RED at `6367614...` is Sol `FIX-FIRST` (10 HIGH): no real
immutable-SHA Windows CI; runners treat crash/setup as behavioral RED; hardcoded PCRE2 construction
oracle outside census; parent-forgeable Job heartbeat + multiline ambiguity; default Job cleanup not
independently proven; SDDL accepts unknown/inherit-only/garbage; CNG export invalid flag without
exportable positive control; TxR omits exact close ownership; Python producer self-attests before
start; public `-f`/`--file` GREEN unbounded read before ledger. Retained: Counter/census/job/vector/
Cargo binding; foreign-chain/catalog fixtures; scoped rg/sidecar overrides; discoverable close
ownership. After #911 merges and base proof lands, repair those ten blockers → Sol `SHIP` → push draft
→ real Windows CI. #89/#90 remain `READY`; approval and a blocked RED are not implementation.

---

## P1 — external dogfood findings (a real user hit these)

- [x] **Anonymous `--claim`** — RESOLVED, and the resolution is the load-bearing part: the sentinel
  STAYS. An adversarial audit killed the auto-derive option with a receipt — `_find_overlaps`
  suppresses when `new.agent_id != _DEFAULT_AGENT_ID and entry.agent_id == new.agent_id`, so two
  zero-config agents sharing a *derived* id would silently drop each other's overlaps, reproducing
  #845 by a new mechanism in the ledger's primary use case. Nor can any derivation escape it: a
  per-checkout id conflates agents, a per-process id is not stable across one agent's calls. Agent
  identity is not derivable from the environment. The SIGNAL got louder instead (`NOT attributable`
  + a machine-branchable `agent_id_is_anonymous`). Pinned by
  `tests/unit/test_anonymous_claim_signal.py`. *Task #13/#23.*
- [x] **Ledger Slice 2 rollup parity** — RESOLVED (`record`/`find` now canonicalise through
  `_ledger_physical_root`). *Task #14.*
- [x] **MaxSim late-rerank is advertised but unexercised** — RESOLVED. Both named defects are fixed: `find_command`'s docstring now states plainly that MaxSim is NOT reachable by a documented path, and `tests/unit/test_find_command.py` asserts the real ORDER inversion rather than only `exit_code == 0`. Verified 2026-08-01. ORIGINAL TEXT FOLLOWS — — *Task #15.* **Now decidable, and the
  answer is DOC-HONESTY, not CUJ coverage.** `tg install-dense` installs `tensor-grep[semantic]`;
  MaxSim needs a DIFFERENT extra (`rerank`) whose model is fetched by
  `python -m tensor_grep.core.retrieval_late --fetch` — **no `tg` command reaches it**. A CUJ would
  first have to build an install path that does not exist, for a stage deliberately held as
  measurably regressing. Two real defects to fix instead: (a) `find_command`'s docstring advertises
  MaxSim while the only control is the undocumented `TG_LATE_RERANK=1`; (b)
  `tests/unit/test_find_command.py` claims to prove the stage "DOES probe/run … observably
  reordering" but asserts only `exit_code == 0` / non-empty / no-exception — **all three hold with
  the env unset**. Its stub already inverts the ranking, so the fix is a one-line ORDER assertion
  with a genuine red arm. `docs(find):`, non-releasing.
- [x] **Bare `tg search P --json` with no PATH** — RESOLVED across all routes. Four dispatch routes
  and then FIVE JSON emitters, fixed one at a time over four releases because each fix closed the
  route that happened to be reported. Both populations are now held by enumerating tests
  (`test_every_search_dispatch_route_discloses.py`,
  `test_scope_note_covers_every_json_emitter.py`). The fifth emitter is the lesson:
  `normalize_gpu_sidecar_json` builds the document by hand with `serde_json::json!()`, so a census
  keyed on `#[derive(Serialize)]` reported "4 of 4 covered" and was wrong by one. **Enumerate
  EMITTERS, not the mechanism they happen to use.** PR #871.
- [x] **GPU exit-2 calibration — RETIRED 2026-08-02.** The canonical ruling is: exit `0` is a
  complete result with matches, exit `1` is a complete no-match, and exit `2` means incomplete.
  An unhonoured GPU request remains disclosed in-band and does not independently force exit `2`.
  The stale investigation is retained below as historical context, not active work. ORIGINAL TEXT
  FOLLOWS — **BLOCKED, twice over.** *Task #22.* (1) The CAUSE of #868's CI
  failure is still unknown. A two-arm control proved that IF `resolve_native_tg_binary()` returns a
  path, `main.py:7877` `sys.exit`s ~530 lines before #868's rule at `main.py:8408`, reproducing CI
  byte-for-byte — but `.github/workflows/ci.yml:688` says `test-python` never builds that binary, so
  the mechanism is *sufficient*, not *confirmed operative*. A CI-only diagnostic with a positive
  control is in flight. (2) The PREMISE is contract-contested: exit 2 means INCOMPLETE, and that
  search ran to completion and returned its match; the capability is already in-band via
  `native_gpu_unavailable` / `gpu_evidence_status`. Needs a contract decision, not more code.

## P2 — audit queue (deep audit `wf_38d4b580-d89`, 2026-07-28)

Six read-only lenses — security, CI/release workflow, disclosure edge cases, dead/unwired code,
test trustworthiness, scale-correctness — each finding adversarially verified before it lands here.

That placeholder sat unfilled for three days. Replaced with the items from the 2026-07-31 plan
review, each verified against the real code (a finding without a `file:line` was discarded):

- [x] **CWE-88 argv sentinel — a live hole in a sweep recorded as CLOSED.** `#20`-B2.
  `agent_capsule.py::_agent_gpu_evidence` appended a caller-supplied `evidence_path` as a bare
  positional; clap's `path` (`rust_core/src/main.rs:694-695`) has no `allow_hyphen_values`, so a
  dash-leading path becomes an OPTION and the probe queries a scope nobody chose — **still
  reporting `ok`**. #860 fixed the sibling and the class was marked done; this site was in nobody's
  grep. Now held by `tests/unit/test_argv_sentinel_covers_every_builder.py` (5 builders, by symbol).
  PR #872.
- [x] **`--quiet` silently dropped by both internal rg-passthrough branches** — RESOLVED by `cfc3264`, which moved `-q` out of the shared `_build_cmd` into streaming-only `search_passthrough` (`backends/ripgrep_backend.py`); `test_quiet_survives_rg_passthrough.py` covers both arms. **This entry sat OPEN for months after the fix and is the worked example in `docs/audits/2026-08-01-task-board-staleness.md`** of why a citation gate cannot catch content drift: its `main.py:7937-7943` citation still resolves perfectly. ORIGINAL TEXT FOLLOWS — (`main.py:7937-7943`,
  `:8004-8017`; zero "quiet" mentions in `ripgrep_backend.py`). A flag the caller passed that the
  chosen engine ignores, with no disclosure — the silent-downgrade class.
- [x] **RETIRED, not fixed: the two `--gpu-device-ids` gates are NOT in contradiction.** The entry
  claimed `_can_passthrough_rg` excluding the flag and `_can_delegate_to_native_tg_search`
  including it could not both be right. They can, because they gate DIFFERENT CALLEES:
  - `_can_passthrough_rg` (`main.py:5467`) hands the query to **rg**, which has no GPU. Excluding
    the flag is what stops a silent CPU downgrade at exit 0 with no `fallback_reason` -- its own
    comment says exactly that.
  - `_can_delegate_to_native_tg_search` (`main.py:3813`) hands it to the **native tg binary**,
    which DOES accept `--gpu-device-ids` (declared at `rust_core/src/main.rs:395` and `:650`, and
    forwarded by `_build_native_tg_search_command:3828-3833`).

  So each gate routes GPU work AWAY from the engine that cannot do it and TOWARD the one that can.
  That is the same policy expressed twice, not two policies fighting.

  **The finding was a category error: same flag, different callees.** It survived two passes
  because both sites mention `gpu_device_ids`, and a grep that matches the same identifier in two
  places looks like a contradiction until you read what each one is gating. Recorded rather than
  silently deleted -- a documented retirement is worth as much as a fix (board rule 4), and this
  one would otherwise be re-derived every time someone greps that flag.
- [x] **`--ndjson` zero-match discloses nothing in-band in EITHER engine** — RETIRED, not a defect. The Python emitter deliberately stays silent on a COMPLETE zero-match so readers are not trained to expect a record every run; the Rust divergence is documented at `core/json_fmt.py` (see the comment above the summary emitter). Verified 2026-08-01. ORIGINAL TEXT FOLLOWS — The summary record now
  carries the scope note (#871), but a zero-match `--ndjson` still emits no reason field.
- [x] **The three `main.rs` envelope literals have no direct test** — PARTIALLY REFUTED. Two of three carry serialization tests; the third is `#[cfg(feature="cuda")]` and its exclusion is justified in-code (`cuda-feature-check` runs `cargo check`, not `cargo test`). Verified 2026-08-01 — the finding as written overstated the gap. ORIGINAL TEXT FOLLOWS —, and the CUDA one is
  type-checked but never executed anywhere in CI (`cuda-feature-check` runs `cargo check`, not
  `cargo test`). Checking is not running.
- [x] **THE POPULATION IS THE RECURRING DEFECT, NOT THE ASSERTION.** #872's enumerating test had
  its population wrong **three times in one day** — 5 members, then 8, then 10 — and every miss was
  the same judgement: *"builder A transitively covers builder B."* Each was disproved the same way,
  by deleting B's guard and watching the suite stay green. The third miss,
  `ast_wrapper_backend.py::_build_command`, is the only one in the sweep whose regression is
  **destructive**: a caller-supplied path of `-U`/`--update-all` reaching ast-grep's `run`
  subcommand is its auto-fix switch, so a read-only scan becomes a file rewrite. Two derived rules,
  now in the test's own docstring: **do not add a member by reasoning it is covered — call it**; and
  **a guard whose placement is config-conditional has as many members as it has configurations**
  (the run form's sentinel lives in the `else:` of `if stdin_enabled`, so the default arm alone was
  sampling). A third, from the same review: **a COUNT is blind to an ORDER SWAP** — two members
  asserted `len(tail) == 2` and passed when the pattern and path were exchanged, which in production
  searches a directory named like the pattern for a pattern that is the temp path.
- [x] **`AGENTS.md:1437` is stale on the argv sweep** — REFUTED; already corrected. AGENTS.md now records the sweep-is-now-a-test conversion and the `_agent_gpu_evidence` hole #872 found. Verified 2026-08-01. ORIGINAL TEXT FOLLOWS — — it describes the class as swept while #872
  was open against it. A doc that certifies a sweep it cannot verify is the prose-rung failure this
  board keeps re-learning.

## P3 — strategic / positioning (informed by Exa competitive research, 2026-07-28)

- [x] **Articulate the policy layer as the moat -- CLOSED 2026-08-02.** The item's actual ask was *"tool_comparison.md still positions tg mostly as a search comparator; reframe around one-call edit readiness"* -- delivered by PR #899: the doc now leads with an axis split and carries "One Call To Edit Readiness" + "What The Answer Says When It Could Not Finish", and README mentions edit readiness 4x (was 0). All three falsified claims were kept OUT: 0 occurrences of "market consensus", 0 of "escalat" (PR #900 dropped even the honest `ask_user_before_editing` usage, because our own research said the word invites a rebuttal), and no claim that `prepare` fuses the semantic leg. **RESIDUAL IS A CEO DECISION, NOT WORK:** whether to BUILD an escalation layer or leave the word dropped. Tracked on the CEO-gated list, not here -- an item whose only remaining step is a decision is not an eligible work item, and leaving it open sends agents at finished work. ORIGINAL TEXT FOLLOWS --  — FRAMING CORRECTED 2026-08-01, read `docs/positioning/2026-08-01-policy-layer-moat.md` BEFORE writing any copy.** Verdict: PARTLY TRUE. `tg prepare` genuinely is rare (no surveyed tool bundles target + confidence + blast radius + validation commands in one call), and budget control is real (measured: `--deadline 0.1` -> exit 2, `partial: true`, confidence downgraded 0.9 -> 0.72 with named reasons). BUT three parts of the quote below are NOT ours to say: (a) "the 2026 market consensus" is ONE single-author blog post whose own numbers are self-labelled *illustrative, not measurements*; (b) **tg ships NO escalation** -- 2 occurrences in tracked code, both unrelated (control: `fallback` = 725); `docs/routing_policy.md` is an ENGINE router, not a retrieval-strategy escalation policy; (c) `tg prepare` does NOT fuse the semantic leg -- `agent_capsule.py` imports zero of bm25/dense/fusion (control: it does import `retrieval_lexical`). **Fix the product or drop the word; do not ship the quote.** ORIGINAL TEXT FOLLOWS — The 2026 market consensus is that lexical +
  structural + graph are all table stakes and *"there is no shortage of tools, there is a shortage
  of POLICY — the orchestration layer that combines all three with escalation and budget control is
  the real gap."* `tg prepare` and `tg agent` ARE that layer, and `docs/tool_comparison.md` still
  positions tg mostly as a search comparator. Reframe around one-call edit readiness.
- [x] **Name incompleteness-honesty as a differentiator -- CLOSED 2026-08-02, PR #899** at the NARROW defensible width, not the falsified absolute. `tool_comparison.md` now carries "What The Answer Says When It Could Not Finish": exit-code/payload agreement with a rerunnable two-arm receipt, `budget_remediable()`'s fail-closed allow-list, contract-level scope + CI ratchets -- and GitHub's `incomplete_results` and LSP's `isIncomplete` NAMED as prior art rather than denied. Verified: 0 occurrences of "market consensus" in the doc. ORIGINAL TEXT FOLLOWS --  — CLAIM NARROWED 2026-08-01.** The contract is real and deep (`result_incomplete` at 124 Python + 42 Rust sites; `incomplete_reason_class` at 82 + 3; `budget_remediable()` is a fail-closed allow-list). BUT "no competitor documents such a contract" is **FALSE** and would lose on inspection: GitHub's REST Search API ships a REQUIRED `incomplete_results` boolean with near-identical doc language, and LSP ships `CompletionList.isIncomplete`. The defensible narrower claim (no counterexample found): exit code AGREEING with payload + a closed reason-class vocabulary WITH a remediability verdict + contract-level scope with two CI ratchets -- and MCP has not standardized this at all. Also note we do NOT have "one machine-branchable field": at least three vocabularies exist and CONTRACTS.md #293 defends the split deliberately. ORIGINAL TEXT FOLLOWS — No competitor surveyed (Gortex, Serena,
  claude-context, grepai, CodeGraph, Sourcegraph, Augment) documents a contract of the form
  *"a surface that cannot finish must say so, in a machine-branchable field, with the exit code
  agreeing."* agentmako's freshness labels (live/fresh_indexed/stale/contradicted/unknown) are the
  nearest analogue and are weaker. This is a real moat and it is currently invisible outside the repo.
- [x] **Token-economics is the category's scoring metric — RESEARCH COMPLETE, publication decision
  owned only by canonical #72.** Competitors publish token-reduction
  numbers (grepai 97% input-token cut, CodeGraph ~70% fewer tool calls, GitNexus 88%, Gortex 3–50×).
  tg's own measured **7.5× fewer tokens than grep** is the same metric family. Publication is
  The publication decision is not duplicated here; see the canonical status index.
- [x] **Language coverage gap, stated honestly -- SHIPPED #902 (2026-08-02).** Closed by the
  premise check, not by new work: `docs/tool_comparison.md` on `main` already carries the
  two-tier table (both tiers computed live from the language registry, with a "re-derive this
  table; do not trust it" note), the competitor breadth numbers, AND the harder 2026-08-01
  revision this entry was updated to demand -- a **"Where other tools are ahead, stated
  plainly"** section conceding roughly **5 vs 30 on the deep tier, in Gortex's favor**, and the
  explicit framing that tiered disclosure is *table stakes, not a differentiator*. Nothing was
  left to dispatch. 18th stale entry this session; found by running Step 0 of
  `verify-plan-against-code` (is the work still needed?) before dispatching an agent at it.
  The 257-vs-256 discrepancy this entry flagged is RESOLVED (Exa, 2026-08-02): **Gortex's own
  docs disagree with each other** -- `docs/languages.md` says "currently indexes 256" and its
  table totals 256, while the README, `docs/features.md` and gortex.dev say 257. Both of our
  numbers had a real source. `docs/tool_comparison.md` now cites the RANGE and names the
  contradiction rather than picking a side. Same self-contradiction class we spent this session
  fixing in our own docs -- worth knowing it is not unique to us. Also re-verified while there:
  their bespoke tier does contain all ten of `tg`'s languages, so our "deep tier covers all ten
  plus roughly twenty more" line is correct as written.

- [x] **`tg prepare` is invisible** -- **CLOSED 2026-08-02, PR #899.** Measured before and after: README `tg prepare` 0 -> 6; `tool_comparison.md` `prepare` 0 -> 5, `incomplete` 0 -> 8 (control: `ripgrep` 6, unchanged). Added the real one-call invocation with a verified payload, plus two new sections. All 15 speed/parity rows left intact -- they are true. ORIGINAL TEXT FOLLOWS --  The capability the board calls the moat appears **zero times in `README.md`** (control: `tg agent` appears 3x) and sits in no mkdocs-nav'd page except `CONTRACTS.md`. `docs/tool_comparison.md`'s 15 data rows are ALL speed or parity: `grep -ci prepare` -> 0, `grep -ci incomplete` -> 0 (control: `ripgrep` -> 6). Surface the product before repositioning around it -- rewriting a comparison table for something nobody can see is the wrong order. Found 2026-08-01, `docs/positioning/2026-08-01-policy-layer-moat.md`.

## P4 — carried backlog (from `docs/BACKLOG.md`, still open)

- [x] **#58** promote `tg route-test` hidden -> public -- **ALREADY DONE, verified 2026-08-01.** `tg --help` lists it (`route-test  Diagnose routing agreement between context-render...`), it is pinned in `PUBLIC_TOP_LEVEL_COMMANDS` (`tests/e2e/test_routing_parity.py:75`), and no `hidden` marker exists on it anywhere in `cli/main.py`. Found by verifying the item BEFORE dispatching work against it -- the 10th stale-open entry this campaign. ORIGINAL TEXT FOLLOWS --  → public
- [x] **#98** MCP tool consolidation -- **KILLED AS WRITTEN 2026-08-02, re-filed as three.** Phase-1 ALREADY SHIPPED 2026-07-17 (`6d8a23e`, v1.81.0: 10 meta-tools + `TG_MCP_LEGACY_TOOLS` + contract bump 1.3.0->1.4.0). The item's own numbers were fiction: "45" was true for two days and was never right after filing (real: 48 pre-consolidation, 58 now), and **`TG_MCP_TOOL_SURFACE=lean` DOES NOT EXIST** -- its only occurrence in the repo is that backlog line. "non-breaking" is true of what shipped and definitionally FALSE of what remains (the default flip removes 46 names from the wire). Re-filed: (a) a contract-disclosure fix -- 58 tools and 12 tools both report `mcp_contract_version 1.7.0`, so a pinning client cannot tell the surfaces apart; (b) the Phase-2 default flip, PARKED (2026 clients solve this client-side via deferred loading); (c) "staleness receipts", KILLED -- the phrase appears nowhere else in the repo. Evidence: PR #894, `docs/investigations/2026-08-01-mcp-tool-consolidation.md`. ORIGINAL TEXT FOLLOWS --  (45 → ~10 task-shaped dispatch tools, non-breaking)
- [x] **#141** native `AstBackend` vs ast-grep wrapper -- **INVESTIGATED AND FIXED 2026-08-01/02.** The divergence is REAL and its failure mode is a SILENT WRONG ANSWER, not a refusal: a bare word that is also a tree-sitter node-type name is read as a structural query by native and as a code-pattern by the wrapper; both return results, neither warns (PR #890, with a lock-in test that asserts native matches lines not containing the token). Two proven defects then FIXED in #892: `tg scan` called a DRIFTED duplicate `_select_ast_backend_for_pattern` missing its sibling's fail-closed guard (collapsed to a forwarding shim -- one implementation, nothing left to drift), and a `cast(ComputeBackend, ...)` bare-name that would `NameError` at runtime inside an `except` handler. Also found: a previously undocumented THIRD AST engine (`rust_core/src/backend_ast.rs`) which already gives the correct answer on the default path, so full DSL parity stays correctly demand-gated. ORIGINAL TEXT FOLLOWS --  — DSL divergence
- [x] **#160** v1.71.3 dogfood feature tail -- **RECONCILED 2026-08-02.** Four of five named sub-features were ALREADY SHIPPED, verified against the live CLI with positive AND negative controls (`suggested_ignore` returns a value on a vendor tree and `None` on a clean repo). The one genuine gap -- `getattr(mod, "Symbol")` bucketed as plain `string-literal` with no dedicated classification -- is closed in PR #893 as a new `getattr-arg` occurrence value. Correctly judged NOT an MCP contract bump: `string_refs[].occurrence` was never a closed vocabulary. ORIGINAL TEXT FOLLOWS --  (`suggested_ignore`, orient auto-deweight)
- [x] **#115** symlink sweep — CLOSED. `docs/BACKLOG.md` already carried the KILLED/CLOSED verdict; THIS BOARD was the stale copy (verified 2026-08-01).
- [x] **#125** checkpoint `except Exception` → `except BaseException` — CLOSED. Same as #115: `docs/BACKLOG.md` was right, this board was the stale copy (verified 2026-08-01).
- [x] **#143 / #155** Opus-gate LOW follow-ups -- **BOTH ALREADY CLOSED, verified 2026-08-02** (PR #891). Six sub-items, each traced to the commit that closed it and every commit confirmed an ancestor of HEAD via `git merge-base --is-ancestor`: `248fa35` (#603) metadata-ownership guard, `81b2148` (#652) bounds all 9 warm-daemon handlers, `e575075` (#604) lru_cache + #155's `path_provenance`, #140/#143 the `--` sentinel. No code change was needed. **Do NOT conflate the closed #143 sentinel item with #862** -- that is a separate, still-open nit at a different `agent_capsule.py` site. ORIGINAL TEXT FOLLOWS --  *(LOW)*
- [x] Dead code: delete `sidecar.py::_classify_lines` *(LOW)* — done 2026-08-01 backlog campaign, PR-D
- [x] apply_policy argv-sentinel — RETIRED (not fixed), 2026-08-01 backlog campaign, PR-D. See
      `docs/BACKLOG.md`'s LOW-severity section for the reasoning.

## UNSHIPPED ARTIFACTS -- found by a worktree sweep, 2026-08-02

Not "open work" someone chose; work that was **invisible** because its branch looked like a husk.
All three are now pushed to origin, so none is disk-only. `git merge-base --is-ancestor` says
"landed" for a branch whose COMMITTED head is on main -- it says nothing about uncommitted files in
the worktree, which is exactly how 519 lines hid behind an "ANCESTOR of main" verdict.

- [x] **`perf/context-tests-limit-deadline` -> SHIPPED as PR #904, merged 2026-08-02 (`8fc51f8`).**
  Rescued from 519 uncommitted lines a worktree sweep nearly deleted; `_context_tests` now emits
  `test_candidates_scanned`/`_total` into `deadline_limit` via a dedicated `_TestScanCounts`.
  Verified on the merged artifact by an AST walk (2/2 call sites carry the counter; impact free of
  `_test_source_limit`), not a substring scan.

  **THREE independent gates found FOUR defects my own verification missed** -- it had reported zero
  regressions across 41 derived files. Root cause of the first three in one sentence: every parity
  arm ran a 32-file fixture where the 2000-file ceiling is UNREACHABLE, the one population where the
  bound cannot fail. Full receipts in `AGENTS.md`, "Three Independent Gates Found Four Defects I Did
  Not".

- [x] **`probe/classifier-feasibility` -- VERDICT ALREADY EXISTED; the INSTRUMENT was the gap.**
  Resolved 2026-08-02 without new measurement. The probe DID reach a verdict and it is recorded in
  memory (`tensor-grep-centrality-leg-moot-2026-07-16`, agent `af308cb3` -- literally this
  worktree): the real-query probe **OVERTURNED** the synthetic oracle ceiling. On the synthetic
  golden (4 hubs TIED at 19.0) a perfect-classifier gate scored **+0.256 ndcg@10**, leaf-regression-
  free by construction -- "worth PROTOTYPING a real classifier". On REAL queries the
  perfect-classifier ceiling itself goes NEGATIVE. **#189 Item-2 is DEAD, not conditionally alive;
  the +0.256 was a synthetic artifact.** All three #189 CPU levers are now dead-as-specified.

  What was actually at risk: **`benchmarks/datasets/find_realquery_golden.jsonl` -- the 26-query
  instrument that produced that negative -- existed ONLY as an untracked file in that worktree.**
  `git log --all` for it returned **0** commits across every ref, while its synthetic sibling
  returned 1 (positive control). One `worktree remove --force` and the evidence behind a settled
  verdict was gone, leaving only a conclusion nobody could re-derive -- at which point the next
  session re-runs the synthetic arm, sees +0.256, and re-chases a dead lever. Committed as `939f133`
  and pushed. **A negative is only durable if the instrument that produced it survives** (same
  reason the MaxSim and cAST negatives are kept indexed). Branch retained as the real-query
  counterpart to #641's synthetic de-risk asset; delete only if that asset is deliberately retired.

- [x] **`rescue/lazy-wave-stash-2026-08-01` -- CLOSED 2026-08-02, and the commit MESSAGE nearly
  cost the check.** I twice described this as "a `temp-verify-preexisting-flake` hack on a v1.64.2
  base, almost certainly discardable" -- reading the subject line, not the diff. The CONTENT is a
  real lazy-import perf change (task #94 PR-2) with a measured rationale: defer
  `tensor_grep.backends.base` off every other `tg` command's hot import path (~5ms on that box),
  plus a lazy `test_command`.

  **Verified already shipped, both halves, against `origin/main`:** `BackendExecutionError` is
  function-local at `main.py:4782` and `:7471`; `ast_workflows` is function-local at `:14949`
  (checked by indentation, not by presence -- a module-level import would have looked identical to
  a grep). Positive control: the same blob yields 6 top-level `tensor_grep` imports, so the search
  works. Nothing in the branch is unshipped.

  Kept on origin as the receipt for the parallel-worktree stash collision
  (`git branch <name> stash@{0}` is the non-destructive rescue); no longer open work. **The lesson
  is the near-miss: a commit subject is not a diff.** A `temp-verify-*` subject on a stash entry
  described the WIP it was taken from, not the change it carried.

## READY — buildable now

None at this snapshot. Start-now set after the 2026-08-05/06 reconcile is empty for product builds
(docs/research only). Do not treat a reproduced bug as a READY build license when its owner program
is still Task 2A FIX-FIRST or rust/e2e blocked.

## BLOCKED — program / CI / Task-2 dependency (not CEO-gated)

- [ ] **#89** WSL-to-Windows native delegation passes a Linux `/mnt/c/...` path to a Windows
  executable, which reports `path_not_found` although WSL can stat the directory. Reproduced, but
  owned by Task 2A→2B; RED `6367614…` is Sol FIX-FIRST and unpushed (no Actions). Receipt:
  `docs/audits/2026-08-02-backlog-reconciliation.md` + `docs/audits/2026-08-03-ceo-backlog-update.md`.
- [ ] **#90** WSL `tg scan` false-clear on raw `/mnt/c` paths; translated control finds matches.
  Doctor half shipped #571; scan half blocked with #89. Same Task 2 program.
- [ ] **F5 / F6 / F8 / MCP-SURFACE** — see canonical index `BLOCKED` triggers (rust/e2e shared-box
  ban; MCP Task 2C ladder). Detail: `docs/BACKLOG.md` reconcile table + closeout plan STOP table.

## BLOCKED — environment (hardware)

None at this snapshot. #109 shipped in PR #605. WSL path bugs above are program-blocked, not
“needs a GPU box.”

## CEO-GATED (do not start without an explicit go)

- [ ] **#72** publish the benchmark proof-point (7.5× fewer tokens than grep) — public claim -- MEASURED 2026-08-23: no public surface carries the number (root README, the PyPI long description rust_core/README.md, pyproject, the GitHub About blurb; docs/ never ships). Nothing to withdraw -- the gate is on PUBLISHING a number, not on retracting one.
- [ ] **#131 / #169** GPU deep-dive + multi-week rebuild; CUDA asset publishing is on a deliberate
  HOLD. Phase-0 shipped correctness-proven assets gated OFF by
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE`; the flag-flip is the CEO's call.
- [ ] **#48** public-shim startup overhead — closed as an honest NEGATIVE (tg's native walk *is*
  rg's walk, same `ignore` crate, so widening relocates cost rather than removing it). The
  architectural remainder is a CEO scoping call.
- [ ] **#77** `tg ledger` local agent context-sharing — approved in principle, scope gated

---

## RETIRED (do not re-chase — each cost a real cycle to settle)

| Idea | Why it is dead |
|---|---|
| `HashSet<PathBuf>` distinct-path counter | The code it would edit documents the design as rejected: unbounded per-path Vec behind a mutex is a contention point AND a DoS surface (50k unreadable entries → 50k-entry payload). Also breaks byte-reproducibility. |
| Rename `incomplete_paths_count` | Its zero-cost precondition expired when the field shipped in v1.99.5; a published field is a 90-day dual-emit exercise, not a two-line diff. |
| `SearchStats::is_empty()` as a live bug | The guarded state is provably unreachable — every writer of `binary_match_files` is preceded by `searched_files += 1`. |
| cAST structural chunking | Real-corpus eval: net-wash quality, 24.4× slower. |
| Dense int8/binary/PCA embedding compression | Retired on measurement. |
| GPU-for-search crossover | Re-adjudicated 2026-07-21 across 10MB–5GB: no crossover at any scale; the shipped kernel is a position-parallel brute-force byte-compare, not PFAC. |
| "Beat rg on cold search" | Closed as an honest negative — tg's native walk IS rg's walk (same `ignore` crate). The campaign's return was a defect family, not milliseconds. |

---

## Reference

- Historical ledger: `docs/BACKLOG.md` · Contracts: `docs/CONTRACTS.md` · Laws: `AGENTS.md`
- Release mechanics + positioning rules: `.claude/skills/tensor-grep-release-and-positioning`
- What counts as proof: `.claude/skills/tensor-grep-validation-and-qa` (oracle forms 1–10)


