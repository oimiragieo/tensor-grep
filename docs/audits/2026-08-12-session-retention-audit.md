# Session retention and skill accuracy audit - 2026-08-12

Status: COMPLETE (audit 35/35; fix wave landed in the same branch)

## Scope and authority

- Audited base: `568065a92a3f3064bf384019f8e7b7e903f20f6f`.
- Working tree: clean worktree `docs/retention-2026-08-12`; the dirty shared checkout is not an audit input.
- Complete skill population: 35 tracked `.claude/skills/*/SKILL.md` files.
- Documented carry-forward population: 34 (`33` `tensor-grep-*` folders plus
  `code-search-and-retrieval-reference`); the bare `tensor-grep` usage skill is deliberately listed
  separately.
- Other tracked retention surfaces: 0 project agents, 2 project workflows, 1 paper,
  `.claude/skill_rules.json`, `.claude/skill_anchor_audit.py`, and
  `.claude/rg_argv_differential_fuzz.py`.
- A passing roster/citation gate is not semantic clearance. The baseline
  `uv run --no-sync pytest tests/unit/test_skill_index_sync.py
  tests/unit/test_skill_library_drift.py -q` completed with `9 passed`; those tests explicitly do
  not prove that cited lines still support the surrounding claims.

## Coverage contract

Every row below must finish as `CLEAN`, `DRIFT_FOUND`, or `CANNOT_VERIFY`. A null, empty, timed-out,
or uncited response is `CANNOT_VERIFY`, never clean. Each receipt must bind to the exact skill path
and audited base, name the strongest claim actually re-derived, identify self-contradictions and
stale facts, and give a fold/no-change/new-skill disposition for the 2026-08-12 lessons.

