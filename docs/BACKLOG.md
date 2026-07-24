# tensor-grep — Project Backlog & PR Tracker

> **Canonical prioritized work list.** Kept in sync with the CLI task store (`TaskUpdate`) and
> GitHub (`gh pr list` is the source of truth for PRs). **CEO status** = summarize SHIPPING + P0/P1.
> Update whenever a PR opens/merges or the queue changes. Task-store IDs (`#NNN`) cross-referenced.
> Last refreshed 2026-07-24 (post-v1.98.1 — the top-10 symbol-graph language campaign, v1.93.10->v1.98.1: #723 validation-scan optimization, then 5 new languages — java (#725, v1.94.0) / php (#724, v1.95.0) / csharp (#726, v1.96.0) / c (#731, v1.97.0) / cpp (#732, v1.98.0, closing 10/10) — plus a go/php/csharp file-dependency foundational tier (#728, v1.96.1) and a coverage-honesty + payload-invariant fix (#733+#734, v1.98.1); prior: v1.93.2 — the CEO v1.92.1-dogfood "fix all + implement + dogfood" goal campaign (v1.93.0, #702-#706), executed end-to-end with a published-wheel 7/7 dogfood verdict, followed by the v1.93.1 (#708) banked-nit close-out and the v1.93.2 (#709) blast-radius scoring-prefilter fix + a session-capture skill/doc-library reconcile; before that v1.92.2 world-class-tier #249 + deep-research #251). **Live PyPI is v1.98.1 (2026-07-24). TOP-10 SYMBOL-GRAPH LANGUAGE CAMPAIGN COMPLETE — the top-10 language campaign (CEO-approved design plan, v1.93.10->v1.98.1) shipped 5 new languages this pass, java/c#/php/c/cpp, all FOUNDATIONAL tier (defs + imports; regex-fallback refs/callers) alongside the existing parser-backed py/js/ts/rust/go, closing the long-CEO-gated "next-language expansion" item (Ruby was not part of this wave). Full per-release receipts in CURRENT STATE below. Fully published (verify `/simple`/`gh run list` before citing a version live if you are reading this soon after a fresh push — runner-scarcity can stretch a release to 30-60min queued, this is healthy not stuck). PR queue: EMPTY (0 open) before this reconcile PR opens.** The CEO `/goal`
> #232 campaign (2026-07-20) mapped the CEO's 9-point spec ("make tg REQUIRED vs rg/ast") one
> gap-point per release, one-per-publish, each independent-Opus-gated, all CPU-safe cloud+CI (never
> the shared desktop): **8 releases v1.84.0 -> v1.91.0, ZERO broken *published* releases, drain now
> CLEAR (0 open PRs).** **CEO#9 GPU-honesty:** `tg calibrate --json` now emits a structured
> `{"calibration_status": "skipped_no_cuda_build", ...}` line on a CPU-only build (a new
> `NoCudaBuildError` downcast in `crossover.rs`, exit code unchanged at 2) so a dogfood harness can't
> misread an honest CPU-only skip as a bare FAIL -> **#678 -> v1.84.0**. **CEO#1 never-empty
> best-effort-primary:** a `tg agent` scan truncated by `--deadline` before ranking ever resolved a
> primary target used to return an empty `{"file": "", "symbol": null}` -- now
> `_best_effort_primary_target_from_map` substitutes the best already-scanned symbol/file/most-central
> file, flagged non-authoritative via `partial_primary: true` + `primary_basis:
> "deadline_truncated_best_effort"`, with a STRUCTURAL `confidence.overall <= 0.55` cap (hardened by a
> gate nit from an emergent to a construction-guaranteed bound) so a partial result can never
> masquerade as confident -> **#679 -> v1.85.0**. **CEO#4 completeness you can trust:** a new
> bidirectional-oracle regression gate (`test_graph_completeness_oracle.py`) proves the documented
> three-state exit-code contract actually holds for `importers`/`callers`/`blast-radius` (exit 0 only
> on a truly complete scan, exit 2 -- never 0 -- on any cap/deadline cut), and closes a real parity
> gap it found along the way: `tg callers`' file-ordering only went likely-first above the 2000-file
> caller-scan ceiling, unlike `importers`, so a deadline cut on a smaller repo could strand a
> late-sorting caller -> **#680 -> v1.86.0**. **CEO#8 enterprise close-the-loop:** wires the existing
> signed `EvidenceReceipt` into a first-class CI gate -- `review-bundle create --receipt` embeds
> signed receipts, `review-bundle verify --against <PR-head-sha>` re-verifies each one's signature,
> trust, and revision-freshness against the real PR head (never `$GITHUB_SHA`, which resolves to a
> merge commit), and two new default-OFF policy levers (`--min-receipts N`, `--expect-key KEY_ID`)
> close a genuine empty-bundle bypass a post-gate NIT caught (a stripped-to-`[]` receipts list
> previously still verified `valid:true` because `all([])==True`) -> **#681 -> v1.87.0**. **CEO#5 `tg
> prepare`:** a one-shot edit-readiness CUJ (`tg prepare REPO "task"`) composes orient -> search ->
> agent -> route-test -> callers -> evidence -> ledger into one call -- primary target + confidence +
> `ask_user`, a callers/blast-radius floor, validation commands, and claim/evidence coordination
> hooks, all under the same `--deadline` exit-2 honesty contract as the rest of the agent-capsule
> family -> **#682 -> v1.88.0**. **CEO#6 AST parity that doesn't fight ast-grep:** `tg run`/`tg scan`
> zero-match exits now print a remediation idiom catalog instead of a silent empty result; the
> ruleset pack resolver accepts mental-model aliases (`auth`, `secrets`, `crypto`, `tls/ssl`,
> `subprocess`, `deserialize`) that resolve 1:1 to the matching canonical pack (never a guessed
> meta-pack); and a `$`-metavariable pattern with no usable ast-grep/native backend now raises a
> clean "Error: ..." + exit 2 instead of an uncaught traceback, and is NEVER silently rerouted to the
> native tree-sitter backend (different query DSL -- would return wrong/empty results) -> **#683 ->
> v1.89.0**. **CEO#2 mega-repo advisory auto-narrow:** a new `_detect_workspace_root` (reusing the
> same closed-vocabulary project-marker set `tg search`'s unbounded-root refusal already uses) stamps
> `workspace_root_detected: true` + a proactive `suggested_scope` on `tg orient`/`tg agent` when the
> target looks like a folder of several independently-cloned projects -- purely additive/advisory,
> the full unscoped result is always still returned, NEVER a silent re-scan or exit-code change ->
> **#684 -> v1.90.0**. **CEO#7 `tg install-dense`:** a one-shot `tg install-dense` installs the
> `semantic` extra (model2vec + numpy, torch-free) via the same `uv tool -> uv pip -> pip` cascade
> `tg upgrade` uses, then fetches the checksum-pinned dense model -- fail-closed on any
> pip/network/checksum failure, never a partial model; `tg find`'s BM25-only degrade message now
> points at it -> **#687**, bundled at release with **CEO#3's $0 doc-honesty fix** (README /
> `docs/installation.md` now say plainly that `pip`/`uvx` installs pay the ~150-250ms Python-
> interpreter floor (#48) and point stable-channel users at the native curl\|bash/PowerShell/npm
> front door for `rg`-parity cold search; `tg upgrade` already gets the native one) -> **#686**, plus
> a calibrate-stdout-JSON-only contract pin + daemon-deadline-route de-flake test nit -> **#685** --
> all three releasing together as **v1.91.0** (`#687`'s Rust command-enum collision with the
> same-day `#682` merge was keep-both-resolved at `bd3a142`, both `install-dense` and `prepare` enum
> variants + dispatch arms retained, re-verified CI-green across the full platform matrix, Opus-gated
> for the stale-venv trap + subprocess-safety + fail-closed model-fetch behavior). **Two headline
> fixes were BINARY-VERIFIED**, not just code-reviewed -- a clean-room `uvx --from
> tensor-grep@1.87.0 tg ...` dogfood confirmed both the GPU-calibrate structured skip on stdout and
> gap#2's truncated-agent emitting a real `primary_target` (never `null`). **CEO-gated, unchanged (out
> of AI scope -- do not build without an explicit CEO decision):** CEO#3's architectural half -- the
> native front door / public-shim startup-overhead reduction -- is **#48** (a currently-open GitHub
> issue; the ~30-40ms Python-interpreter floor caps how far shim tuning alone can close the gap);
> CEO#9's CUDA compute build is **#169** (>$100 spend); **#72** benchmark-numbers publish
> (public/irreversible); **#240-opt2** per-platform native wheels (a public-distribution decision).
> **#72/#169/#189-fork/#240-opt2 are this ledger's own task-store framing, not open GitHub issues** --
> re-verify with `gh issue list`/`gh issue view` before citing any of them as a tracked GitHub item.
> The prior CEO `/goal` "ultimate agentic toolkit" campaign (#224, CEO 2026-07-19, session Stop
> hook: "dogfood + build the ultimate agentic toolkit that saves on searches, uses contracts, supports
> agent-to-agent, [creative GPU], fix any regression, all tests green on the massive workspace incl LSP +
> symbol/codebase mapping, make AI smart without wasting tokens") shipped **8 PRs #668-#675 (v1.81.17 ->
> v1.83.0), one-per-publish, ZERO broken *published* releases** -- and it UN-GATED two long-CEO-held
> directions: **A2A (was #77/#99)** and **GPU ideation (was #169's spend gate on the compute build)**.
> **Headline: `tg ledger` -- the on-moat A2A code-coordination plane -- SHIPPED end-to-end,
> EXPERIMENTAL/default-inert.** `tg ledger claim/release/list` (advisory code-scoped locks, always exit-0 +
> an `overlaps` report, TTL-prune, crash-safe: a dead agent's claim ages out) = **#673 -> v1.82.0**; `tg
> ledger record/find` (content-addressed finding reuse -- the "saves on searches / uses contracts" pillar:
> revision-freshness stamps `fresh:false` on a dirty tree, integrity tamper-detect via a recomputed
> `receipt_digest` + `hmac.compare_digest`, refcount-safe blob GC) = **#675 -> v1.83.0**. Both compose ONLY
> existing primitives (`atomic_write_json`/`_index_lock` RMW, cross-process `index_lock`, evidence receipts,
> `_repo_revision_identity`) -- no new crypto/transport, no network/bus/task-queue, never a blocking lock --
> and each earned an INDEPENDENT adversarial Opus gate (path-confinement + cross-process concurrency for
> claims; integrity tamper-detect + revision-freshness for findings), then a **published-binary dogfood**
> (#225: agent-b sees agent-a's `overlaps` in production, exit-2 traversal; #227: record/find round-trips on
> the shipped wheel). **The deadline-SLA wave that preceded the ledger (#668-#672, v1.81.17-.21)** closed the
> last of the CEO-dogfood enterprise-scale gaps: **#669/v1.81.18** bounds the cold-`tg agent` post-deadline
> assembly tail (wall-to-partial ~= deadline + constant), but the v19 real-workspace dogfood then FALSIFIED
> its magnitude -- the dominant cost was a super-linear vendored-subtree dedup, fixed as the REAL win in
> **#671/v1.81.20** (an O(n^2) `resolve()` dedup = ~61% of `tg agent` wall, 90-144x faster) **[LESSON: a
> synthetic golden set does not carry MAGNITUDE -- the 3rd time this session a real-repo dogfood overturned a
> "fixed" claim; #222]**; **#670/v1.81.19** made `tg importers` scan deterministic-likely-first so a bounded
> partial still finds real importers; **#672/v1.81.21** gave `route-test` a default wall-clock deadline +
> partial-honest agreement under concurrent load; **#668/v1.81.17** shipped the LOW LSP follow-ups the prior
> reconcile had flagged as "queued not started" (exact `rustup component add` remediation + a `pygls>=2.0`
> floor). **#674/v1.82.1** (between the two ledger slices) bounds `tg codemap`'s git-identity calls + kills a
> `resolve()` storm so large workspaces degrade honestly -- and its Opus gate CAUGHT an incomplete
> `_excluded_by_output_str` signature migration (a `tg codemap --check` TypeError) that CI would have
> shipped. **Creative-GPU ideation** (the un-gated half of #169) produced 3 amortization-passing Tier-A ideas
> (GPU corpus-embedding index-build; reframe the already-built+correctness-proven native CUDA many-string
> engine as a `tg scan` many-rule whole-repo prefilter; GPU query-conditioned centrality) -- all build-gated
> behind #169's spend. **3 release transients self-healed via targeted `gh run rerun --failed`** (a crates.io
> `curl` flake, a session-daemon-start flake, a GitHub-API 503 outage on the v1.83.0 release-assets job #228)
> -- none a code regression. **Durable lessons banked:** build-agent commit-and-push disconnect (a slow
> full-suite run stranded a CORRECT #674 fix uncommitted while the PR head stayed broken -- verify the PR
> HEAD has the fix, not the agent's "fixed" claim); ruff-clean != mypy-clean (the Formatting&Linting gate
> runs BOTH `ruff` AND `mypy` -- #675 shipped a mypy-red nit fixed via a `TypeGuard[str]` predicate);
> launcher-shadow (a stale `~/bin/tg.exe` shadows the pip entrypoint -- `tg doctor` detects it, `tg
> repair-launcher` fixes it, dogfood via the explicit `Scripts/tg.exe`); PyPI wheel-lag (`info.version`
> flips before the abi3 wheels finish CDN propagation, so `tag==PyPI` per JSON != pip-installable yet).
> The prior senior-review + Rust-dogfood campaign (2026-07-17/18, CEO directive "review + fix
> + find dead/unused code + clean up", then a same-session Rust-repo dogfood) shipped 11 PRs -- **#655-#666**
> -- one-per-publish, ZERO broken releases, each independently Opus-gated pre-merge. **#655/v1.81.6** defers
> the fast-path-unused `directory_scanner` import in `bootstrap.py`: measured -24% (18.8ms off ~78.1ms)
> `import tensor_grep.cli.bootstrap` cost, but scoped ONLY to `--version`/`-V` and native `run`/`scan`/
> `test`/`ast-info` fast-dispatch (NOT `search`/`--help`, which still hit the broad-scan guard first) --
> explicitly a **partial** win on **#48** (public-shim cold-start): the bare issue-number parenthetical in
> the PR title triggered GitHub's own issue-linker despite the PR body's explicit answer that the fix was
> only a partial win, not a full resolution; the linker's action was manually reverted ~1hr later
> (18:16:37Z / 19:22:13Z) -- lesson: never put a bare issue-number parenthetical in a PR title/body unless
> the merge should actually terminate that tracked item. **#656/v1.81.7** adds one stderr line at `tg agent`'s two
> `typer.Exit(2)` sites distinguishing a trustworthy deadline-partial (high confidence, `ask.required:false`)
> from a genuine incomplete -- no JSON/exit-code change. **#657/v1.81.8** drops the inert
> `opentelemetry-sdk`/`-exporter-otlp` (zero configured `TracerProvider`, all 6 call sites already
> ImportError-guarded no-ops) and moves `pyarrow` into the `gpu` extra only (its 2 production consumers are
> both already gated behind `import cudf`) -- ~31-55 MiB lighter non-GPU installs; adds a governance test
> that now also checks the bare `[project.dependencies]` list (previously only extras were checked, which
> is how both drifted in unnoticed). **#658/v1.81.9 (audit C1/C2)** -- the prior campaign's deadline-honesty
> "COMPLETE" claim was FALSIFIED and re-fixed: `build_symbol_defs_from_map` was called bare (no
> `deadline_monotonic`) by 5 sibling `_from_map` builders + 2 cold wrappers, so its internal test-relevance
> scan ran unbounded on both the cold CLI and warm-daemon paths regardless of `--deadline`; live pre-fix
> repro `tg defs search --deadline 40` -> 113.5s exit 0 `partial:null` (a silent ~3x overrun of the 40s
> budget), impact/refs/callers/source similarly overran -- fixed by threading the deadline through all 7
> sites (mirrors the shipped #205 pattern) plus a return-time backstop on the cold `build_symbol_defs`
> wrapper. **LESSON: a "program COMPLETE" claim needs adversarial fresh-eyes on ALL stages + an OLD-vs-NEW
> real-binary repro, not a one-path dogfood.** **#659/v1.81.10 (audit C4, CWE-59)** -- `tg evidence emit
> --out` and `tg review-bundle create --output` (also the MCP `tg_review_bundle_create` tool) used bare
> `write_text` with no symlink refusal or atomicity; fixed via `session_store._write_json_atomic` extended
> with an `is_symlink()`-before-`.resolve()` guard. **#660/v1.81.11 (audit C3)** -- the MCP `tg_query`
> tool's `workspace_roots` fan-out had no cap and passed the FULL `deadline` to every root (20 roots x 60s
> = up to 1200s from one call); fixed with a fail-closed `_MAX_WORKSPACE_ROOTS = 8` cap (mirrors the
> existing `_MAX_INLINE_RULES=100` precedent) plus one shared monotonic deadline across the loop (not
> divided -- an early-finishing root gives back its unused time). **#661/v1.81.12 (audit B9/A18)** -- `tg
> edit-plan --max-files` visibly wired `max_edits` into `_suggested_edits_from_related_spans` but the
> callee never read it, so `suggested_edits` grew unbounded despite the flag; fixed via a new
> `_capped_suggested_edits` enforcement point (opt-in `suggested_edits_max`, default `None`/unbounded
> elsewhere). **#662 (swept into v1.81.13, non-releasing `chore:`)** -- dead-code cleanup: 255 LOC / 14
> symbols removed across `repo_map.py`/`main.py`/`agent_capsule.py`/`directory_scanner.py` (11 direct
> zero-reference removals + 3 cascaded orphans found while removing their sole caller); 10 of the task's 11
> seed candidates were FALSE POSITIVES on inspection (dispatch-table signatures, a stdlib callback
> contract, a Python protocol method, one already fixed by #661) and were deliberately kept --
> independent-Opus-gate proved every removal dead against the real tree. Flagged (not removed)
> `_negotiate_position_encoding` as an incomplete LSP feature needing a follow-up, which became #663.
> **#663/v1.81.13 (audit B13)** -- `_negotiate_position_encoding()` had zero call sites and no
> `@server.feature(INITIALIZE)` handler, so `ls._position_encoding` stayed permanently stuck at
> `"utf-16"`, giving wrong columns to utf-8/utf-32-negotiating LSP clients on non-ASCII lines; fixed to
> mirror pygls's own `ls.workspace.position_encoding` (verified against both `pygls==2.0.1`, the
> `uv.lock`-pinned version, and `2.1.1`) rather than re-deriving a second, potentially-disagreeing
> negotiation. Writing the behavioral test surfaced a SECOND, independent bug in the same file:
> `_to_cp_col`/`_from_cp_col` treated utf-8 as passthrough same as utf-32, which is wrong since utf-8 is
> variable-width -- added `_utf8_col_to_codepoint`/`_codepoint_col_to_utf8`. **#664/v1.81.14 (CEO dogfood
> find, tg 1.81.11/1.81.12)** -- `tg defs <FILE> <symbol> --provider lsp`/`hybrid` crashed with
> `NotADirectoryError`/WinError 267 because 10 call sites derived an LSP `workspace_root` straight from
> `repo_map["path"]` with no directory guard, reaching `subprocess.Popen(cwd=<file>)`; `--provider native`
> (the CLI default) never hit this path, so it shipped silently. Fixed via a new `_repo_map_root_dir()`
> helper (mirrors the existing `root if root.is_dir() else root.parent` pattern) threaded through all 10
> sites across defs/source/impact/refs/callers/blast-radius; directory-input behavior stays byte-identical.
> **#666/v1.81.15 (broader B9/#661 flag-lie, #212)** -- same flag-lie class in 3 more commands:
> `context-render` (full profile), `blast-radius-plan`, `blast-radius-render` all advertised `--max-files`
> but never bounded `suggested_edits` -- live-dogfood-verified on tensor-grep's own repo
> (`blast-radius-render --max-files 1` vs `50` -> byte-identical 73 `suggested_edits`/40 files, zero effect
> pre-fix); fixed the same way B9 did. **#665 (merged 2026-07-18, publishing as v1.81.16 -- C4/#659
> residual)** -- uniformizes the C4/#659 hardened-write pattern (precheck + same-dir-temp + fsync +
> `os.replace`; bare `O_NOFOLLOW` is a confirmed no-op on Windows) across every sibling atomic writer via a
> new shared `_index_lock.atomic_write_bytes`/`atomic_write_json` primitive: the biggest gap found was
> `checkpoint_store._write_json_atomic` having NO symlink precheck at all, and
> `audit_manifest._write_history_index` (the tamper-evident audit chain) being a fully bare `write_text`
> with zero hardening; also closed a 4th near-identical `dogfood._write_json_atomic` with a predictable
> (non-`uuid4`) temp filename. `codemap.py::_atomic_write_text` (doc-generation, a different risk class)
> explicitly left out of scope, flagged for a future pass. **The Rust-repo dogfood side-investigation
> (#210/#211/#214/#216) closed clean:** #214 (rust-analyzer-init reported as broken) is working-as-intended
> -- it is a missing-rustup-component ENV gap, not a `tg` bug; `tg`'s own doctor/detection code already
> references `rustup` at 4 sites (`scan_guardrails.py`/`main.py`/`lsp_provider_setup.py`/`bootstrap.py`),
> so no code change was needed. **VERIFIED CORRECTION to the prior "in flight" framing:** a
> `fix/lsp-polish-rustup-msg-pygls-floor-216` worktree was scaffolded for LOW-priority follow-ups (a
> friendlier rustup-component message, bumping the declared `pygls` floor from `>=1.3.0` toward the
> `uv.lock`-pinned `2.0.1`, a warm-daemon LSP parity test) but carries ZERO commits and zero uncommitted
> changes as of this reconcile (`git diff origin/main --stat` empty) -- queued, not started; do not
> describe it as "in flight." **CEO-gated, unchanged (verified via `gh issue list`/`gh issue view`, only
> #48 is a currently-open GitHub issue; #72/#169/#189-fork are this ledger's own task-store framing, not
> open GitHub issues):** #72 benchmark-publish (public/irreversible); #48's native-front-door
> architectural half (the ~30-40ms Python-interpreter startup floor visible at the top of every
> `-X importtime` trace, before `tensor_grep` is even reached, bounds how close a Python console-entry
> shim can get to `rg`'s ~7ms native start -- separate from this campaign's import-deferral win); #169 GPU
> enterprise (spend); #189-fork query-gated signal channels vs accept-the-find-ranking-ceiling (taste).
> **Prior campaign (2026-07-17):** the v1.79-v1.81.5 dogfood + deadline-honesty campaign is COMPLETE +
> drained clean, one-per-publish:
> the warm-daemon `--deadline` surface is now bounded end-to-end. **#200 (HIGH, dogfood-caught):** `tg agent
> --deadline` silently ignored on the default warm-daemon path -> #642 (cold residual) + #200-A/#647 (warm
> default deadline, v1.81.2) + #200-B/#648 (front-door anchor, v1.81.3), dogfood-VERIFIED on the published wheel
> (warm `tg agent --deadline 3` -> exit-2 + 'deadline' partial, 0/4 silent). **#203/#652 (v1.81.4):** bound the
> ~9 remaining warm cmds (context/defs/impact/refs/callers/file_importers/blast_radius family), independent-Opus
> -gated (all 9 non-vacuous, fail-closed holds). **#205/#653 (v1.81.5):** the refs internal-context-pack parity
> nit. **Two recurring release-flakes permanently killed:** #646/#202 (test_lifecycle TCP-connect) + #650/#204
> (test_index_lock_is_per_root_not_global wall-clock ratio -> overlap-invariant, validated on the flake runner).
> Also #201/#649 dogfood-harness false-negative, #643 MCP consolidation Phase-1, #198/#644 bench release-intent
> validator, #645/#199 context-render honesty; #189 CPU-moat research negatives recorded in `docs/PAPER.md` §3.10
> (#651, all three ColGrep levers dead/negative on REAL data -> lean-(c) accept-the-ceiling). ZERO broken
> *published* releases across the whole v1.79-v1.81.5 line. The prior `tg find`
> campaign #189 -- CPU semantic moat / ColGrep response -- shipped CLI (v1.77.0) + MCP tool (v1.78.0)
> this session on top of the v1.76.x "remaining AI-actionable backlog" wave #176, ZERO broken *published* releases).
> Shipped 15 PRs (v1.76.x wave): v1.76.0 #601 route-test / v1.76.1 #602 checkpoint-symlink / v1.76.2 #604 perf / v1.76.3 #603 daemon-guard /
> v1.76.4 #605 cuda-ceiling / v1.76.5 #606 orient-scope / v1.76.6 #608 agent-scope / v1.76.7 #610 daemon-coercion+rust-checkpoint-cleanup /
> v1.76.8 #611 checkpoint-symlink-disclosure (**security**) / v1.76.9 #612 GPU-calibrate-honesty / v1.76.10 #615 WSL-detection hardening (`/proc/version`) /
> v1.76.11 #617 device_detect-get_platform-WSL2-honesty / v1.76.12 #619 importers-directory-index-resolution (benchmark-found) /
> v1.76.13 #621 GPU-calibrate-honesty-nits (#612 gate NITs, #182); + #613 flaky-test-hardening + #616 help-contract-flake-fix (both no-release).
> Plus the `tg find` campaign #189, now fully MERGED and RELEASED (v1.78.1): v1.77.0 #626 CLI hybrid search (Wave 2b/2c) / v1.78.0 #627 MCP `tg_find` tool (Wave 2d) /
> #628 `TG_FIND_DENSE_WEIGHT` knob (Wave 3, chore, no-release) / #629 backlog reconcile (docs, no-release) / #632 `mcp` CVE-2026-52870 floor bump (fix, patch-released as v1.78.1);
> + #624 rank_chunks extraction (Wave 2a) + #625 T8 golden harness (Wave 1), both no-release. **On top of v1.78.1, still unreleased (chore, no-release):** #630 whitespace-gate the
> dense-weight classifier + nan/inf clamp (flip-prep, #191). **[v1.78.1-era snapshot; the CURRENT queue is EMPTY per the header above.] PR queue then: 1 open** (`#634`, `fix/find-dense-weight-flip` -- the `TG_FIND_DENSE_WEIGHT`
> default-flip itself, proposing to move the default from inert `1.0` to the swept 1:5 bm25:dense ratio for multi-word NL queries; per #191's evidence
> trail this is the still-open CEO checkpoint every skill referencing the knob describes as "not yet flipped" -- verify current PR state with `gh pr view 634`
> before citing either "flipped" or "still default-OFF" as current).
> Prior: v1.75.0->v1.75.4 GPU Phase-0 (#593/#594/#595/#596/#597, #173 reconcile); v1.73.0->v1.74.4
> (#584/#585/#131-F3/#164/#166/#591); v1.70.0->v1.72.1; v1.69.0-.3; #142.

**Process:** deep-dive/audit (cite `file:line`) → verify-against-code → Sonnet TDD build in
`isolation:'worktree'` → real-venv verify (`uv run --active --no-sync`; copy `rust_core.pyd`, set
VIRTUAL_ENV+PYTHONPATH — a worktree "tests pass" is a hypothesis) → `ruff check` + `ruff format
--preview` + `mypy` (+ `cargo fmt --check`/`clippy` for Rust) → **mandatory adversarial Opus gate** if
it touches apply_policy/mcp/cpu_backend/index_lock/session_daemon/backends → PR → drain
(one-merge-per-publish). Match model to task. Common-sense gate before pending the CEO.

**Legend:** `P0` ship-blocking/#1 gap · `P1` HIGH bug/moat · `P2` MED · `P3` LOW. Status:
`[shipping]` open PR · `[ready]` buildable · `[wip-blocked]` cap-blocked (>5 PRs) · `[blocked]` gated · `[done]`.

**Drain discipline (hard-won 2026-07-10):** verify publish via `/simple` full wheel-pattern
`tensor.grep-1.58.N` OR the release run's publish-pypi=success — NOT a top-level "completed/success"
(can be a non-release run), NOT `grep | head` (head masks grep's exit). Stamp-on-main = Semantic
Release done (safe once /simple lists it). A run `in_progress` on "Python Semantic Release" = native
wheel compile (~65min normal), don't panic-rerun. **WIP CAP: no new build while >5 PRs undrained.**

---

## ⭐ CURRENT STATE (2026-07-24) — authoritative; every section BELOW is HISTORICAL until the next full refresh

- **Live PyPI: v1.98.1 (2026-07-24). Coverage-honesty fix + the invariant repair it needed — the
  campaign's clean close-out.** **#733** fixed `coverage.language_scope` (the `tg agent`/`tg orient`
  capsule field advertising which languages the symbol graph covers): it was hardcoded to the
  pre-campaign `python-js-ts-rust` 4-language list, so a dogfood run on a Java/PHP/C#/Go repo
  under-reported real coverage; now derived DYNAMICALLY from `lang_registry` (the live 10-language
  two-tier scope), dogfood-found, not review-found. **#733's own larger descriptor then tripped a
  DIFFERENT governance test**: `test_importers_payload_is_far_smaller_than_map`'s <0.1x-map
  byte-ratio invariant — the lightweight `tg importers` envelope shares a `_envelope()` helper with
  the heavier `coverage` payload, so growing the coverage descriptor bloated importers' payload too
  (1076B vs a 974.5B floor, DETERMINISTIC not flaky); the build agent's own Windows self-gate ran a
  test subset that skipped `test_file_deps.py` and missed it. **#734** fixed it same-day by
  stripping the shared envelope keys symmetrically before the byte-ratio comparison, restoring the
  invariant without shrinking the coverage fix. **LESSON banked:** a dynamic descriptor that grows a
  SHARED response envelope can trip a payload-ratio governance test on the SMALL-payload side of
  that envelope; a self-gate's test subset is not the full CI matrix.
- **Live PyPI: v1.98.0 (2026-07-24). TOP-10 SYMBOL-GRAPH LANGUAGE CAMPAIGN COMPLETE — py·js·ts·
  java·c#·c++·c·go·rust·php, all 10 lit up for `tg orient`/`tg defs`/`tg source`/`tg imports`/`tg
  agent`.** **#732** ships C++ (`lang_cpp.py`, foundational tier, mirrors `lang_c.py`'s shape):
  functions (free/in-class/qualified out-of-class `Foo::bar()`/`Widget::~Widget()`/templated
  `Box<T>::get()`, all resolved to the bare name so a prototype and its out-of-class definition pair
  under one name), classes (`class`/`struct`/`union`/`enum`/`enum class`, forward declarations
  excluded), namespaces, type aliases (`typedef` + C++11 `using X = ...`), and `#include` (all 4 real
  `preproc_include` shapes). **Live-verified against two real, unmodified, fetched-fresh public
  headers, not just synthetic fixtures:** CPython's `object.h` (49 symbols clean, incl. an
  `#ifdef`/`#else`-guarded struct parsing both arms) and LLVM's `StringRef.h` — which surfaced and
  HONESTLY DISCLOSED the one real gap in the PR body: `class LLVM_GSL_POINTER StringRef {...}`
  misparses to kind `"function"` (the attribute macro becomes a fake return type) — an INHERENT
  ceiling of a preprocessor-unaware parser, indistinguishable from the legitimate
  `struct Point make_point() {...}` shape, so no guard was added (would suppress the legitimate case
  too); member recall mostly survives (StringRef's ~85 methods still resolve individually, only the
  enclosing class's own kind label is wrong). One related bug the same dogfood caught and DID fix: a
  macro-prefixed anonymous union in `object.h` mis-extracted the bare keyword `union` as a symbol
  name — fixed via a `_CPP_RESERVED_KEYWORDS` reject-list layered on the shared name-validity check.
  True `#include -> file` resolution stays deferred to BACKLOG (harder than go/php/csharp's own
  deferred resolvers — C/C++ has no standardized manifest at all). Also corrected a stale AGENTS.md
  claim ("8 of the top-10... C/C++ deferred") that #731 had left unedited.
- **Live PyPI: v1.97.0 (2026-07-24). C symbol + import intelligence (foundational tier) — Phase 1 of
  the campaign's last gap.** **#731** registers `"c"` in `lang_registry` (`lang_c.py`, mirrors
  `lang_go.py`/`lang_php.py`/`lang_csharp.py`): function definitions + prototypes (kind `"function"`,
  gated on the declarator chain passing through a `function_declarator`), struct/union/enum WITH a
  body (kind `"class"`; forward declarations excluded), typedefs (kind `"type"`, one record per
  declarator), and all 4 real `#include` node shapes (plain/quoted/macro-expanded/macro-combined)
  honest-unresolved. `.h` deliberately NOT claimed (already owned by the future C++ grammar per
  `_provider_language_for_path`'s pre-existing cpp assignment for every C/C++ header suffix).
  **Known limitation (code-verified this reconcile against the shipped module, not previously
  written up in any PR — batch into the next C/C++ touch, ultra-low ROI):** a file-scope C
  function-pointer VARIABLE (e.g. `void (*cb)(int);`, no `typedef`) is mis-kinded `"function"` —
  `_c_declarator_name_node` (`lang_c.py:171-207`) sets `seen_function=True` whenever a
  `function_declarator` node appears ANYWHERE in the declarator chain, which is true both for a real
  prototype (`int add(int,int);`) and for a function-pointer-typed variable (the callable-signature
  part of its type is still a `function_declarator` node); the fix is distinguishing an
  outermost-direct `function_declarator` from one reached only through a wrapping
  `pointer_declarator`/`parenthesized_declarator`. Name + location stay correct, only the `kind`
  label is wrong — cosmetic. Cross-file caller-graph stays deferred (`references_and_calls=None`,
  same foundational-tier contract as PHP/C#/Java/Go); `tg refs`/`callers`/`blast-radius` fall through
  to the generic regex-heuristic path (never a crash, never a fabricated AST hit), with an honest
  `resolution_gaps` entry.
- **Live PyPI: v1.96.1 (2026-07-24). `tg imports`/`tg importers` file-dependency FOUNDATIONAL tier
  for go/php/csharp.** **#728** (the investigation opened as a draft mid-campaign) extends the same
  honest-unresolved tier Java already landed (#725) to three more languages: new
  `go_imports_with_lines`/`php_imports_with_lines`/`csharp_imports_with_lines` extractors + shared
  membership in `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES`/`_resolve_raw_import_entry`'s honest-unresolved
  branch — `tg imports` on a `.go`/`.php`/`.cs` file now returns real `{module, line}` rows instead
  of `result_incomplete`, every row `resolved=None, external=False` (never a fabricated path or a
  fabricated `external=True`). **Verify-first correction of the campaign's own scoping brief:** the
  investigation found java (already shipped, #725) was NOT actually a full-resolution reference as
  the original brief assumed — its own `_resolve_raw_import_entry` branch is foundational-tier only
  too, and `tg importers`'s reverse-confirm step (`_confirm_import_edges`) excludes java as well, via
  its own separate language allow-list (unchanged, still `javascript`/`typescript`/`rust`/`python`
  only) — so go/php/csharp were classified under one consistent TRUE-resolution bar: all three need
  resolver work go's `_go_import_path_to_dir` resolves to a PACKAGE DIRECTORY not a 1:1 file map;
  php/csharp have no `composer.json`/`.csproj`/namespace manifest at all. True forward resolution +
  the reverse-confirm allow-list both stay deferred to BACKLOG for all three (scoped precisely in the
  PR body for a follow-up). 12 new tests, 197 related tests green. **Also this pass (non-releasing
  docs):** **#730** refreshed the `tensor-grep-add-language` skill's worked example + seam
  line-number anchors after #728's insertions; **#729** triple-checked + refreshed the full
  27-skill library's citations against v1.95.0 (re-verified file:line anchors, session methods).
- **Live PyPI: v1.96.0 (2026-07-24). C# symbol + import intelligence (foundational tier).** **#726**
  registers `"csharp"` in `lang_registry` (`lang_csharp.py`): classes/interfaces/structs/records/
  enums -> kind `"class"`, methods/constructors -> kind `"function"` (an interface method signature
  and its class implementation both resolve as separate records sharing a name — no dedup, matching
  every real AST node being a legit hit); `using` directives -> imports. Lights up `tg
  orient`/`defs`/`source`/`imports`/`agent` for `.cs` files. Cross-file caller-graph deferred
  (`references_and_calls=None`), same foundational-tier contract as Go/PHP/Java. **Also this pass
  (non-releasing docs):** **#727** folded the session's learnings into `AGENTS.md`/`CLAUDE.md` (new
  *Adding a Language* + *Optimization Discipline* sections, the skill index, `.claude/skill_rules.json`)
  and registered the new `tensor-grep-add-language` skill documenting the 5 critical seams
  (most-forgotten: `_target_language_for_path`, the capsule confidence gate) — the handbook the rest
  of this language wave (C/C++) then followed.
- **Live PyPI: v1.95.0 (2026-07-24). PHP symbol + import intelligence (foundational tier).** **#724**
  registers `"php"` in `lang_registry` (`lang_php.py`, mirrors `lang_go.py`'s self-contained module
  shape, no import cycle with `repo_map.py`): classes/interfaces/traits/enums -> kind `"class"`,
  functions/methods -> kind `"function"`; `namespace_use_clause` imports recorded with PHP's `\`
  namespace separator preserved (an `as` alias not recorded, matching Python's own dotted-module
  convention). Lights up `tg orient`/`defs`/`source`/`agent` for `.php` files; cross-file caller-graph
  deferred, same foundational-tier contract.
- **Live PyPI: v1.94.0 (2026-07-24). Java symbol + import intelligence (foundational tier) — the
  top-10 language campaign's first new language.** **#725** registers `"java"` in `lang_registry`
  (inline in `repo_map.py`'s registration block, not a separate `lang_java.py` module): classes/
  interfaces/enums/records -> kind `"class"`, methods/constructors -> kind `"function"`; `import`
  declarations (plain/multi-segment/`static`/wildcard `.*`) -> imports. Wired at every dispatch site
  a real `.java` file needs (`_imports_and_symbols_for_path`, `build_symbol_source_from_map`,
  `_imports_with_lines_for_path`, `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES`, `_resolve_raw_import_entry`,
  and the MOST-FORGOTTEN seam `_target_language_for_path` per the Go precedent's own code comment).
  Cross-file caller-graph explicitly deferred (`references_and_calls`/`provider_alias_calls`/
  `file_imports_symbol_from_definition`/`import_update_target`/`prime_repo_context` all `None`) —
  degrades HONESTLY: `tg callers`/`tg blast-radius` on a Java target return empty (never a crash,
  never a fabricated hit) plus a labeled `resolution_gaps` entry.
- **Live PyPI: v1.93.10 (2026-07-23). Post-#719 profiling probe's SECOND (previously-deferred) lever,
  now shipped.** **#723** adds a byte-identical textual pre-check to `_framework_test_pattern_bonus`
  (`repo_map.py`) before its per-candidate AST parse (`_framework_test_function_candidates` ->
  `_python_parametrized_test_function_candidates` -> `_cached_ast_parse`) — the #719/v1.93.9 reconcile
  had profiled this at 46% of `tg context-render`'s wall / 23.9% of `tg prepare`'s and DEFERRED it as
  "no clean path"; a fresh profiling pass found the byte-identical substring pre-check that had been
  missed. **Microbench on the shipped wheel: 3657ms -> 1172ms across the target function (~68%
  faster).** Byte-identity holds via a WORD-SPLIT pre-check (not a naive whole-term contiguous check)
  — verified against a constructed adversarial counter-example where a JS `describe`/`it` synthesized
  suite-join candidate straddles the artificial join space; the naive check would have wrongly
  short-circuited it to a false 0, the shipped word-split check correctly proceeds to real scoring. 5
  new tests incl. a call-counting monkeypatch proving the AST parse is actually skipped, not
  coincidentally equal; 69/69 `test_validation_commands.py` + a broader ~200-test regression sweep
  green. **Lever 2** (threading `precomputed_file_paths` through the second validation-plan chain)
  verified ALREADY shipped in #645 — skipped, not bundled, nothing left to build.

- **PR #728 (opened as a draft mid-campaign, referenced here when this section was last touched
  mid-flight) shipped as v1.96.1 — full receipt in the v1.96.1 entry above; this stub line is kept
  only so the historical draft-state framing below it doesn't read as still-current.**
- **Live PyPI: v1.93.9 (2026-07-23). Post-campaign optimization pass — a fresh `cProfile` probe of the published v1.93.8 hot paths (orient/callers/imports/agent/prepare) found 2 levers; the clean one SHIPPED and is DOGFOOD-VERIFIED ~54% faster on its target function.** **#719/v1.93.9** merges the 3 redundant full-tree `ast.walk()` passes in `_python_imports_and_symbols` (repo_map.py) into ONE — measured **82% of `tg orient`'s cold wall** (also ~53-67% of callers/agent). BYTE-IDENTICAL by construction (Import/ImportFrom/ClassDef/FunctionDef/AsyncFunctionDef/Call are mutually-exclusive node types; the trailing `sorted(dict.fromkeys)`/`symbols.sort` make interleaved append-order irrelevant); INDEPENDENT-OPUS-GATED **SHIP** via a 386-file OLD-vs-NEW differential (4960 imports + 10220 symbols compared, **0 mismatches**); the build also removed the now-orphaned `_python_dynamic_import_entries` (its last live caller went away -- #716 removed the other). **DOGFOOD-VERIFIED on the published wheels:** a microbench isolating the function (ast-parse lru-cached, so it times only the walk-merge) = v1.93.8 961ms -> v1.93.9 446ms across 80 files = **~54% faster (>2x)**. The 2nd probe lever (framework-test AST scan in `_discover_validation_tests_for_primary_file`, 23.9% of prepare) was measure-first **DEFERRED** -- no clean path (gate-it = validation-test recall regression risk; parallelize = GIL-uncertain + `@_mtime_aware_cache` thread-safety). Walk-merge lever class now EXHAUSTED (all `ast.walk` sites in repo_map.py swept; the hot redundant-walk fns were #716 `_python_imports_with_lines` + #719). Also this pass: **#720** (test-only, NON-releasing) de-flaked the 2 uncontended hot-path perf-floor asserts in `test_index_lock_concurrency.py` (the #244 ratio form itself flaked at elapsed=4.531s vs the flat 4.0s floor on a loaded runner -- root cause #244 missed: `baseline_elapsed` omits the snapshot-WRITE I/O that `elapsed` pays; widened to `max(baseline*6, 8.0)`, bidirectional guard preserved; the 2 stale-lock-reclaim asserts KEEP flat `<4.0` -- there 4.0 is SEMANTIC, must beat the 5s acquire timeout). LESSON: a WARM `tg orient` dogfood measured the CACHED repo-map path (the function never runs) -> a false -36% artifact; verify a COLD-path optimization by microbenching the function (parse-cached) or clearing `.tensor-grep` between reps, NOT a warm end-to-end run. Both #719/#720 worktrees pruned; drain clear. Tools: scratchpad/opt10/{microbench_astwalk,dogfood_v1939_orient}.py.

- **Live PyPI: v1.93.8 (2026-07-23). The CEO `/goal` "deep-dive + optimize until a +10% overall increase in speed AND output AND accuracy, dogfood-verified" campaign — ACHIEVED at +25.3% overall on the published v1.93.8 wheel (2.5x the 10% target).** A scorecard + baseline were FROZEN before any work (scratchpad `scorecard_definition.md` + `baseline_results.json`, oracle-validated, commit a002d7f1); the goal is the frozen sec-4 composite `overall = mean(speed_leg, accuracy_leg, output_leg) >= 0.10`, 3 legs equally weighted. **RESULT (uvx published-wheel, clean env): speed_leg +13.4%** (median of 8 cold cells: S5 `prepare` +29.8% [map-reuse #714 + O(k) source-truncation #713], S6 `imports` +17.7% [walk-merge+stdlib-fastpath #716], S4 `callers` +15.0%) **· accuracy_leg +62.6%** (the scorecard's `rrf` arm ndcg@10 0.3047->0.4953 via **max-combine fusion #717**: best-rank-wins `max(1/(k+rank))` per leg vs the old `sum`, so the near-floor bm25 leg can no longer DRAG strong dense results down) **· output_leg +0** (results-identical). **Capsule 16/16 agent-accuracy HARD floor HELD; no regression on any class.** SHIPPED v1.93.3->v1.93.8 one-per-publish, ZERO broken *published* releases: the speed wave **#711-#716** (warm-deadline thread, O(k) truncation, prepare map-reuse, imports fast-path, cold-start import-deferral, accuracy-regression harness) then the accuracy lever **#717** (max-fusion default flip). DISCIPLINES that delivered it: **measure-before-build** overturned a cProfile-inflation pessimism (a lens predicted ~4%; the real combined wall was +13.4%); an experiment (`fusion_experiment.py`) VALIDATED the fusion lever on the frozen golden set BEFORE the load-bearing default flip; the **independent Opus gate caught a real single-token-literal regression** the build agent's NL-only dogfood missed (max -0.0369 ndcg on `literal_golden.jsonl`) -> folded a conservative fix (`_find_combine_mode` routes single-whitespace-token queries back to `combine="sum"`, NL keeps max; `reciprocal_rank_fusion`'s default stays max so the scorecard arm is preserved); the frozen scorecard was **NEVER goalpost-moved** (a goal-interpretation fork was surfaced to the CEO, who chose the strict 3-leg reading). HONEST framing: this is an accuracy-LED +25% (the frozen baseline's fusion was genuinely underperforming -- rrf 0.30 vs dense-alone 0.60 -- so the fix is a big relative gain), speed real-but-smaller, output flat. The output levers (O1 bytes/O2 completeness) and the incomplete #3 (`_context_tests` double-pay) are UNNEEDED -- accuracy alone cleared the target. Tools: scratchpad/opt10/{remeasure_speed,compute_speed_leg,compute_composite,fusion_experiment}.py.

- **Live PyPI: v1.93.2 (2026-07-22, #709).** Closes the first of the four follow-ups banked in the
  v1.93.0 entry below: the blast-radius reverse SCORING prefilter now excludes `dynamic_unresolved`
  literals (the #703 dynamic-import honesty fix), so `affected_files`/`dependent_files` no longer
  fuzzy-pulls a same-named decoy module into a blast-radius result. Landed behind a **pin-first
  ranking gate** (a test pinning the CURRENT ranked output GREEN on base *before* the change, so any
  legitimate-entry reorder after it is a STOP-finding, not noise) -- `test_blast_radius_legitimate_dependent_ranking_pin`
  proved zero legitimate reorder. The fourth banked follow-up (the in-repo `tensor-grep-ledger` skill
  question) is resolved by this same session-capture reconcile: all 6 CEO-drafted skill folders
  (ledger/prepare/gpu/find-and-route/multi-project-search/enterprise-review-bundle) are now registered
  in both `AGENTS.md` and `CLAUDE.md`'s skill indexes, `test_skill_index_sync.py`-green. **CEO desk
  (unchanged):** #72/#169/#255/#189-fork/#240-opt2.
- **Live PyPI: v1.93.1 (2026-07-22, #708).** Closes the middle two of those same four banked
  follow-ups: the bootstrap oversized-implicit-root probe now forwards each `--no-ignore*` flag to
  its own field (parity with the sibling large-root guard, which already had per-flag fields); a new
  structural bounded-probe cost-pin test proves the walk stops at `ceiling+1` rather than completing
  a full unbounded walk on a huge implicit root; `_agent_gpu_tg_command` now pre-resolves a bare
  `"tg"` via `shutil.which` before the WSL cross-domain gate runs, so an absolute path always feeds
  that gate; plus one stale citation fix. **CEO desk (unchanged):** #72/#169/#255/#189-fork/#240-opt2.
- **Live PyPI: v1.93.0 (2026-07-22). The CEO v1.92.1-dogfood GOAL CAMPAIGN ("fix all of those issues + implement all of the needs-improvements, then dogfood it") executed END-TO-END in one session: 6 items -> 6 agents -> 5 Opus-gated PRs + 1 evidence-adjudicated HOLD -> 2 releases -> a published-wheel closing dogfood, 7/7 PASS.** Ships: **#702/v1.92.3** unscoped-search fast-refuse (the DEFAULT flag-less/pip-only `_run_rg_passthrough` path had NO walk ceiling -- natively reproduced, not a WSL artifact; bounded probe on `paths_defaulted` only, `IMPLICIT_SEARCH_WALK_FILE_CEILING=1500` single-sourced across all 3 doors; gate: ZERO false-refused shapes) -> then the **documented rapid-window BATCH** (the v1.91.0 precedent) merged 4 PRs into ONE combined release **v1.93.0**: **#703** dynamic-import false-edge fixes (the asked-for feature was ALREADY SHIPPED (#504); execution-verify found relative `import_module(package=...)`/`__import__ level=1` resolving to DECOY top-level files -> honest `dynamic_unresolved`, decoys excluded both directions) · **#704** WSL GPU-probe fix (installer's bare-named POSIX shim wraps tg.exe; suffix-only cross-domain detection misclassified it -> untranslated /tmp path = the reported `path_not_found`; dual-signal detection, live-verified on the reporting box; + gate-folded fail-closed/bounded metadata read) · **#705** UX/honesty batch (ALL dense hints lead with `tg install-dense`; doctor cold-daemon `autostart: on-first-use` field; anonymous prepare-claim `agent_id_hint`; **`tg prepare --out FILE`** byte-identical capsule persist -> `evidence emit` chains without a manual save; + a found-fix: `--semantic` was missing `tg find`'s friendly degrade hint) · **#706** ledger PATH-footgun (ROOT CAUSE was physical: each cmd resolved the STORE dir from the literal PATH -- `claim core/hooks` + `list .` used two different stores; fix = nearest-`.git` canonical store (worktree `.git`-FILE correct) + stored `scope` + subtree rollup + release honesty + a gate-folded CONTRACTS migration note). **CLOSING DOGFOOD (published wheels, clean uvx envs): 7/7 PASS** -- ledger round-trip (wrong-path release now RELEASES, footgun eliminated) · unscoped refuse exit-2 in 1.7s (was 60s timeout) · install-dense hints · doctor autostart honesty · prepare hint/--out/evidence-chain/symlink-refusal · WSL probe symptom ABSENT · dynamic-import decoys excluded. **GPU publish = adjudicated HOLD** (read-only decision package, every claim cited: "beats CPU on WSL/Windows search" is CONTRADICTED by every measured artifact; kernel corrected to brute-force byte-compare NOT PFAC; #169 is task-store framing not a GitHub issue; options (i) flip / (ii) gated-experimental + 2 named messaging fixes / (iii) hold -- recommendation (iii), CEO's call). Also this session: **#701** killed the 2-release index-lock flaky permanently (scheduler-independent Event-handshake contract test) after it red-ed the v1.92.2 release (decoded + rerun --failed recovery). **Banked follow-ups (PR comments):** scoring-prefilter fuzzy-match of unresolved literals into blast-radius affected_files (pre-existing; own slice + pinned ranking test) · no-ignore-family field mirroring + bounded-probe cost pin (#702) · `_agent_gpu_tg_command` shutil.which pre-resolution (#704) · stale citation in #705's region + the in-repo ledger-skill question (adding one requires the skill-index sync test + AGENTS/CLAUDE index updates). **CEO desk:** #72 benchmark-publish · #169 GPU (decision package on file) · #255 moat-options (multi-day cross-language) · #189-fork · #240-opt2.
- **Live PyPI: v1.92.1 (2026-07-21); v1.92.2 (#699) publishing at reconcile time — verify `/simple`/`gh run list` before citing it live. TWO campaigns this session drained one-per-publish, ZERO broken releases: the v21 world-class-readiness tier (#249) and the CEO deep-research "steal-list" directive (#251, now CLOSED).** World-class wave: ledger-CI (#689) · opt-in agent-accuracy golden gate (#690, a loop-4 measurement tool) · **hard cold-path SLA #691/v1.91.1** (bounded the #222 quadratic reverse-import BFS +4 siblings, 26.6s→9.5s; Opus-gate caught a 4th un-gated BFS on the callers path). **CEO deep-research campaign — 6 paper/tooling "steals" VERIFIED against the real code, 5 production improvements + a guard shipped, each independent-Opus-gated (a72885ce/a9d8458/a5438582/ab857cc):** **#693/v1.91.2** loop-4 CLI-dispatcher ranking fix (#250; the #690 gate surfaced it, accuracy 15→16/16) · **#694** many-pattern dedup guard (test-only; found a latent native aho-corasick over-count that blocks fast `-e/-f` delegation) · **#695/v1.91.3** intra-file rayon parallel search on the `backend_cpu.rs` FFI fallback path (line-aligned ≥50MiB chunks, byte-identical to serial) · **#696** accuracy-gate per-task pinning (#252; `assert not misses` replaces a floor that silently absorbed single-task regressions) · **#697/v1.92.0** CodeAnchor-style inline caller annotations (default-OFF `TG_CAPSULE_INLINE_CALLERS`, +2.8% tokens, found+fixed a DAR line-offset off-by-one) · **#698/v1.92.1** chunk-parallel binary-detection parity (#253; `search_file_chunk_parallel` was hardcoding `binary_detected:false` → raw byte matches on >64KiB binaries; mirrors the pinned grep-searcher 0.1.16 64KiB floor) · **#699/v1.92.2** Blackbird flat-scorer hardening (#254; exact word-boundary bonus + best-effort test-file demotion, provably non-destabilizing). **HONEST RESEARCH VERDICT (the CEO deliverable):** every "cheap win" the papers advertised came back NEGATIVE / big-refactor / secondary-path / MODEST once verified vs real code — cAST rejected (24x slower, quality-wash), dense-int8 memory-only + ~2x slower in numpy, warm-session a big refactor (the daemon holds a symbol-map, not a search-index; the common `tg search` is raw rg-passthrough), single-file only a fallback speedup (the headline 200MB tie is `native_search.rs`, streaming-serial-LOCKED by a tested ≥25ms first-match contract), ranking no golden-set movement. **The genuine moat gains are all multi-day CROSS-LANGUAGE efforts — native int8 kernel / native dedup+FFI / `execute_search`-extract+daemon-search or a PyO3 `TrigramIndex` binding / cuVS GPU — banked as #255, CEO-prioritize.** **CEO desk (unchanged + #255 added):** #72 benchmark-publish (public/irreversible) · #169 GPU (>$100 spend) · #48 native front door (~30-40ms Python floor) · #189-fork (taste) · #240-opt2 native wheels (distribution decision) · **#255 moat-investment options** (the deep-research follow-up — which multi-day cross-language effort, if any).
- **Live PyPI: v1.90.0 (2026-07-20); v1.91.0 (#685-#687) still publishing at reconcile time -- verify `/simple`/`gh run list` before citing it live. The CEO `/goal` "make tg REQUIRED vs rg/ast" 9-point campaign (#232) fully drained -- all 9 CEO gap-points mapped to a shipped release, PR queue EMPTY, drain CLEAR, ZERO broken published releases:** **CEO#9** GPU-honesty (`tg calibrate --json` structured `calibration_status` skip signal on a CPU-only build, #678/v1.84.0) -> **CEO#1** never-empty best-effort-primary under deadline truncation (`partial_primary` + a structural `confidence<=0.55` cap, #679/v1.85.0) -> **CEO#4** bidirectional-oracle exit-code completeness gate + `callers` likely-first parity (#680/v1.86.0) -> **CEO#8** enterprise close-the-loop (`EvidenceReceipt` -> `review-bundle --receipt` -> `verify --against` PR-head + `--min-receipts`/`--expect-key` policy enforcement, closing an empty-bundle bypass, #681/v1.87.0) -> **CEO#5** `tg prepare` one-shot edit-readiness CUJ (#682/v1.88.0) -> **CEO#6** AST parity that doesn't fight ast-grep (empty-result remediation + resolve-only ruleset aliases + honest sg-absent error, #683/v1.89.0) -> **CEO#2** mega-repo advisory auto-narrow (`workspace_root_detected` + proactive `suggested_scope`, NEVER a silent narrow, #684/v1.90.0) -> **CEO#7** `tg install-dense` one-shot packaged dense-embedding install, bundled with CEO#3's $0 doc-honesty fix (pip/uvx pays the Python-interpreter floor, #48; `tg upgrade` gets the native front door) and a calibrate-stdout-contract test nit (#687+#686+#685, all releasing as v1.91.0). **Two headline fixes BINARY-VERIFIED** via a clean-room `uvx --from tensor-grep@1.87.0 tg ...` dogfood: the GPU-calibrate structured skip on stdout, and gap#2's truncated-agent emitting a real `primary_target` (never null). **CEO desk (unchanged):** CEO#3-architectural native front door = **#48** (an open GitHub issue; ~30-40ms Python-interpreter floor); CEO#9-CUDA compute build = **#169** (>$100 spend); **#72** benchmark-publish (public/irreversible); **#240-opt2** per-platform native wheels (public-distribution decision) -- the latter three remain task-store framing, not open GitHub issues.
- **Live PyPI: v1.83.0 (2026-07-20, published clean). The CEO `/goal` "ultimate agentic toolkit" campaign (#224) shipped every AI-actionable pillar; PR queue EMPTY, drain clear, ZERO broken published releases:** the on-moat **A2A `tg ledger`** plane is live and dogfood-verified on the published binary -- **claims** (advisory code-scoped locks, always exit-0 + `overlaps`, TTL-prune; #673/v1.82.0; #225 dogfood: agent-b sees agent-a's overlap in production) + **findings** (content-addressed reuse with revision-freshness + integrity tamper-detect; #675/v1.83.0; #227 dogfood), both EXPERIMENTAL/default-inert, each independent-Opus-gated, composing only existing primitives (no new crypto/transport/bus). The deadline-SLA wave (#668-#672, v1.81.17-.21) closed the CEO-dogfood enterprise-scale gaps -- headlined by **#671/v1.81.20**, a super-linear vendored-subtree `resolve()` dedup (90-144x, ~61% of `tg agent` wall) that the v19 real-workspace dogfood surfaced AFTER #669's synthetic-scoped tail fix (**#222 -- synthetic sets don't carry magnitude**), plus `importers` likely-first bounded scan (#670), `route-test` SLA-under-load (#672), and the queued LSP follow-ups (#668). **#674/v1.82.1** bounded `tg codemap`'s git-identity/`resolve()` storm (its gate caught a `--check` TypeError CI would have shipped). **Creative-GPU ideation** produced 3 amortization-passing Tier-A ideas, all build-gated behind #169's spend. **CEO desk (unchanged except #77 A2A now DONE):** #72 publish the moat numbers (public/irreversible; verified + ready), #169 GPU-compute build (spend), #189-fork query-gated signal channels vs accept-the-ranking-ceiling (taste), #48 native-front-door (the ~30-40ms Python-interpreter startup floor). Demand-gated: #98 MCP-consolidation, #141 native-AstBackend. **#207 (stale local checkout) stays inert; #219 (torch/CUDA-13 bump) waits on RAPIDS shipping a CUDA-13 `cudf-cu12`.**
- **Live PyPI: v1.78.0 (2026-07-16, published clean). The `tg find` campaign (#189) SHIPPED end-to-end this session -- the CPU semantic moat / ColGrep response, the forward direction after GPU-for-search retired (#169):** whole-repo natural-language code search (BM25 + local CPU dense embeddings -> weighted RRF -> optional MaxSim -> budget-fitted file:line). Built via Fable plan -> 4-lens adversarial review (correctness/security/eval-integrity/architecture, unanimous GO-WITH-MUST-FIXES, each citing file:line) -> 3 TDD build waves + an MCP tool -> golden gate-run validation -> live dogfood, all cloud Agent subagents + GitHub CI (zero local CPU per the shared-server rule). **Per-wave receipts:** Wave 2a extracted the `rank_chunks` shared fail-closed core from `rerank_hybrid` (#624, `2393a7e`, byte-identical, Opus SHIP). Wave 1 built the T8 golden harness (`benchmarks/eval_late_rerank_quality.py`), a 40-query NL vocab-mismatch golden set, a 74-file corpus, and the P5 lane (#625, `d6fa824`, `chore(bench)` = no-release, bidirectional-oracle). Wave 2b/2c shipped the `tg find` CLI command -- registered at all sites, wired walk->chunk->legs->rank_chunks->budget-fit, with a fail-closed matrix (`BackendExecutionError`->exit-2 catch, chunk-cap->`result_incomplete`+exit-2, hand-written exit codes) (#626 -> **v1.77.0**, `501dc26`). Wave 2d shipped the MCP `tg_find` tool (agent-callable) as its OWN PR to de-risk the LLM-facing surface -- confine-root-first, an error-sanitization split, harness_api docs, and a contract-version bump (#627 -> **v1.78.0**, `6d79945`). **The gates earned their keep -- CI-green does not mean contract-correct, and they caught 2 real bugs, not nits:** the Wave-2c Opus gate caught a genuine F1 fail-closed violation (a query-time `DenseUnavailableError` would have crashed instead of BM25-degrading; fixed RED->GREEN, `045fadc`); the dual-Opus MCP gate caught a required contract-version bump the plan had missed (1.2.0->1.3.0, fixed `3fcca06`). **VALIDATION (INTERNAL; publishing stays CEO-gated #72):** the golden gate-run shows `tg find`'s hybrid ranking (rrf) beats plain BM25 by **+0.195 ndcg@10 (0.305 vs 0.109) / +0.30 recall@10 (0.55 vs 0.25)** on the 40-query NL golden set, positive in all 4 categories and essentially wins-or-ties per query (a single ndcg loss out of 40), bidirectional-oracle-validated twice, deterministic. Live dogfood of the published v1.77.0 wheel PASSED (real `uvx` wheel: `find` registered and not misrouted, honest BM25-only degrade when the `semantic` extra is absent, real relevant results for an NL query, exit 0). **IN FLIGHT: Wave 3 dense-weight knob (#628, still an open draft PR, checks green so far, not yet merged)** ships `TG_FIND_DENSE_WEIGHT` DEFAULT-OFF (1.0 = byte-identical no-op) plus a query-adaptive rule (queries over 2 `split_terms` tokens get the env weight; 2-token-or-shorter queries always stay at 1:1) plus a 10-query literal-query golden slice -- evidence infrastructure for the design pass's finding that a 1:5 bm25:dense weighting lifts NL ndcg@10 by +0.14 (0.305->0.4466) with zero per-category regression, while the literal slice stays protected by construction. Opus-gated SHIP-WITH-NITS, with 2 nits to close before any default-flip: a `math.isfinite` clamp on malformed `TG_FIND_DENSE_WEIGHT` input, and a 3-token-identifier re-sweep (multi-segment identifiers like `getUserName` classify as NL under `split_terms`). **The default-flip itself is a separate CEO checkpoint** (product taste; changes shipped ranking; evidence will be in hand once #628 lands). **Wave-4 stays HELD/evidence-gated:** `TG_LATE_RERANK` remains off -- the gate-run shows rrf+maxsim regressing vs bm25, but that is entangled with a known harness simplification (the late-rerank doc-role encoder is not query/doc role-aware yet, `retrieval_late.py:328-333`), so it is NOT a verdict on MaxSim itself; do not flip until role-aware encoding lands and it is re-measured. `TG_RRF_CHANNELS`/`TG_CHUNKER` remain evidence-gated too. **PR queue: 1 open** (draft #628). **CEO desk:** #72 publish the moat numbers (public/irreversible -- now covers both the original P1/P4 tokens-per-correct proof and this NL-search gate-run, verified + ready, still held); the dense-weight default-flip (product taste, pending #628 + evidence review); #77 tg-ledger; GPU retired-for-search (#169). Demand-gated: #98 MCP-consolidation, #141 native-AstBackend.
- **Live PyPI: v1.76.13 (2026-07-16, published clean). The last AI-actionable item shipped as its own honest close-out -- ZERO broken releases:** #182 (the 3 SHIP-WITH-NITS Opus-gate follow-ups from #612 GPU-calibrate honesty) had been deferred as "opportunistic-batch, do NOT fire standalone." With the drain clear and no future GPU-calibrate PR coming to batch into (the GPU program is CEO-held #169), that deferral would have let real honesty fixes rot -- so #182 shipped as **v1.76.13 #621** (a one-time close-out that empties the queue is closure, not tail-churn). **NIT-1 (the real fix):** the Python `tg calibrate` no-binary message still name-dropped `TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia` in a "confirm before relying on" aside -- asymmetric with the Rust side (`crossover.rs::detect_device_name`), whose test forbids that override as an obtainable path (no nvidia asset ships). Dropped it; added the symmetric `FLAVOR not in output` assertion (RED->GREEN). **NIT-3:** "so calibrate can run" -> "that calibrate requires" (calibrate still fails-closed on a CPU-only box post-upgrade). **NIT-2 (`crossover.rs`, comment-only):** the `#[cfg(feature="cuda")]` mirror-TEST fn is compiled by NO CI job (`cuda-feature-check` omits `--tests`; `test-rust-core` is cuda-off) -- the "Compile-checked only" comment overstated coverage; corrected to state the real gap (the production fn IS compile-checked via its `:533` call site; only the test assertion is uncovered) + why `--all-targets` is deferred (pre-existing cuda test debt in `main.rs`/`test_routing.rs`). **All text-only -- no logic, no control-flow, no CI-config change.** **Adversarial Opus gate: SHIP-CLEAN** -- every honesty claim independently verified TRUE against the shipped assets (default release profile `native-frontdoor` = CPU-only; nvidia legs `if:`-gated off; PyPI wheel carries no CUDA) + no stale assertion elsewhere + zero regression. **Non-blocking coupling banked on #169:** if the GPU release flag ever flips to `native-frontdoor-gpu`, BOTH this message ("not shipped in any current build") and the Rust mirror test ("not shipped in this build") must update in the same change. **PR queue EMPTY (0 open). AI-actionable backlog EMPTY.** **CEO desk unchanged:** #72 publish (public/irreversible; verified numbers ready), #77 tg-ledger, #169 GPU held; #98/#141 demand-deferred.
- **Live PyPI: v1.76.12 (2026-07-16, published clean). The #72 benchmark MOAT RE-PROOF + the correctness fix it surfaced, ZERO broken releases:** The idle drain was put to the highest-value strategic use — re-running the CEO-flagged **#72 tokens-per-correct benchmark** now that **#460** shipped the scoped `tg imports`/`tg importers` primitives. The 2026-07-08 harness + express corpus survived in `scratchpad/bench/` (deterministic, **$0 — no model API**), so the re-run was internal (running is NOT gated; only public *publishing* is CEO-gated per the benchmark skill). **RESULT (independently re-scored via aggregate.py): P4 file-deps tokens-per-correct 53,631 (whole-repo `tg map`) -> 2,387 (scoped) = from ~10x WORSE than rg -> ~2.24x BETTER**, F1 preserved+improved (0.542->0.606, bidirectional oracle PASSED 25/25); P1 def-lookup still 6.4x better (tg 1,457 vs rg 9,328). **The moat is now proven on BOTH axes** — the P4 weakness the original benchmark exposed is closed. The re-run also surfaced a genuine correctness gap -> **v1.76.12 #619** `tg importers` now resolves directory-index imports (a file doing `require('./router')` — Node resolves to `lib/router/index.js` — is now found as an importer; express repro `importer_count 0 -> 2`). Confined to `tg importers` ONLY via `_reverse_importer_extra_aliases` (the shared `_module_aliases_for_path` is byte-identical to main, so `tg blast-radius`/ranking/PageRank untouched). **Opus gate SHIP-WITH-NITS -> remediated** (softened a false "cannot create a false-positive" comment + documented/tested the bare-specifier 0.2-conf heuristic; confined + a blast-radius non-inflation regression test) — and the remediation itself CAUGHT + fixed a PageRank regression in the gate's OWN suggested confine. **PR queue EMPTY (0 open).** **CEO desk:** #72 publish is the CEO's call (public/irreversible) — verified numbers ready; #77 tg-ledger, #169 GPU held; #98/#141 demand-deferred; #182 LOW-batch.
- **Live PyPI: v1.76.11 (2026-07-16, published clean). Post-v1.76.10 dogfood/hygiene follow-ups — 1 WSL-honesty fix + 1 latent release-gate flake, ZERO broken releases:** v1.76.11 **#617** `device_detect.get_platform()` now detects WSL2 via a 3-signal `_running_under_wsl` (env `WSL_DISTRO_NAME`/`WSL_INTEROP` -> `/run/WSL` -> `/proc/version` "microsoft", fail-closed) instead of `/run/WSL`-only — so a stripped-env WSL host reports `platform:"wsl2"` not `"linux"` in the `tg devices` GPU inventory (same WSL/GPU-honesty theme as #612/#615; closes the `device_detect.py` /proc/version sibling nit). **Opus gate SHIP-WITH-NITS** — all 5 safety claims verified against real code (`Platform.WSL2`/`LINUX` has NO control-flow consumer, only a report string at `device_inventory.py:63`; layering-clean core-must-not-import-cli; logic byte-identical to `is_wsl_host`; tests RED-GREEN + CI-safe) — the one drift NIT closed in-PR with a parity test pinning `_running_under_wsl == is_wsl_host`. **#616 (no-release, `test:`+docs)** fixed a LATENT release-gate flake: `test_empty_invocation_fallback_help_matches_public_contract` flipped PASS/FAIL on a BYTE-IDENTICAL binary because it parsed clap's fallback help and clap renders the `update` visible_alias width/platform-dependently -> switched to an INVARIANT assertion (all real cmds present + no unexpected + known aliases optional). Root-caused by BUILDING the real origin/main binary after a wrong first hoist-guess failed CI (lesson: [[tensor-grep-clap-help-parse-width-fragile-2026-07-15]]); the docstring softening + v1.76.10 ledger reconcile rode in #616 too. **#617's first CI red was a stale-base artifact** (branched pre-#616) — fixed by rebasing onto main, not a code defect. **PR queue EMPTY (0 open).** **AI-actionable backlog EMPTY** — remainder demand-deferred (#98/#141), CEO-gated (#72 benchmark, #77 ledger, GPU flip/Phase-2), LOW-batch (#182).
- **Live PyPI: v1.76.10 (2026-07-15, published). CEO v1.76.9-dogfood follow-up — one real fix after a corrected misdiagnosis:** v1.76.10 **#615** `is_wsl_host()` gains the canonical `/proc/version` "microsoft" fallback (Opus SHIP-WITH-NITS + WSL-verified end-to-end) — closes the all-signals-stripped WSL detection-miss behind the CEO's `failed_probe_path` residual. **CORRECTION BANKED (`tensor-grep-verify-code-against-origin-not-stale-local`):** the WSL path-*bridging* bug I first chased was ALREADY fixed v1.75.1 (#594) — I misdiagnosed it by grepping the STALE local checkout (47 behind, v1.74.0) + a manual raw-binary test that BYPASSED tg's translation; the build agent caught it via verify-against-origin/main BEFORE any code (no churn, #184 closed). **BIG UNBLOCK this session:** got WSL repro access (`wsl.exe -e bash`) — the WSL cluster (#89/#90) is no longer env-blocked; reproduced the CEO's failures NATIVELY (unscoped fast-refuses exit 2, GPU reports honestly) = 9p transients, NOT bugs. **2 LOW WSL nits ride forward:** the is_wsl_host docstring softened (this reconcile); `device_detect.py:278` has the same `/run/WSL`-only gap (theoretical — devices already detect; batch-with-future-GPU-touch). **AI-actionable backlog EMPTY** — remainder demand-deferred (#98/#141), CEO-gated (#72 benchmark, #77 ledger, GPU flip/Phase-2), LOW-batch (#182/#186-nits).
- **Live PyPI: v1.76.9 (2026-07-15, published). Post-#176 hardening + dogfood wave — 4 more PRs, ZERO broken *published* releases:** v1.76.7 **#610** gate-NIT hardening (session-daemon metadata coercion-safe removal via `_daemon_identity()` on both sides + Rust `create_checkpoint` fail-closed cleanup `remove_dir_all` on write-failure; Opus SHIP-WITH-NITS) · v1.76.8 **#611** checkpoint snapshot **SECURITY** — no longer follows symlinks (out-of-root file-disclosure): recreate-as-symlink instead of `std::fs::copy`, undo fail-closed via `_resolve_within_root` (Opus SHIP; F1 comment-accuracy + F2a Windows `ERROR_PRIVILEGE_NOT_HELD` message MUST-FIXes addressed + re-verified RED-GREEN) · v1.76.9 **#612** GPU `tg calibrate`/`doctor` guidance honest when this build ships no nvidia asset (CEO v1.76.6-dogfood ask — conditions on the Rust `#[cfg(feature="cuda")]` compile flag, splits the shared hint into no-cuda-build vs device-not-found so an nvidia-binary user is never told "not shipped"; Opus SHIP-WITH-NITS = #182) · **#613** widen the flaky `test_index_lock` heartbeat timing bound 0.6->2.0s for loaded CI runners (`test:` no-release; RED-GREEN verified 0.064s green vs 3.977s sabotaged). **PR queue EMPTY (0 open).** RELEASE-FAILURE NUANCE reinforced: v1.76.9's FIRST run FAILED on that timing-flaky heartbeat test (Semantic Release SKIPPED, no tag, PyPI not bumped) — a job-failure release does NOT self-heal (distinct from a push-race rejection), `gh run rerun --failed` cleared it (flaky passed on retry) and #613 hardens it against recurrence. **#90 CLOSED** — ast-grep "doctor false-available (exit-127 shim)" verified already-fixed in #130(b) (`is_available()` probe-RUNS each `which()`-resolved candidate via `ast-grep --version`, gates on exit 0); native dogfood confirmed. **AI-actionable backlog EMPTY** — remainder demand-deferred (#98 MCP-consolidation, #141 native-AstBackend), env-blocked (#89 WSL /mnt/c path, needs Linux), CEO-gated (#72 benchmark publish, #77 tg-ledger, GPU flag-flip held/Phase-2), or LOW opportunistic-batch (#182 = #612 gate NITs).
- **Live PyPI: v1.76.6 (2026-07-15, published). Directive #176 ("implement the remaining AI-actionable backlog") COMPLETE + a dogfood follow-up (#608) — a 7-PR wave, Sonnet-TDD in `isolation:'worktree'`, Opus-gated where load-bearing, drained one-per-publish, ZERO broken releases:** v1.76.0 **#601** promote `tg route-test` hidden->public (also closed a native-front-door gap — route-test was absent from the rust front door; dogfood-verified on the wheel) · v1.76.1 **#602** checkpoint/rollback write symlink-hardening (Opus SHIP — genuinely TOCTOU-safe incl. Windows `FILE_FLAG_OPEN_REPARSE_POINT` same-handle check, NOT the #110 O_NOFOLLOW-noop) · v1.76.2 **#604** perf `@lru_cache _expected_tg_version` + `tg importers` dead-provenance precision fix · v1.76.3 **#603** session-daemon removes only its OWN metadata (stale-daemon orphan-pileup guard; Opus SHIP-WITH-NITS) · v1.76.4 **#605** bound the cuda GPU implicit-walk to mirror the #105 native DoS ceiling (Opus SHIP-WITH-NITS, exact parity + fail-closed) · v1.76.5 **#606** `tg orient` `suggested_scope` excludes deweighted/ignored trees (no longer misdirects agents to `.claude`; dogfood-verified agent-studio `.claude`->`scripts/`) · v1.76.6 **#608** `tg agent`/`context-render` `suggested_scope` excludes ignored trees too — the #606 SIBLING that dogfooding the SHIPPED v1.76.5 wheel caught (tg agent STILL misdirected suggested_scope to `.claude` while suggested_ignore excluded it; CI + the #606 review both missed it; dogfood-verified before/after `.claude`->`scripts/`). **PR queue EMPTY (0 open).** One CI hiccup self-corrected: v1.76.3 hit a transient Windows dep-install flake -> `gh run rerun --failed` cleared it (a job-failure release does NOT self-heal, unlike a push-race rejection — banked). Cleanup done (6 agent worktrees + all branches pruned). **AI-actionable backlog is now EMPTY** — remainder is demand-deferred (#98 MCP-consolidation, #141 native-AstBackend), env-blocked (#89/#90, need Linux/WSL), or LOW nits (#178/#125; #179 shipped as #608). DOGFOOD LESSON reinforced: running the SHIPPED wheel after a fix catches sibling gaps that CI + the fix's own review miss — #179 was found dogfooding v1.76.5.
- **Live PyPI: v1.75.4 (2026-07-14, published).** The GPU Phase-0 program drained one-per-publish, ZERO
  broken releases: **v1.75.0** #593 `tg orient`/`tg agent` broaden `suggested_ignore` to whole vendor/
  skill trees (M1+M2, a CEO-dogfood-found gap in #164's `.claude` deweight) | **v1.75.1** #594 GPU
  Phase-0 P0-1 WSL probe path-domain bridging + a `cargo check --features cuda` anti-bit-rot CI gate |
  **v1.75.2** #595 GPU Phase-0 P0-2/P0-3 doctor probe failure-taxonomy + honest device-id validation |
  **v1.75.3** #596 GPU Phase-0 P0-4/P0-5 calibrated remediation message + loud nvidia->cpu installer
  downgrade | **v1.75.4** #597 GPU Phase-0 gate-nits (**#172**): doctor-probe precision + native
  error-kind taxonomy, 5 nits incl. the `cfg(any(cuda,test))` classifier fix that silently skipped 3
  tests under a default `cargo test`. Together this closes out **#171** (the GPU Phase-0 program) --
  full receipt in CURRENT LIVE BACKLOG below. **HONEST SCOPE (council must-fix MF-3):** this wave
  hardens the CPU-default GPU code path's correctness/observability under the existing default-OFF
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` gate -- it does NOT promote GPU, change the CPU-default
  recommendation, or prove a speed crossover; full reframe in CEO-FACING GPU below. **#592** (prior
  docs reconcile to v1.74.x) merged `adf5750`; the PR queue was empty going into this wave and is empty
  again after it (see SHIPPING below).
- **Prior wave: v1.74.4 (2026-07-14, published).** The v1.73.0->v1.74.x
  wave — the CEO's v1.72.1 dogfood tail + the v1.74.0 WSL-saddle dogfood fix-queue (#164) — drained
  one-per-publish, ZERO broken releases: **v1.73.0** #584 `tg edit-plan` top-level `confidence` +
  `ask_user_before_editing` (agent parity) & #585 `--deadline` on source/docs-coverage/blast-radius-plan ·
  **v1.74.1** #131-F3 fail-closed `GpuSearchParams` flag completeness (replace/only-matching/max-filesize/
  color/no-ignore-vcs + `context`) · **v1.74.2** #164 embed mermaid in JSON when `--json --mermaid` combined
  (was: `--mermaid` silently dropped under `--json`) · **v1.74.3** #166 clean error + exit 2 for explicit
  `--gpu-device-ids` with no GPU backend (was a raw `ConfigurationError` traceback) · **v1.74.4 (releasing)**
  #164 `tg orient` deweight `.claude` tool-config trees + populate `suggested_ignore` (real-corpus validated:
  agent-studio 10/10 `.claude` in top-10 central_files -> 0/10; tensor-grep byte-identical). **HONEST
  CORRECTION (dogfood-the-shipped-artifact):** F3 (v1.74.1) hardened the rust GPU path, but dogfooding the
  live wheel proved `tg --gpu-device-ids` is handled ENTIRELY by the Python `Pipeline` (selects CuDF/Torch
  backend or raises `ConfigurationError`) and NEVER invokes the rust `handle_gpu_search` — so F3 is CLI
  dead-code. Corrected to the CEO, closed #131/#165, filed the real UX fix as #166 (shipped v1.74.3). **CEO
  1.74.0 dogfood FULLY addressed:** --mermaid (v1.74.2), GPU traceback (v1.74.3), orient-deweight (v1.74.4);
  session_id absence = not-a-bug (uniformly absent across agent/orient/callers, filed LOW observability);
  WSL timeouts = 9p artifacts (native-repro'd, complete). **#591** (`chore(test):`, no release) widened
  timing headroom on 2 flaky sidecar-IPC timeout tests (#167) — MERGED (`fc231ed`). **#592** (this docs
  reconcile) is the lone open PR (was branched from a stale local main at v1.74.0; rebased onto current
  main so its `pip-audit` sees the shipped setuptools 83.0.0, not the pre-bump 82.0.0).
- **Prior wave: v1.72.1 (2026-07-13) — the edit-plan/agent-parity + `--deadline` coverage wave, drained one-per-publish, ZERO broken releases, dogfood-verified where noted:** v1.71.3 **#159** `tg lsp` fail-closed with a clean "pip install tensor-grep[ast]" message on the missing `ast` extra (was a raw `ModuleNotFoundError` traceback; run `29281694988`) · v1.72.0 **#580** `tg edit-plan` structured top-level `validation_plan` (parity with `tg agent`; the CEO v1.71.1 dogfood ask #1) · v1.72.1 **#581** accept `--deadline`/`--no-deadline` on agent/edit-plan/context/context-render/map/orient + `--deadline` on defs (the CEO v1.71.3 dogfood HIGH — the exit-2 "No such option" cliff that burned agent loops; dogfood-verified on the wheel: all 7 accept it, enforced, correct exit codes, orient stays exit-0 per its NO-exit-2 contract). **#582 merged (test-only, `test(cli):`, no release)** — closes PR #581's Opus-gate coverage gaps (daemon-skip regression test w/ passing mutation-check + real-truncation exit-2 + agent-2nd-scan + `CONTRACTS.md` `tg context` nit); full CI matrix green (`6cb53a4`). **PR queue now EMPTY (0 open).** Docs-only, no release, both merged: #578 (4-skill WSL-artifact corrections) + #579 (prior backlog refresh).
- **Prior wave (v1.70.0-v1.71.2, 2026-07-13) — the v1.69.3-dogfood MED batch + audit sweep, drained one-per-publish, ZERO broken releases, all dogfood-verified on published wheels:** v1.70.0 **#152** sys.path.insert imports (2 HIGH) · v1.70.1 **#127** non-git `.gitignore` · v1.70.2 **#90b** `tg doctor` ast-grep exit-0 honesty · v1.71.0 **#153** `tg codemap` default deadline (agent-loop-safe) · v1.71.1 **#154** unscoped/multi-root search fast-refuse (<1s vs 60s timeout — enterprise gap #1) · v1.71.2 **#158** `tg scan` marked-root workspace refuse (the #154 sibling; verified on the wheel — fast-refuses a marked workspace parent). **#578** (docs, no release): 4-skill accuracy refresh correcting TWO false WSL-`/mnt/c` "regression" claims (whole-repo `tg agent` + `tg codemap` — native repro: agent ~26s, codemap 41s whole-repo `partial=false` complete). **CodeQL alert #13 (py/redos test fixture) resolved** (dismissed — false positive on a deliberate ReDoS fixture). **Moat FULLY dogfood-verified on real code** (orient / agent / `search --rank` / `--semantic` graceful-degrade / codemap + #158 scan) — all healthy.
- **Prior wave (v1.70.0) -- the CEO's 2 HIGH `sys.path.insert` fix (#152/#568, `feat` = minor bump), dogfood-verified on the published wheel.** CEO v1.69.3 dogfood found `tg imports`/`importers` did NOT resolve `sys.path.insert(0, .../lib)` path-hacked modules (`from ultrathink_routing import` -> `resolved=None`/`external=True`). Fix parses statically-resolvable `sys.path.insert/append` dirs as import search roots for BOTH the forward (`_python_imports_with_lines`) and reverse (`_python_imports_and_symbols`) resolvers in `repo_map.py`; dynamic/out-of-root exprs stay external (honest). **Verified live on the v1.70.0 wheel** (clean venv): forward resolves `.../lib/ultrathink_routing.py` (`external=False`); reverse `tg importers` -> `importer_count=1, importers=['main.py']`. The release recovered from a razor-thin timing flake in an UNRELATED perf test (`test_incremental_refresh`, missed the `<0.5x` bar by 0.0013s -- NOT a #152 regression): the rerun passed + `release-tag-smoke`=success on the wheel; **#569** (`6eaf384`, `test:`, no release) permanently de-flakes it (per-file sleep raised so the signal dominates the shared graph overhead). **DRAINING one-per-publish: #570** index `.gitignore` non-git-dir no-op fix (#127, `add_ignore` trio in `index.rs`, Opus-gate SHIP, 5 Rust tests) -> **v1.70.1**.
- **Prior wave (v1.69.3): #151 shipped (2026-07-13):** running the published wheel on 3 real external repos (flask/fastapi/requests) surfaced one genuine correctness gap -- `tg importers FILE [ROOT]` (ROOT defaults to CWD) returned an empty `importer_count` with NO signal when FILE is OUTSIDE ROOT (indistinguishable from "genuinely unimported"; silent-wrong for an agent shelling `tg importers /other/repo/file.py` from a different CWD). Fix (**#566** `00e4e99`, Sonnet-TDD -> **Opus gate SHIP** 7-axis adversarial, additive-only, MCP output-shape safe): a lexical containment check in `build_file_importers_from_map` stamps `file_outside_root` + an honest `scan_remediation`. **Dogfood-verified on the published v1.69.3 wheel:** outside-root -> `file_outside_root:true` + remediation; in-root -> `false` + correct `importer_count`. fastapi/requests batteries were clean (no new defects).
- **v1.69.0-.2 (prior wave):** **CEO v1.68.1 WSL-dogfood drain COMPLETE** (2026-07-13) - 3 genuine fixes built (Sonnet-TDD in `isolation:'worktree'`, Opus-gated where MCP-reaching), drained one-per-publish, **zero broken releases**, all **dogfood-verified on the published v1.69.2 wheel** (`release-tag-smoke` = success on the wheel): (a) **#562** `tg codemap --ignore` + `--deadline` (`codemap.py:862`, reuses `_apply_ignore_globs`; no MCP/backend surface) -> **v1.69.0**, both flags accepted + JSON emitted; (b) **#563** F2 nested-import recall (`repo_map.py` two `tree.body` -> `ast.walk(tree)` at :5827/:1813; `tg imports`/`importers` had silently missed function/class-scoped imports incl. the repo's own `main.py -> repo_map.py`; Opus SHIP) -> **v1.69.1**, verified nested `json`+`collections` now resolve alongside top-level `os`; (c) **#564** F3 `suggested_scope`-on-tie (`agent_capsule.py` new `_suggested_scope_from_tied_targets` :197, trigger :2375; the ambiguous-tie path now emits a narrowing scope (deepest common parent of the tied candidates) when they share a subtree, honest-null when the tie spans the whole repo -- both confirmed by dogfood; touches `tg_agent_capsule` MCP; **Opus SHIP** + gate-recommended `os.path.normpath` `..`-confinement hardening + probe test, 11/11 real-venv) -> **v1.69.2**, verified code+normpath-hardening shipped. **WSL-artifacts DEBUNKED (not chased):** codemap "60-180s/no JSON" = WSL 9p (native 33s complete); daemon "not warm" = a naive 2-run test that never hit cache (real ~90-150x cold->warm); env-blocked **#89/#90** need a Linux/WSL box.
- **Prior wave:** **Live PyPI was v1.68.2.** **Campaign #142 ("backlog-100") COMPLETE** — all 4 PRs drained one-per-publish, zero broken releases. **Post-campaign (docs-only, no release):** #559 backlog-reconcile + #560 AGENTS.md whole-repo ruff-scope hardening merged; local-git hygiene = 46 stale branches + 9 remote refs cleaned. Release-blocker learnings banked: `tensor-grep-whole-repo-ruff-format-gap-and-git-show-smudge-2026-07-12` (doc-code-block ruff-format + stale-lock rode into #553; hotfixed via #558) + `tensor-grep-windows-worktree-agents-mask-cross-platform-ci-2026-07-12` (#556 Windows-path tests failed Linux CI).
- **Campaign #142 4-PR queue DRAINED** (Sonnet-built, Opus-gated, one-per-publish): **#554** mcp default 512→2000 (#98) → v1.67.1 · **#555** daemon Tier-2 orient/agent (#108, ~16x latency — dogfood-verified 15.8s→0.95s on the PUBLISHED wheel) → v1.68.0 · **#556** apply_policy UNC-bypass + cross-platform test hardening (#126) → v1.68.1 · **#557** `--count-matches` honest-refuse (#121) → v1.68.2. The mandatory security/correctness gate caught+fixed PRE-MERGE: a UNC command-injection edge (#556), a contract-governance gap (#557), a cross-platform test hole (#556), and a daemon cold-rescue recall regression (#555).
- **Campaign #142 ("backlog-100")**: 4 Fable design-planner audits (`docs/plans/backlog-100/cluster-{1,2,3,4}-*.md`, 2026-07-12) re-verified this ENTIRE ledger, file:line-cited, against the real tree. Headline: **the ledger was badly stale** — most standing items were already shipped across 4 drain waves (#514–#537) that never got written back here. This refresh reconciles it.
- **Reconciled this campaign (already-fixed → dropped from the live backlog below; full per-item receipts in the cluster docs):**
  - **P0 #128/#130/#131 audit queue — 9 of 12 sub-items already fixed**, drain wave #514-#523: #128a ast-grep malformed-JSON→`BackendExecutionError` (`c9e54ef`/#515) · #128b nested-`.gitignore` in both Python walkers (`29269ef`/#522 + `5bf49ad`/#523) · #130a inventory `--deadline`→files=0 (`f88c2a0`/#516) · #130b `tg refs` "45s hang" **superseded/debunked** (deadline-bounded since #393/#478/#440; live repro = 9.16s, exit 2, `partial:true` — an honest partial, not a hang) · #130c checkpoint `IsADirectoryError` (`fad9c2e`/#517) · #130d doctor false `ast_grep.available` (`ac2e153`/#518) · #131 F1 PFAC doc claim (`1889a69`/#514) · F2 GPU benchmark `line_number` vs native `line` key (`7bbe15c`/#519) · F10 dead GPU code (`4a72fca`/#520). Only **#128d, #128c, F3** survive — see CURRENT LIVE BACKLOG. Cite: `cluster-1-p0-correctness.md`.
  - **#118** (#93 SUB-3 unscoped-refuse + SUB-2 companion) — fully shipped via `#506`+`#528`; the companion shipped as **`suggested_scope`** (the old ledger's "suggested_ignore" name never existed in code). **#130 features (a) validation_plan parity + (c) confidence-lift** — shipped via **`#475`** (`ae3ec6d`, v1.54.2, the #84 design). Only **#130(b) sys.path.insert** survives. Cite: `cluster-2-p1-moat.md`.
  - **#129** help-probe-timeout de-flake — closed, two independent control-run fixes (`#521` Python e2e + `#537` Rust sidecar-IPC). **#73** hygiene-guard blind spot (kvikio/dstorage readers) — closed, KEEP-AND-DOCUMENT shipped in `4a72fca`/`#520`. Cite: `cluster-3-p2-followups.md`.
  - **#22, #38, #44, #47, #48, #59, #62 — ALL CLOSED** (the 7 oldest ledger entries, PR3b-era through 2026-07-07): fixed, superseded, or re-homed on receipts (retention-cap #329/#427 · audit-manifest digest+verify system · lockfile #355/#376 · AST byte-budget cache #539 · render-flag guard · sidecar envelope #304 · version-soup structurally gated · daemon Tier-1 #492/#498 · recall+honesty wave #463/#504/#418 · exit-2 contract #419 · Go Stage-1 #420/#422/#431). **#38 (`tg diff-docs`) killed outright** — retirement line added to `PAPER.md` §3.10. **#63 converts to one small build item** (F19+F22+F26 lang-graph tail — see CURRENT LIVE BACKLOG). Full receipts: `cluster-4-stale-reconcile.md`.
- **Net effect:** CURRENT LIVE BACKLOG below is a full rewrite — every surviving item is re-cited against today's tree; #89/#90/#109 (Linux-blocked) carry forward unaudited (outside campaign #142's scope).
- **CEO-gated (the CEO's call):** benchmark publish #72 (the 7.5x-fewer-tokens-than-grep proof) · `tg ledger` #77 (local agent coordination) · GPU multi-week rebuild (conflicts with no-SaaS) · next-language expansion (Java/C#/C++/Ruby/PHP). See CEO-FACING below.
- **Strategic (standing CEO steer, still in force):** tool WORKS (moat = **7.5x fewer tokens than grep on definition-lookup**, benchmark-proven); finish the moat + shift to gotcontext wiring vs draining the self-refilling tail; no-SaaS (gotcontext.ai is the SaaS shell, not tg).

---

## SHIPPING — open PRs (drain one-per-publish) — task #117

**Queue empty -- 0 open PRs (verified 2026-07-24 via `gh pr list --state open`).** The top-10
symbol-graph language campaign (**#723-#734**, v1.93.10->v1.98.1) drained one-per-publish, ZERO
broken *published* releases -- full per-release receipts in CURRENT STATE above. This BACKLOG
reconcile (`docs:`, no release) is the next PR to open -- drain clear, no other build queued.
**Next move is CEO-gated** (native front door #48, GPU-CUDA compute build #169, benchmark-numbers
publish #72, per-platform native wheels #240-opt2 -- see CEO-FACING below) or demand-gated; no
AI-actionable backlog item is currently queued.

**Prior drain waves:** the CEO `/goal` #232 9-point campaign (**#678-#687**, v1.84.0->v1.91.0)
drained one-per-publish, ZERO broken *published* releases -- full per-point receipts in the header
above. Before that: the CEO `/goal` "ultimate agentic toolkit" campaign (**#668-#675**, v1.81.17->
v1.83.0) drained one-per-publish, ZERO broken releases, un-gating A2A (`tg ledger`) + GPU ideation. Before
that: the senior-review + Rust-dogfood campaign (**#655-#666**, v1.81.6->v1.81.16); the v1.75.0->v1.75.4
GPU Phase-0 wave (#593/#594/#595/#596/#597) drained one-per-publish, ZERO broken releases, closing out
**#171** (GPU Phase-0 program, P0-1..P0-5) + **#172** (gate-nits). Before that: v1.73.0->v1.74.4
(#584/#585/#131-F3/#164/#166/#591); v1.70.0->v1.72.1 (#152/#127/#90b/#153/#154/#158/#159/#580/#581); the
v1.68.1 CEO WSL-dogfood 3-PR drain (#562/#563/#564 -> v1.69.0/.1/.2); campaign #142's 4-PR queue
(#554-557 -> v1.67.1-v1.68.2) -- all clean.

## SHIPPED — live on PyPI up to **v1.98.1** (v1.93.10-v1.98.1 detail in CURRENT STATE above;
v1.76.10-v1.83.0 and v1.91.0-v1.93.9 not yet individually backfilled into this section -- see
CHANGELOG.md for the authoritative per-version detail in those gaps)

**v1.84.0-v1.91.0 window (2026-07-20, merged, on PyPI/publishing) -- the CEO `/goal` #232 9-point
campaign, full per-point receipts in the header above:** #678 `tg calibrate --json` structured
`skipped_no_cuda_build` signal, CEO#9 (v1.84.0) | #679 `tg agent` best-effort primary under deadline
truncation + a structural `confidence<=0.55` cap, CEO#1 (v1.85.0) | #680 bidirectional-oracle
completeness exit-code gate + `callers` likely-first parity, CEO#4 (v1.86.0) | #681 `EvidenceReceipt` ->
`review-bundle --receipt` -> `verify --against` PR-head + `--min-receipts`/`--expect-key` policy
enforcement, CEO#8 (v1.87.0) | #682 `tg prepare` one-shot edit-readiness CUJ, CEO#5 (v1.88.0) | #683 AST
empty-result remediation + resolve-only ruleset aliases + honest sg-absent error, CEO#6 (v1.89.0) | #684
`suggested_scope`/`workspace_root_detected` proactive mega-repo auto-narrow (advisory), CEO#2 (v1.90.0) |
#687 `tg install-dense` one-shot packaged dense-embedding install, CEO#7, bundled with #686 pip-vs-native
cold-search doc-honesty (CEO#3, $0) + #685 calibrate-stdout-contract/de-flake test nit, all publishing
together as v1.91.0. Two headline fixes (GPU-calibrate structured skip, gap#2 best-effort primary)
BINARY-VERIFIED via a clean-room `uvx --from tensor-grep@1.87.0` dogfood. #677 (CI-audit
transient-503 hardening, no-release) rode between this campaign and the prior #676 backlog reconcile.

**v1.81.6-v1.81.15 window (2026-07-17/18, merged, on PyPI) -- the senior-review + Rust-dogfood campaign,
full receipts in the header above:** #655 public-shim cold-start partial win (v1.81.6) | #656 stderr
deadline-partial note (v1.81.7) | #657 deps-slim, ~31-55 MiB lighter (v1.81.8) | #658 C1/C2
deadline-honesty re-fix across 7 sites (v1.81.9) | #659 C4 symlink-safe atomic write for evidence/
review-bundle (v1.81.10) | #660 C3 MCP `tg_query` fan-out cap + shared deadline (v1.81.11) | #661 B9
`--max-files` now bounds `edit-plan` `suggested_edits` (v1.81.12) | #662 dead-code cleanup, 255 LOC/14
symbols, non-releasing chore, swept into v1.81.13 | #663 B13 LSP position-encoding negotiation + a
utf-8 column-conversion fix (v1.81.13) | #664 `defs`/symbol commands FILE-path `NotADirectoryError` fix
(v1.81.14) | #666 broader B9 flag-lie across context-render/blast-radius-plan/blast-radius-render
(v1.81.15) | #665 uniform atomic-writer symlink hardening via a shared `_index_lock` primitive (merged,
publishing as v1.81.16).

**v1.75.0-v1.75.4 window (2026-07-14, merged, on PyPI) -- GPU Phase-0 program #171 + gate-nits #172
complete:** #593 `tg orient`/`tg agent` broaden `suggested_ignore` to whole vendor/skill trees, M1+M2
(v1.75.0) | #594 GPU Phase-0 P0-1 WSL probe path-domain bridging + `cargo check --features cuda`
anti-bit-rot CI gate (v1.75.1) | #595 GPU Phase-0 P0-2/P0-3 doctor probe failure-taxonomy + honest
device-id validation (v1.75.2) | #596 GPU Phase-0 P0-4/P0-5 calibrated remediation message + loud
nvidia->cpu installer downgrade (v1.75.3) | #597 GPU Phase-0 gate-nits: doctor-probe precision + native
error-kind taxonomy, 5 nits incl. the `cfg(any(cuda,test))` classifier fix (v1.75.4). **Scope stays
CPU-default-honest** -- this hardens the gated-OFF GPU code path's correctness/observability; it does
not promote GPU or prove a speed crossover (full reframe: CEO-FACING GPU below).

**v1.73.0-v1.74.4 window (2026-07-14, merged, on PyPI):** #584 `tg edit-plan` top-level confidence +
ask_user_before_editing (v1.73.0) · #585 `--deadline` on source/docs-coverage/blast-radius-plan (v1.73.0) ·
#131-F3 fail-closed GpuSearchParams flag completeness (v1.74.1 — later dogfood-proved CLI-dead-code; the
rust GPU path is unreachable from `tg --gpu-device-ids`, which the Python Pipeline owns; #131/#165 closed) ·
#164 embed mermaid in JSON under `--json --mermaid` (v1.74.2) · #166 clean error + exit 2 for `--gpu-device-ids`
without a GPU backend (v1.74.3) · #164 orient deweight `.claude` tool-config + `suggested_ignore` (v1.74.4,
real-corpus validated). v1.74.0 (prior wave, CEO dogfood target).

**v1.71.3-v1.72.1 window (2026-07-13, merged, on PyPI):** #159/#577 `tg lsp` fail-closed on the missing `ast` extra (v1.71.3) · #580 `tg edit-plan` structured top-level `validation_plan`, parity with `tg agent` (v1.72.0) · #581 accept `--deadline`/`--no-deadline` on agent/edit-plan/context/context-render/map/orient + `--deadline` on defs (v1.72.1, dogfood-verified on the wheel: all 7 accept it, orient stays exit-0) · **#582** (`test(cli):`, merged, no release) closes #581's Opus-gate coverage gaps, full CI matrix green (`6cb53a4`). Docs-only, no release: #578 (4-skill WSL-artifact corrections) + #579 (prior backlog refresh).

**v1.70.0-v1.71.2 window (2026-07-13, merged, on PyPI):** #152/#568 sys.path.insert imports resolution — 2 HIGH (v1.70.0) · #127/#570 non-git `.gitignore` (v1.70.1) · #90b/#571 `tg doctor` ast-grep exit-0 honesty (v1.70.2) · #153/#573 `tg codemap` default deadline (v1.71.0) · #154/#574 unscoped/multi-root fast-refuse (v1.71.1) · #158/#576 `tg scan` marked-root workspace refuse (v1.71.2) · #572 skills + BACKLOG docs refresh (`docs:`) · #575 **CLOSED** (CodeQL py/redos suppression — non-functional inline comment; the API dismissal is the real fix).

**v1.59–v1.66.1 window (merged, on PyPI):** #541 index capability-validator · #542 AstBackend tree-sitter query-API repair · #543 warm-daemon default-ON flip (#94 latency lever) · #544 `--index` front-door routing · #545 `--rank` chunk cap · #2/#546 atomic + cross-process-locked index write · #547 backlog reconcile · #63/#548 iterative Go AST walk (no RecursionError) + Python `in_annotation` leak + registry-dispatch governance test · #92/#549 `tg classify --stdin/--text` · #550 ast-grep fail-closed · #551 wedged-python help-probe deflake · #552 launcher import-defer perf · #124-P2/#553 Ed25519 evidence-signing (v1.67.0) · #558 release-blocker hotfix · #554-557 campaign-100 (v1.67.1→v1.68.2, incl. #108 daemon Tier-2 -> v1.68.0, #126 apply_policy fail-open -> v1.68.1, #121 --count-matches -> v1.68.2) · #559 backlog-reconcile (docs) · #560 AGENTS.md whole-repo ruff-scope hardening (docs) · #561 backlog-refresh v1.68.1->v1.68.2 (docs) · **#562 codemap --ignore/--deadline (v1.69.0)** · **#563 nested-import recall (v1.69.1)** · **#564 suggested_scope-on-tie + normpath ..-confinement (v1.69.2)** · **#566 importers outside-root honest signal (v1.69.3, dogfood-found on flask)** · #565/#567 backlog refreshes (docs) · **#130b/#568 sys.path.insert import resolution (v1.70.0)**. Older detail below is HISTORICAL.

Prior batch: #499→v1.58.5 (tg_repo_map 512→2000) · #500→v1.58.6 (#110 write-path symlink TOCTOU) ·
#503→v1.58.7 + #505→v1.58.8 (two flaky-test root fixes) · #501→v1.58.9 (multi-pattern `-e`/`-f`) ·
#502→v1.58.10 (#49 MCP stdio byte-framing+DoS) · **#508→v1.58.11 releasing** (**H3/H4** checkpoint
arbitrary-read + disk-DoS — first codex-audit security fix live). Earlier: v1.58.0-v1.58.4 (daemon
Tier-1, native DoS, blast_radius+GPU-honesty, dual-help, ReDoS fail-closed).

---

## CODEX EXTERNAL AUDIT — HIGH WAVE COMPLETE (#123 [done])
All 5 HIGH verified still-real + fixed + adversarial-Opus-gated + PR'd (H1→#511, H2→#509, H3+H4→#508,
H5→#512, P1→#510). **The gate caught 3 real defects that would've shipped** (H5 POSIX no-op, H1
smart_case 5th silent-wrong, H2 defanged test).

## CEO DIRECTIVE 2026-07-10 (#99 [done]) — after the codex audit
**Do NOT build the SaaS.** Build tg features gotcontext.ai can wire into + focus on the tool
**WORKING** + optimally **PERFORMING**. Workstreams: (A) correctness=audit bugs; (B) perf=#94 + MED
perf; (C) wire-able=EvidenceReceipt (#124). gotcontext stays the CEO's product; we hand it clean
signed consumable tg outputs.

---

## CURRENT LIVE BACKLOG (reconciled 2026-07-13, task #162 — cross-checked against `git log` + live code, not just the ledger)

**Reconciled this pass (already shipped or resolved -> dropped from the active queue below; one-line receipts):**
- **#543** warm session-daemon default-ON flip + version-skew guard (#94) -> shipped `45000f4`, v1.65.0.
- **#544** route `--index` to the Rust capability validator (#138/#140) -> shipped `eaaaf0a`, v1.65.0.
- **#545** cap the plain-`--rank` corpus rechunk (#128d/MED-1) -> shipped `f43b7c0`, v1.65.1.
- **#2** index atomic+locked `.tg_index` write (audit A4) -> shipped `aa57254`/#546, v1.65.4.
- **#63** lang-graph crash/leak tail (Python `in_annotation` leak, Go unbounded recursion, registry-
  dispatch governance test) -> shipped `0fa47d6`/#548, v1.65.5.
- **#92** `tg classify --stdin`/`--text` literal mode -> shipped `7f11bc0`/#549, v1.65.6.
- **#130b** `sys.path.insert`/`append` import-awareness (imports/importers) -> shipped `abd58e2`/#568
  (re-tagged **#152** in later ledger entries, same fix), v1.70.0.
- **#124-P2** EvidenceReceipt signing (shipped as Ed25519, not HMAC as originally scoped — same intent:
  `tg evidence verify`/`keygen`/`pubkey`) -> shipped `5e046ed`/#553, v1.66.1.
- **#124-Gap1/Gap2** checkpoint undo persistence -> both confirmed live in code: `undo_argv`/
  `undo_command` are computed via `_undo_argv` (`checkpoint_store.py:264,871-872`) and returned on
  checkpoint create; the manifest `rollback` block is persisted in `evidence_receipt.py:651-666` and
  `apply_policy.py:988` payloads. Neither is in-memory-only anymore — no single PR to cite, closed
  incrementally across the checkpoint/evidence work.
- **#108** daemon Tier-2 (orient/agent capsules via the warm daemon) -> shipped `47174b4`/#555, v1.68.0.
- **#126** apply_policy fail-open edge (canonicalize exec parent) -> shipped `d8cf53c`/#556, v1.68.1.
- **#121** native `--count-matches` no-rg degrade -> shipped `87515df`/#557, v1.68.2.
- **#127** index-build `.gitignore` non-git-dir no-op -> shipped `2c07e0a`/#570, v1.70.1.
- **F3** GPU fail-closed capability matrix (`--gpu-device-ids` combined with ast/nlp/count/
  fixed-strings/context/line-regexp/word-regexp/LTL) -> confirmed shipped across a "round-4" audit
  pass, `pipeline.py:203-293` (each combo fails loud via `_raise_explicit_gpu_configuration_error`
  instead of silently dropping the flag). The `-o`/`--max-filesize`/`--color`/`--no-ignore-vcs` flags
  named in the original finding are output/filter concerns that never independently select a backend,
  so they were never a live instance of this gap.
- **Dead-code (partial):** `semantic_index.py` already carries the honesty docstring asked for
  (`semantic_index.py:1`, "kept SEPARATE from the Rust TGI v3 `.tg_index`"). NOT confirmed deleted:
  `sidecar.py::_classify_lines` (still defined, `sidecar.py:157`, a thin unused wrapper around
  `_classify_lines_with_metadata`) and `rust_core/src/backend_cpu.rs::replace_in_place`
  (`backend_cpu.rs:212`, still `pub fn`) — kept as a small LOW item below rather than marked shipped.
- **#171** GPU Phase-0 program (de-risking toward a possible Phase-1 `cuda-check` CI gate) -> SHIPPED:
  P0-1 WSL probe path-domain bridging + `cargo check --features cuda` anti-bit-rot CI gate (`7f8de84`/
  #594, v1.75.1) | P0-2/P0-3 doctor probe failure-taxonomy + honest device-id validation (`7350d77`/
  #595, v1.75.2) | P0-4/P0-5 calibrated remediation message + loud nvidia->cpu installer downgrade
  (`a4b3c05`/#596, v1.75.3). Phase 0 is now DONE; Phase 1 (flipping
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE`) is a reversible release-config decision, not a rebuild --
  see the reframed CEO-FACING GPU entry below.
- **#172** GPU Phase-0 gate-nits (doctor-probe precision + native error-kind taxonomy) -> shipped
  `3fd3af7`/#597, v1.75.4. 5 nits incl. a decisive one: `classify_gpu_route_failure` and its 3 tests
  were `#[cfg(feature = "cuda")]`-gated, so a default `cargo test` (no `--features cuda`) silently never
  ran them.
- **#173** this BACKLOG reconcile (docs-only, no release) -- CURRENT STATE/SHIPPING/SHIPPED refreshed to
  v1.75.4 + the CEO-FACING GPU section reframed for honesty (council must-fix MF-3, see below).

**Verify-flagged (not on the live task-store queue; also not confirmed shipped in this pass — flagged
for the next audit rather than re-opened as active work):**
- **#86** T7->T8 late-rerank (real-model latency receipt + golden-set ship/no-ship decision). T0-T6
  (foundation/ONNX encoder/`--semantic` wiring, `#471`-`#474`) shipped v1.51-v1.54; `#531` hardened the
  wall-clock deadline (audit A3, v1.63.2). No T7/T8-labeled commit found in `git log --oneline --all`;
  reads as shelved (`TG_LATE_RERANK` stays experimental/opt-in, `reranker.py:45`) rather than an open
  gap, but this pass could not confirm that either way.
- **#128c** session-daemon worker-semaphore (`TG_DAEMON_MAX_WORKERS`) — no matching symbol anywhere in
  `src/` (`session_daemon.py` has no semaphore/max-workers guard). Genuinely looks unbuilt; not on the
  live queue, so not re-added as active work, but it is the one item this pass could not verify as
  either shipped or intentionally dropped.

### Ready to build (no mandatory-gate blocker)
- **#58** promote `tg route-test` hidden->public (small feature follow-up).
- **#98** MCP tool consolidation (45->~10 task-shaped dispatch tools, non-breaking,
  `TG_MCP_TOOL_SURFACE=lean`) + staleness receipts (P2). Design previously recovered/verified
  (campaign #142). Note: `#554`/v1.67.1 shipped a much narrower precursor under the same tracking
  number (`tg_session_open` default `max_repo_files` 512->2000) — that is NOT this consolidation.
- **#141** native `AstBackend` vs the ast-grep wrapper — DSL divergence + `is_available` broadening
  (design-stage; needs a design pass before a TDD build).
- **#160** v1.71.3 dogfood Medium/Lower feature tail: `suggested_ignore`/orient-auto-deweight,
  complete-scan `suggested_scope`, dynamic-import string/getattr breadth, cold-doctor daemon-autostart
  hint — needs verify-against-code first (some sub-items may already be partially covered by shipped
  work; re-check before scoping a PR).

### LOW-severity follow-ups (non-blocking)
- **#115** symlink sweep — 3 unguarded `std::fs::write` sites (checkpoint metadata, checkpoint index,
  rollback-restore); the `write_bytes_refuse_symlink` helper already exists with one caller, mechanical
  swap to 4.
- **#125** H3+H4 gate follow-ups — checkpoint `except Exception`->`except BaseException`
  cleanup-on-abort + create-vs-undo symlink consistency. MCP-reachable (`tg_checkpoint_undo`).
- **#143** Opus-gate LOW follow-ups — `#543`'s race-test/symbol-timeout/`lru_cache` flip + `#140`'s
  `--` sentinel (non-blocking).
- **#155** `#152` Opus-gate LOW nits — dead reverse-tag block + an ordering comment.
- **Dead-code (partial, see reconciliation note above):** delete `sidecar.py::_classify_lines` (unused
  wrapper) + `rust_core/src/backend_cpu.rs::replace_in_place` if confirmed zero-caller; light Opus
  parity review for the Rust deletion (`cpu_backend` is a mandatory-gate surface).

### Blocked on a Linux/WSL box (env-blocked, not CEO-gated)
- **#89** WSL `/mnt/c` absolute-path resolution in the native backend.
- **#90** `tg scan` ast-grep Linux/WSL portability + doctor false-"available" exit-127. The
  doctor-honesty half already shipped (**#90b**/`fb3291b`, v1.70.2 — `tg doctor` no longer reports
  `available:true` for a non-runnable ast-grep shim); the Linux/WSL ast-grep portability piece itself
  is still open and unverifiable without a Linux/WSL box.
- **#109** cuda GPU implicit-walk ceiling.

### CEO-gated (full framing in CEO-FACING below)
- **#72** benchmark proof-point publish.
- **#131** GPU deep-dive audit + multi-week rebuild (conflicts with no-SaaS).

---

## CEO-FACING / strategic (the CEO's call — not auto-fired)
- **#72** benchmark proof-point publish (tokens-per-correct-answer; tg **7.5x fewer tokens than grep**
  on definition-lookup, oracle-validated). Reinforced by the dogfood + GPU "published accuracy gate"
  enterprise-gap below.
- **#77** `tg ledger` local agent context-sharing (thinktank-reviewed conditional narrow-yes; gated
  behind semantic-search shipping first).
- **GPU program -- REFRAMED 2026-07-14 (Phase-0 complete: #171 + #172; council must-fix MF-3 honesty
  gate baked into this reframe).** NVIDIA native assets are BUILT and locally correctness-proven on the
  dev box (device 0 `RTX 4070` `sm_89`, device 1 `RTX 5070` `sm_120`; see `docs/SESSION_HANDOFF.md` GPU
  dogfood notes and `docs/gpu_crossover.md`), gated OFF the public release by CI Actions var
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; the opt-in flip is
  `native-frontdoor-gpu`, `.github/workflows/ci.yml:1121`). **So Phase 1 is a reversible release-config
  flag-flip decision, not the ~24wk/2-engineer rebuild this section previously described.**
  **CRITICAL HONESTY (do not violate `docs/CONTRACTS.md:80-82`):** flipping the var publishes ASSETS
  only -- it does NOT promote GPU. GPU auto-recommendation stays `false`; no speed crossover vs
  `rg`/`tg_cpu` is proven yet (`docs/gpu_crossover.md` still records "no crossover" for the measured
  workload classes); the reviewer-gated `public-gpu-proof.yml` speed-crossover gate is UNMET (manual
  `workflow_dispatch` only, requires a `self-hosted`/`gpu`/`tensor-grep-public-gpu-proof`-labeled runner,
  and its `environment: public-gpu-proof` lets maintainers require explicit approval before it runs --
  `docs/CI_PIPELINE.md`). Assets become downloadable; the CPU path remains the default and the
  recommended engine until a self-hosted GPU rig proves a crossover -- which it may not.
  **Phase 2** = attach the dev GPU box as that self-hosted runner to actually execute
  `public-gpu-proof.yml`'s speed-crossover proof. CEO-gated: needs the physical hardware attached. **Can
  still re-open the #99 "no-SaaS" wedge the CEO closed 2026-07-10 IF pursued as a funded buildout** --
  Phase 0's de-risking narrows the ask, it does not itself resolve that strategic fork. Campaign #142
  re-homes the old **#47** finding ("GPU public-proof", an NVIDIA-flavor native build) onto this same
  fork -- one CEO decision now covers both. Cite: `cluster-4-stale-reconcile.md` (#47). Phase-0 receipts:
  **#171**/**#172** (CURRENT LIVE BACKLOG above; releases in SHIPPED above). The earlier Phase-0
  honesty/correctness fix (**F3**, the GPU fail-closed capability matrix) also already shipped (see
  SHIPPED above).
- **Enterprise gaps** (dogfood-surfaced, design-scale): **multi-root workspace primitive** (orient/
  search/blast across sibling repos, no manual fan-out) · target-selection accuracy scoreboard
  (top-k/MRR) · cross-OS managed ast-grep · LSP proof-mode (availability ≠ navigation proof).
- **Next-language expansion** (Java/C#/C++/Ruby/PHP) — **SHIPPED 2026-07-24** (CEO-approved design
  plan, v1.93.10->v1.98.1, #723-#734; full per-release receipts in CURRENT STATE above; re-homed from
  **#62**; cite `cluster-4-stale-reconcile.md`). java/c#/php/c/cpp all landed at the FOUNDATIONAL tier
  (defs + imports; regex-fallback refs/callers — `references_and_calls`/`provider_alias_calls`/
  `file_imports_symbol_from_definition`/`import_update_target`/`prime_repo_context` all `None`),
  completing the **top-10 symbol-graph milestone** alongside the existing parser-backed
  py/js/ts/rust/go. **Honesty notes:** Ruby was NOT part of this wave (the original 5-item list
  shipped java/c#/php; C was added instead, and C++ shipped as a bonus 6th language beyond the
  original ask). True cross-file caller-graph resolution for all 5 new languages stays deferred to
  BACKLOG, foundational-tier only (defs + imports, regex-fallback refs). True import->file resolution
  is a SEPARATE, narrower gap that also stays deferred: `tg imports`/`tg importers` for go/php/csharp
  (#728) — each HAS a real manifest (`go.mod`/`composer.json`/`.csproj`) but tg does not resolve
  against it yet — and `#include->file` for c/cpp (#731/#732), which is harder still since C/C++ have
  no manifest concept at all to resolve against. The Go Stage-1 pattern (registry + fail-closed
  grammar-missing + `resolution_gaps`, `3481742`/#420) was the proven template that made the marginal
  per-language cost low enough to execute this whole wave in one campaign, exactly as this entry
  predicted.
  `_provider_language_for_path` already mapped java/c/cpp/csharp/php ids for the LSP-provider layer
  before this wave; the graph layer now does too — the same drift class **#63**'s F22 governance test
  (shipped, `#548`/v1.65.5) continues to guard against future drift here.

## References
- Cross-session resume anchor (memory): `tensor-grep-drain-resume-2026-07-09.md` (live drain/audit/dogfood/GPU state).
- Full process rules: [AGENTS.md](https://github.com/oimiragieo/tensor-grep/blob/main/AGENTS.md).