| Wave | Skill | Status | Receipt summary |
|---:|---|---|---|
| 1 | `tensor-grep-change-control` | DRIFT_FOUND | 7 findings. HIGH: in-place-edit rule for another agent's live WIP contradicts AGENTS.md never-edit-live-worktree law; one-per-publish vs C-batch merge guidance contradicts itself. MED: GPU wave range includes unrelated #593; `needs: smoke` census command returns 2 not 12; CONTRIBUTING ruff command mismatch; ~6 drifted anchors. All four 2026-08-12 lessons fold here; no new skill. |
| 1 | `tensor-grep-debugging-playbook` | DRIFT_FOUND | 8 findings. HIGH: claims both routes enforce 60s/exit-124 but direct native rg route waits unbounded via `Command::status()`; release-tag-smoke tests editable tag source, not the PyPI wheel, and hard-gates the run. MED: `total == total` sold as byte-identity; PR-CI binary premise contradicts its own §19; Git Bash cwd claim false on this host; 15.6ms clock generalized (twins in AGENTS.md + validation skill + one test). All four lessons fold; no new skill. |
| 1 | `tensor-grep-failure-archaeology` | DRIFT_FOUND | 9 findings. HIGH: Battle 29 retired by #868/`50595ef` but listed OPEN/BLOCKED; Battle 28 understates MaxSim decisive retirement; Battle 20 no-crossover overbroad vs current candidate lane. MED: Battle 21 misstates #694 guard; Battle 23 falsely says no `lang_java.py`; AGENTS.md release-authority self-contradiction. Patch-id lesson folds; Task 2A receipts deferred until settled. No new skill. |
| 1 | `tensor-grep-validation-and-qa` | DRIFT_FOUND | ~150 claims checked; substance holds. HIGH counts: e2e "16 files" stale (actual 22) contradicting its own dated 21; GOLDEN_CASES 20 -> actual 21. ~30 anchor drifts with verified substance. Folds: RED reason-class, receipt union==manifest, evidence-outside-tree, wobble-isolation; A87 already present verbatim. No new skill. |
| 1 | `tensor-grep-hermetic-hostile-tests` | DRIFT_FOUND | 8 findings. HIGH: junction rule factually wrong (link path must not pre-exist; target may be populated); behavioral-RED law omits crash/setup/import rejection; Event-gated parent-swap construction absent despite advertised swap coverage. MED: CI env table stale (ast extra + maturin extension present in matrix), dense seam misdescribed, platform arms missing, 2 citation drifts. Folds; no new skill. |
| 2 | `tensor-grep-cross-platform-path-confinement` | CLEAN | 12/12 claims verified incl. single-deref contract and fail-closed `root_servability_reason`. LOW fold gap: `atomic_write_bytes_anchored` is fsync-anchored, not A38 identity-anchored (Sol F5 honest-state sentence owed); 2 nits (hardcoded `:149` against its own cite-by-symbol rule; embedded rotting tag). Fold F5 state; no new skill. |
| 2 | `tensor-grep-release-drift-check` | CLEAN | All 8 sweep derivations re-executed live: tag v1.110.14, 34-skill count, 10/0 language tiers, 10 grammars, doctor schema-3 facts, stamp grep, governance-test shape. LOW: 21/7 receipt counts vs cited ledger census 17/5 not re-derivable — annotate, never rewrite the dated receipt. No change; no new skill. |
| 2 | `tensor-grep-architecture-contract` | DRIFT_FOUND | HIGH: quiet blast-radius claimed still open but `-q` shipped in `search_passthrough` with guard test; AST wrapper-selection moved `main.py` -> `ast_workflows.py`. MED: broken grep instruction; routing taxonomy missing `native_can_serve_plain_text`; MCP 5th site absent from registration model; ~30 anchor drifts with grep-instructions still resolving. Folds: #977 changes-gate note + A90 bullet. No new skill. |
| 2 | `tensor-grep-argv-normalization-and-shadowing` | DRIFT_FOUND | Core model verified byte-exact (rewrite set, destructure ratchet, `-q` four-consumer receipt, fuzz gate wiring). MED: broken `monoton` grep instruction; bullet misleadingly reads `--gpu-device-ids` as a rewrite-set member; sibling `SEARCH_PYTHON_PASSTHROUGH_FLAGS` unnamed. Fold `-f`/`--file` door-parity example (not ledger wording — no ledger at this SHA). No new skill. |
| 2 | `tensor-grep-index-fingerprint-freshness` | DRIFT_FOUND | 18 claims; M17 ordering contract verified at all three sites. MED: skill says re-export cache is NOT swept while `_clear_all_source_caches` explicitly clears it (doc-asserts-hole-code-closes shape). LOW/NIT: stale pre-fix line refs, temp-namespace one-dot vs two-dot spelling, cap counts files-only, rebuild disclosure is verbose-gated. No fold (A92 fingerprint is a different concept); no new skill. |
| 3 | `code-search-and-retrieval-reference` | DRIFT_FOUND | Tier SUPERSEDED chain verified live (10/0 descriptor byte-exact). HIGH: MaxSim/TG_LATE_RERANK called HELD/evidence-gated but `retrieval_late.py` says RETIRED 2026-08-05, superseding the older note — AGENTS.md benchmark bullet carries the same stale wording (twin). MED: RRF formula describes non-default `combine="sum"` (default is `max`); provenance count 6 -> actual 8; two grep instructions point at the pre-shim `main.py` instead of `ast_workflows.py`. 2026-08-12 market result stays in receipts file, not folded; no new skill. |
| 3 | `tensor-grep-config-and-flags` | DRIFT_FOUND | 39 env vars all have real readers (zero phantoms); doctor schema-3 + classify provenance byte-exact. HIGH: `TG_FIND_DENSE_WEIGHT` row says default-OFF/1.0-no-op but the adaptive 5.0 flip landed (#191). MED: boolean "everywhere 1/true/yes/on" contradicted by its own strict-`"1"` rows; missing provenance entry for the newest row; stale v1.78.1 sentinel; wrong `TG_LSP_PROVIDER` reader cite; ~25 line drifts. Fold A70 evidence-signing ambient-key row + ledger knobs; no new skill. |
| 3 | `tensor-grep` | CLEAN | ~22 claims incl. A90 unknown-command contract verified in both front doors; scoped-search workaround still true at this SHA (index dirs deliberately not auto-excluded). 2 LOW: REFERENCE.md session-subcommand list omits `importers`; ceiling "single-sourced in directory_scanner.py" stale (canonical home `io/scan_limits.py` since #715). No fold; no new skill. |
| 3 | `tensor-grep-build-and-env` | DRIFT_FOUND | All mechanism/version claims re-derived clean (toolchain 1.96.0, pyo3 0.29.0 abi3, maturin/uv/ruff/mypy pins, stale-native skipping via `runtime_paths.py:278-321`). MED: rebuild/CI-parity tables recommend local cargo compile, contradicting CPU-SAFE Operating Rule 3 + rustfmt-only exception; A60 WSL-venv rule absent; uv.lock hand-splice + `uv export --locked` verify absent; 13 stamp drifts. Fold 5 items (CPU-SAFE note, A60, lockfile, broken-WSL-stdlib verify-before-trust trap); no new skill. |
| 3 | `tensor-grep-run-and-operate` | DRIFT_FOUND | 27 claims; three-state exit contract verified byte-exact. MED self-contradiction: §11d/§15 still say exit 2 = INCOMPLETE+EMPTY vs found-OR-empty elsewhere; `--deadline` table lists 12 of 21 actual sites; MCP registration reorganized (legacy gate, 13 bare decorators vs claimed count). Folds: closed `incomplete_reason_class` vocabulary + unreadable-path-not-budget-remediable; callers-vs-refs TS guidance. No new skill. |
| 4 | `tensor-grep-diagnostics-and-tooling` | DRIFT_FOUND | ~45 claims; TG_DOCTOR_OFFLINE chain re-derived end-to-end. HIGH: `installation_health` enum names wrong (actual: foreign_launcher/unverifiable_version/launcher_version_mismatch/stale_install). MED: shadow_launchers misdescribed; telemetry/health-endpoint claim has zero source backing; Tool-4 dogfood battery stale; GPU probe status list omits failed/unsupported; v1.110.14 rows added without provenance entry. No lesson fold; drift corrections owed. No new skill. |
| 4 | `tensor-grep-docs-and-writing` | DRIFT_FOUND | 22 claims; two-layer stamping contract verified byte-exact. HIGH self-contradiction: "(verified: no test reads CLAUDE.md)" is false (test_skill_index_sync.py reads it). MED: stale present-tense snapshot of SESSION_HANDOFF date; ci.yml citations drifted. Confirms live #89/#90 handoff contradiction. Folds both 2026-08-12 docs lessons (no embedded snapshots in append-only receipts; same-ID cross-doc contradiction grep). No new skill. |
| 4 | `tensor-grep-release-and-positioning` | DRIFT_FOUND | ~43 claims; release-class two-authority split and push-race DAG verified exact. MED: false "content vanished" claim — ranking-scorer topic lives at AGENTS.md:737 (paraphrase-miss grep failure). LOW-MED: README stamp target declared but token absent (inert stamp). Fold: converse batch-merge rule for no-release windows. No new skill. |
| 4 | `tensor-grep-workspace-dogfood` | CLEAN | All verifiable claims hold at exact SHA incl. live 10/0 descriptor probe. 3 NITs: trend table missing 1.110.14 row; artifact named 111013 cited as 1.110.14 receipt; "Core CUJ 21/21" population not derivable in-repo (23-check gate differs). No fold (lessons are orchestration-generic); no new skill. |
| 4 | `tensor-grep-enterprise-agent` | DRIFT_FOUND | 13 claims; live descriptor 10/0 byte-identical to terminal SUPERSEDED state; chain intact, no stale live row. MED-LOW: two already-rotted re-stamps (bootstrap ~98 lines, repo_map ~247) violating the skill's own never-re-stamp law. NITs: misquoted NOTE range; ambiguous A9/A10/A15 IDs; blast_radius_floor hard-stop claim not in its own list. No fold; no new skill. |
| 5 | `tensor-grep-prepare` | DRIFT_FOUND | 13 claims; --out security triad verified byte-exact. LOW self-contradiction: quotes retired weak agent-id hint beside the strong one; stamp says LIVE 1.110.14 but dogfood table's newest row is 1.110.13 (latest-pointer lesson instance); hard-stop lists `result_incomplete` which prepare never emits. Fold latest-pointer fix; no new skill. |
| 5 | `tensor-grep-ledger` | DRIFT_FOUND | 23 claims; PATH-canonicalization-to-.git verified for both slices. HIGH: worktree "common store" mechanism overclaim — code never follows the `gitdir:` pointer, so sibling worktrees do NOT share a store. MED: `release` matches only claim_id/symbol, not scope (skill says list/release both match). Self-contradiction: "resolve" used in two senses. No fold (union-merge lesson already present); no new skill. |
| 5 | `tensor-grep-find-and-route` | DRIFT_FOUND | 14 claims; fail-closed matrix re-derived branch-by-branch. MED: route-test "can exceed 60s" stale since #672 (now 60s deadline + partial/agreement_basis); #191 adaptive-5.0 flip omitted (multi-word examples now dense-favored, opt-out undocumented). Cited-file drift (semantic-search-campaign default-OFF wording + MaxSim HELD) belongs to wave 7. No fold; no new skill. |
| 5 | `tensor-grep-multi-project-search` | CLEAN | All structural claims verified incl. triple-door glob-does-not-bypass mechanism; stamp fresh. 2 NITs (frontmatter reads loosely for defaulted PATH; timings are dated observations). No fold; no new skill. |
| 5 | `tensor-grep-enterprise-review-bundle` | CLEAN | All contract claims verified incl. receipts-stripping bypass closure via --min-receipts and keyless-checksum honesty. LOW: Related-ref `tensor-grep-code-audit` is machine-global, not in-repo (fresh-clone readers can't load it; AGENTS.md cites it too). Anchor drift disclaimed by design. No fold; no new skill. |
| 6 | `tensor-grep-gpu` | CLEAN | 10 claims verified incl. brute-force-not-PFAC honesty, scoped no-crossover, HOLD/#169, 3 CPU checksum rows, WSL probe fix receipts; stamps fresh. 1 NIT banked (A11/A14 prefixes are gate-internal IDs, unverifiable in-repo). No fold; no new skill. |
| 6 | `tensor-grep-add-language` | DRIFT_FOUND | Registry count, seams, fail-closed grammar behavior, and terminal 10/0 tier verified by four independent mechanisms. HIGH: E1 item says `#include` resolution "tracked but not started" but #957 shipped `lang_c_cpp_include.py` wired into both C and C++ seams (ancestor of the skill's own stamp). MOD: lang_registry.py bare citations rotted, contradicting its "zero drift" claim. MINOR: SUPERSEDED chronology inverted/redundant. Fold corrections; no new skill. |
| 6 | `tensor-grep-backlog-campaign` | DRIFT_FOUND | All drain/playbook mechanics verified incl. 3-way board-grammar chain; skill is on the correct side of an AGENTS.md release-intent self-contradiction it does not share. Stale stamps: "26/27 skills" vs actual 34 (third recurrence); table omits 7 on-disk skills. Minor tensions: ~6 min referents; never-two-per-fire vs docs-only batching. Folds: green-gap batch merge sentence; Step-0 stale-branch reconciliation. No new skill. |
| 6 | `tensor-grep-codex-gated-audit-loop` | DRIFT_FOUND | Hermetic-shim mechanism verified structurally against the real ratchet test. MED: 5 stale line stamps (symbols resolve); verdict vocabulary self-contradiction (FIX-BEFORE-MERGE vs canonical FIX-FIRST; trigger list lacks FIX-FIRST). LOW: receipts omit the A-laws they became (A83/A84/A85/A89); twin-law promised in description but absent from body; Step 5 lacks A51/A81 pinning. Fold parked-FIX-FIRST-state row; no new skill. |
| 6 | `tensor-grep-worldclass-roadmap` | DRIFT_FOUND | Spine verified: VerifyEdit is a future obligation (RESERVED, not KNOWN), parity test excludes reserved names, A92 escrow contract byte-agrees, H1 stamp fresh with exact version attribution. LOW-MED: S1 labeled NEW while canonical doc says BANKED F6 re-scoped (MIXED disposition not surfaced). LOW: S4 parenthetical carries an adjacent correction instead of the doc's own. Fold one-line S1 fix; no new skill. |
| 7 | `tensor-grep-semantic-search-campaign` | DRIFT_FOUND | 22 claims. HIGH: TG_FIND_DENSE_WEIGHT called default-OFF/CEO-checkpoint but flip shipped v1.93.2 (#191/#634, adaptive 5.0, ndcg 0.3047->0.4466). HIGH: MaxSim framed UNVERIFIED/re-run-gate but retrieval_late.py RETIRED it 2026-08-05 post-role-aware-fix. MED: README pins broken (:38->:39, :212->:237); pyproject :620->:627. Self-contradiction: "re-checked UNCHANGED" pins all drifted; flip receipts contradict their own status header. Fold status update; no new skill. |
| 7 | `tensor-grep-benchmark-and-proof-toolkit` | DRIFT_FOUND | ~45 claims; stale-binary refusal chain and launcher attribution byte-exact; CONTRACTS :123 anchor now correct (prior duplicate-anchor contradiction resolved). MED: two repo_map anchors drifted 40/334 lines. LOW: phantom cross-ref pointer; scratchpad receipts unverifiable from any clone; embedded v1.95.0 snapshot heading. Fold 2 one-line fixes (embedded-snapshot + pointer); no new skill. |
| 7 | `tensor-grep-research-frontier` | DRIFT_FOUND | All 5 bank levers verify TRUE (MaxSim RETIRED, cAST rejected, int8/PCA deferred, conditioned-centrality survivor, GPU scope); skill never resurrects dead ends. MED: frontmatter still lists C/C++ as open frontier though SHIPPED/SUPERSEDED 2026-08-09; stamps v1.96.0 with unlogged 2026-08-09 pass; cited-file MaxSim contradictions (twins in semantic-search + failure-archaeology). Conditional fold of 2026-08-12 dated pointer bundled with stamp refresh; no new skill. |
| 7 | `tensor-grep-research-methodology` | DRIFT_FOUND | Structural methodology verified incl. A93 three-artifact numeric agreement. Triple-fault: bare AGENTS.md line cite violating its own rule; two dead re-verify greps (quoting/BRE errors); stale "#456 open" status (merged fca77a4). Conditional fold: three 2026-08-12 lessons (trigger-amendment record, minimum disposition, receipts-propose/PR-owns) + Exa receipt format as evidence standard; no new skill. |
| 7 | `tensor-grep-large-repo-scale-campaign` | DRIFT_FOUND | All mechanism claims verified TRUE (1500-ceiling doors, non-lazy scandir gap still open, #478 four-loop bounding, #390 daemon closure with zero-drift stamps). Findings are stamps only: one self-certified anchor drifted +39; temporal self-inconsistency (frontmatter verifies v1.110.14 "on 2026-07-24"). AGENTS.md twin flagged: CALLER_SCAN_FILE_CEILING prose says 512, code says 2000. No fold; no new skill. |

## Verified pre-audit findings

1. **The current handoff contradicts itself.** `docs/SESSION_HANDOFF.md:21-23` says `#89/#90`
   remain BLOCKED, while `docs/SESSION_HANDOFF.md:38-43` says they remain READY. The canonical
   `2026-08-12.1` board disposition is BLOCKED behind Task 2A.
2. **The reconciliation receipt needs a latest-state pointer.** Its opening still names plan rev 4
   and early Task 2A heads, while its append-only ledger reaches Sol round 1 at `8181762`. Historical
   text stays intact; a current pointer must make the latest ledger authoritative.
3. **`tg-skill-audit.js` is not artifact-bound.** It hardcodes
   `C:/dev/projects/tensor-grep`, does not record the audited root/SHA/blob population, and credits
   any truthy cluster response as complete without exact equality against `skills_audited`.
4. **`tg-audit-fix-loop.js` is metadata-only.** It advertises five phases and defines schemas but
   contains no `phase(...)`, `agent(...)`, or terminal `return`; it is not an executable workflow.
5. **`skill_rules.json` is intentionally sparse.** It is a trigger snapshot rather than the skill
   roster. Schema/regex/dangling-key validation is appropriate; forcing all 35 skills into it would
   change its contract rather than fix drift.
6. **No paper update is currently justified.** `docs/PAPER.md` already records retrieval ceilings,
   end-to-end patch correctness, and apply/verify loops. A date-only restatement would duplicate,
   not retain, the 2026-08-12 finding.
7. **The dirty checkout carried NEVER-COMMITTED lesson sections (ERRATUM-2 to the reconciliation).**
   `git log --all -S` pickaxe proves `## Session Lessons (2026-08-07, campaign continuation)`
   (AGENTS.md + the 16-item handoff block) and `## CI Cost Discipline (2026-08-07)` were never
   committed to any ref; the reconciliation's one-file spot-check had classified all 11 dirty docs
   as "stale snapshots, behind not novel". All three sections landed verbatim in this retention
   branch with provenance notes; reconciliation section 6 carries the erratum.

## Fresh external research disposition

Fresh Exa research supports progressive disclosure, coherent skill boundaries, manifest-first
coverage, failed-agent-as-coverage-hole, and semantic checks beyond path existence. It adds two
qualifications that this campaign will preserve:

- The 5,000-token body target is a recommendation, not a portable validity limit.
- A wave size of five is a local rate-limit/cost safety cap, not a universal research optimum;
  task decomposability and coordination topology determine useful parallelism.

The research also adds a large-library warning: metadata-only routing can lose decisive signals in
skill bodies. That supports body-aware audit/retrieval while keeping foreground progressive
disclosure; it does not justify loading all skill bodies into every agent turn.

## Initial fold-versus-new-skill decisions

- Fold stale-branch preservation and union-merge verification into
  `tensor-grep-change-control` unless the full audit proves a distinct reusable trigger is missing.
- Fold RED-by-design reason classification and hostile-fixture preconditions into existing
  validation/hermetic-test skills.
- Fold CI collector reachability, platform-scoped exclusions, and exact workflow-condition parsing
  into existing CI/change-control guidance.
- Do not create a skill solely because a lesson is new. A new skill requires a distinct trigger,
  independently reusable workflow, and a non-overlapping contract that existing skills cannot hold
  cleanly.

## Completion totals

```text
expected=35  CLEAN=7  DRIFT_FOUND=28  CANNOT_VERIFY=0
missing=0  duplicates=0  unexpected=0
```

CLEAN rows: `tensor-grep-cross-platform-path-confinement` (folds owed, applied),
`tensor-grep-release-drift-check` (annotation owed, applied), `tensor-grep`,
`tensor-grep-multi-project-search`, `tensor-grep-enterprise-review-bundle`, `tensor-grep-gpu`,
`tensor-grep-workspace-dogfood` (nits only; 1.110.14 header corrected to the 1.110.13 artifact).

## Fix wave (same branch, file:line-verified by seven build seats)

Every HIGH/MED substantive finding above was repaired by file-scoped build seats that verified
each fact against this exact tree before writing (two seats corrected facts in their own briefs:
the dense-weight flip first released v1.79.0, not v1.93.2; route-test #672 shipped v1.81.21, not
v1.100.0). Dated receipts were SUPERSEDED/annotated, never rewritten; drifted anchors were
converted to grep-the-symbol form with was->now receipts, never re-stamped bare. Pure line-number
rot without a substantive error was left to the library's own grep-form design and is recorded in
the row summaries above.

Non-skill repairs landed alongside: AGENTS.md (TG_LATE_RERANK RETIRED bullet; release-intent
SUPERSEDED-by-A33 note; CALLER_SCAN_FILE_CEILING 512->2000; A88 junction erratum; the two
never-committed 2026-08-07 sections), SESSION_HANDOFF.md (#89/#90 contradiction fixed; 16-lesson
block landed), reconciliation ERRATUM-2, `tg-skill-audit.js` (artifact binding + exact coverage
equality + evidence floor), `tg-audit-fix-loop.js` (five advertised phases wired; verdict
vocabulary unified to FIX-FIRST), and a new governance test
`tests/unit/test_skill_rules_registry.py` (schema + regex-compile + dangling-key detection).

Verification at audit close: `uv run --no-sync pytest tests/unit/test_skill_index_sync.py
tests/unit/test_skill_library_drift.py tests/unit/test_skill_rules_registry.py
tests/unit/test_backlog_tracker_truth.py -q` -> 56 passed; both workflow scripts pass a
wrapped-function `node --check` (top-level `return` is the workflow DSL shape); new test file
ruff-check + `ruff format --preview` clean.

## New-skill decision

Zero new skills. All 35 seats returned NO with trigger-collision reasoning (each candidate lesson
fits an existing skill's trigger/workflow/contract), matching the Exa "coherent unit" guidance:
folding preserves routing precision; a new skill is owed only when a distinct reusable trigger
exists. The one genuinely new surface was the governance TEST, not a skill.

## Merge receipt (A28/A29)

- Landed as PR **#1005** (`docs:`; no release), commit `f7bcc9a`, squash-merged to `main` as
  `5148664da4cf72a8adf31ebc5deec940b811aca1` (2026-08-13T07:13:33Z).
- PR CI run `31673774966`: first pass 37 success / 10 skipped / 1 failure. The single failure was
  `windows-agent-readiness` probe `public-version-powershell` ("timed out after 30s" at 31.047s)
  while the same binary passed `public-version-pwsh-noprofile` (0.453s), `-cmd` (0.203s), and
  `python-subprocess` (0.141s) in the SAME run — the known profile-loading flake recorded in
  SESSION_HANDOFF "Session Lessons (2026-08-07)" item 3. `gh run rerun --failed` → all green
  (38 success / 10 skipped / 0 failure). Diff is docs/skills/workflows/tests only, so it cannot
  affect that probe.
- Independent adversarial gate: FIX-FIRST (8 findings) → all repaired → re-gate SHIP-WITH-NITS
  (all eight PASS). Nits banked per A19 (briefing branch-name typo; empty worktree `.venv`
  PATH-fallback note; ERRATUM-2 tense).
- Post-merge main run for `5148664`: `31676896244` completed red on `windows-agent-readiness`
  ALONE (same `public-version-powershell` 30s-timeout flake; every other job green). `gh run rerun
  --failed` re-ran that job to restore green. This is the third sighting of this flake in as many
  runs (#968-era main, PR #1005 first pass, post-merge main) — a recurring environmental probe
  timeout, not a product regression; the retention diff is docs/skills/workflows/tests only and
  cannot affect it. Banked as a follow-up to make the probe lenient/longer rather than rerun-driven.
