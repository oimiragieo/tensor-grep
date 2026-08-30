# CLAUDE.md

Claude Code guidance for the **tensor-grep** repository.

> **All agent + contributor guidance lives in [AGENTS.md](AGENTS.md) — read it first.**
> Claude Code auto-loads this `CLAUDE.md`; `AGENTS.md` (read by other agents) holds the full rules, so
> this file points there to keep them DRY.

`AGENTS.md` covers, among other things:

- **The evidence laws — the largest and most load-bearing thing in `AGENTS.md`, and the reason to read
  it before trusting any green signal.** Two families, both keyed to one question: *what would this
  check show if the thing it verifies were BROKEN? If the answer is "the same", it is not
  verification.*
  - **The verification-oracle family** — enumerate with `grep -nE '^\*\*Form [0-9]+ ' AGENTS.md`.
    MIRRORED in `.claude/skills/tensor-grep-validation-and-qa/SKILL.md` Part 0: **adding a Form is a
    two-file edit**, and `tests/unit/test_skill_library_drift.py` fails if the stated count and the
    enumerated forms disagree in either file. Do not hand-count — the header was wrong in both files,
    in opposite directions, for four days.
  - **The dated instrument laws** — enumerate with
    `grep -nE '^#{2,4} .*\(20[0-9]{2}-[0-9]{2}-[0-9]{2}' AGENTS.md`. Each is a receipt, not a maxim:
    a probe that returned a believable number and was wrong. The recurring shape is that **the
    instrument fails more often than the subject** — a blocked instrument and a definitive negative
    look identical, a grep zero is UNRESOLVED rather than ABSENT, and after a fix a grep hit is
    usually the fix's own docstring (count AST nodes, not substrings).

  Counts are deliberately not written here. A number in a third file is a third place to drift, and
  this repo has now been burned by exactly that — including by prose enumerations of its own laws.
- **Adding a Command or Flag** — the four registration sites for a new `tg` command and the two front
  doors for a new search flag (miss one and it silently misroutes to ripgrep).
- **Dogfood the Real Binary, Not CliRunner** — `CliRunner` bypasses the `bootstrap` front door; verify
  the shipped binary. Separately: dogfood precision/heuristic features (classifiers, ranking weights)
  against a REAL, LARGE corpus, not just fixtures — fixture-green can't surface real vocabulary noise
  (the `tg find` whitespace classifier passed a synthetic literal-golden slice but mis-boosted 5/6 real
  identifiers when dogfooded).
- **Verify AI-Drafted Plans Against the Real Code** — cite `file:line` for every seam claim before
  building. Approval is exact-artifact-specific: a green PR does not clear newer worktree bytes, and
  architecture `SHIP` does not replace adversarial-security `SHIP`. Cursor/cheaper-model output is a
  hypothesis until Sol validates the exact resulting bytes.
- **Security plans name real primitives** — PATH never discovers installer authority; path spelling is
  not opened object identity; “CAS”/“trusted signer”/“kill descendants” must name the platform API,
  exact flags, protected authority root, failure behavior, and adversarial RED. Resource ledgers begin
  before every bootstrap/full/native/delegation route, with mixed inclusive cap tests.
- **Static manifests are not live receipts** — committed manifests define required nodes without live
  run IDs; verifiers re-derive the Actions/artifact tuple and cross-check Python JUnit plus Rust census.
  A broad review timeout should be retried on the exact paragraph; a no-verdict seat is failed, not
  approval or an infinite wait. Search deferred tools before declaring Exa unavailable.
- **Backend Fail-Closed Contract** — raise `BackendExecutionError` on failure; never return an empty
  result or silently swap engines for a contract flag (e.g. `--pcre2`).
- **AST Native/Wrapper Two-Engine Divergence (task #141)** — the ast-grep wrapper and native
  tree-sitter `AstBackend` speak different DSLs; the metavar (`$NAME`/`$$$ARGS`) fail-closed guard
  already exists at 3 sites (`ConfigurationError`, never a silent native mis-route); the native-shaped
  fallback to tree-sitter when ast-grep is absent is deliberate (CPU box still gets AST); full DSL
  parity stays demand-gated.
- **`tg find` (whole-repo hybrid NL search, v1.77.0 CLI / v1.78.0 MCP)** — the CPU semantic moat: BM25 +
  CPU dense embeddings → RRF → budget-fitted output, plus the default-OFF `TG_FIND_DENSE_WEIGHT` knob
  gated by a whitespace NL-vs-literal query classifier. A new MCP tool is a 5th registration site (bump
  `_TG_MCP_SERVER_CONTRACT_VERSION`); score ranking changes on the retrieval-quality benchmark.
- **Security Hardening Patterns (Round-3 audit lens)** — four sweep targets when touching those areas:
  symlink-follow disclosure (no `followlinks`); pre-auth unbounded-read DoS (bound + timeout before
  auth); atomic-write permission window (`os.open(O_CREAT\|O_EXCL, mode)`, not write-then-chmod); and
  native-argv flag injection (`--` sentinel before user positionals; list-argv blocks shell but not
  flag injection — CWE-88 / the MCP-276 CVE class).
- **Push Discipline / the push-race** — the real publish is the `Semantic Release` job in `ci.yml`, and
  it runs ~6 min (native-asset compile). Merging *anything* onto `main` during that window — even a
  no-release `docs:`/`chore:` PR — rejects the in-flight release's push (`! [rejected] main -> main`).
  Wait for the prior `chore(release)` commit + PyPI before the next merge; a failed release self-heals
  on the next push (don't panic-rerun).

  **RELEASE CLASS IS PART OF THE FIX.** `scripts/validate_pr_title_semver.py` maps `feat`→minor,
  `fix`/`perf`/**`refactor`**→patch, and `chore`/`docs`/`test`/`ci`/`build`/`bench`→**none**.
  **`refactor:` DOES NOT PUBLISH, and this file asserted the opposite until 2026-08-04.** There are
  TWO release-class systems and they DISAGREE — deriving from only one is how this line was wrong
  twice. `scripts/validate_pr_title_semver.py`'s `_RELEASE_INTENTS` maps `refactor`→`patch`, but that
  script only gates the PR TITLE; it does not publish anything. The publisher is
  `[tool.semantic_release]` in `pyproject.toml`, which configures **no** `commit_parser` /
  `allowed_tags` / `patch_tags`, so python-semantic-release falls back to its DEFAULT angular parser
  whose patch types are `fix` and `perf` ONLY. Measured on PR #915 (`refactor:`, merged `3faf500`):
  Semantic Release logged *"No release will be made, 1.102.4 has already been released!"*,
  `publish-pypi` was SKIPPED, no tag, PyPI unchanged.
  So ask BOTH: `grep -A12 _RELEASE_INTENTS scripts/validate_pr_title_semver.py` for what the title
  gate will ACCEPT, and the `[tool.semantic_release]` block for what will actually SHIP.
  The code is not lost — an unreleased `refactor:` publishes with the next `fix:`/`feat:` merge — but
  a refactor-ONLY run leaves `main` unpublished while every tracker reads "shipped". The repo
  squash-merges, so the PR TITLE is *usually* the release semantic — **but not always, and the
  exception bit me on 2026-08-19.** GitHub's squash uses the PR title only when the PR has MORE
  THAN ONE commit; with a **single-commit PR it defaults to that commit's own subject**, and
  retitling the PR changes nothing. PR #1036 was deliberately titled `ci:` to avoid a release and
  merged as `feat: bare-call ratchet …`, because it was one commit whose message said `feat:`.
  Result: a MINOR version bump published for a dev-only CI gate, against an explicit prediction
  of "no release". **Semantic-release parses the COMMIT ON MAIN, never the PR title** — so verify
  with `git log --format='%s' <last-tag>..origin/main | grep -E '^(fix|feat|perf)'` AFTER merging,
  not by reading the PR. On a single-commit PR, fix the COMMIT subject (amend and force-push the
  branch) or add a second commit; retitling alone is a no-op. A CWE-88 security fix was scoped as a
  `chore:` PR on 2026-08-01 — it would have merged, closed the ticket, and **never published**, with
  every tracker reading "shipped". Ask what the title does to the release BEFORE merging.
  **Committed is not shipped, and merged is not released**: verify on the PUBLISHED artifact, and
  reconcile the board AT completion, never "next cycle" (17 stale items accrued one deferral at a
  time).
- **Local Dev Gotchas (Windows, hard-won)** — backticks in `git commit -m` run command substitution
  (use `-F`/heredoc); cargo/rustc off `PATH` and a "hanging" Rust build is slow LTO that finishes;
  verify FFI/bridge changes against the REAL extension (not mocks); apply post-merge fixes by SYMBOL
  not line number; a dependency upper-cap can silently downgrade the whole install on a newer Python.
  **`git stash` is UNSAFE once parallel worktrees exist** — worktrees share `.git`'s stash refs, so N
  agents reach into ONE drawer; a red-arm revert took a different agent's stash on 2026-08-02. Revert
  with `git checkout -- <file>` or a patch file. Rescue an orphaned stash non-destructively with
  `git branch <name> stash@{0}` (a permanent ref, no checkout, no pop). This applies to every
  worktree campaign this file tells you to run. Keep WSL and Windows venv roots disjoint: never run
  WSL `uv --project /mnt/c/...` against the canonical checkout, because `uv` may replace the Windows
  `.venv` with an incompatible Linux environment (AGENTS.md A60).
  **RED/CI evidence laws (A61–A93):** behavioral RED pins the exact expected reason
  (crash/import/panic/setup ≠ RED); route/start evidence comes from the real producer plus test-owned
  OS/raw proof, never a hardcoded bool or production self-attest; containment authenticates
  writer/client provenance and proves alive-before→dead-after plus cleanup; crypto negatives need a
  valid API operation, exact refusal class, and exportable positive control; security grammar
  validates full sections/types/flags/effective authority and rejects unknown/inherit-only; resource
  protocols name close primitives and prove exact-once reverse cleanup; RED scaffolds cannot enable
  partial public or unbounded pre-guard work; immutable-SHA CI clearance needs a real run with
  expected per-node outcomes and raw artifacts (no run = no clearance); security green is point-in-
  time, so a fresh fixable advisory blocks merge and is upgraded across direct/constraint floors,
  lock, validators, and remediation text before a new exact-head audit — never ignored.
  **A77–A82 (2026-08-06 PM):** never pipe `gh pr checks` into a stdin-eating heredoc (false
  ALL_TERMINAL); usage-limit seats are FAILED not pending; READY→BLOCKED stamps must retarget
  governance pins; gate tip bytes not archaeological RED SHAs; HIGH receipts ≠ Sol SHIP;
  AMEND_SPINE when board READY contradicts reconcile BLOCKED (START_NOW = docs/R0/D1 only).
**A83–A89 (2026-08-08/09):** argv front-door rewrite shadowing (census the normalizer's rewrite list + the target parser, not just the guarded door — SEARCH_OPTION_FIRST_FLAGS → search form drops gpu_device_ids, #979); cross-platform path gating (platform-gate the drive-absolute strip on `os.name == "nt"`, an unconditional strip recreates the escape on POSIX, #983); env-independent gated tests (force the optional-engine seam, never env-detect, mutation-control REDs, #984); stale-ready labels (cite the head SHA's own completed run, rebase before re-labeling, #967/#977); static review ≠ typecheck — hold SHIP until first CI compiles (#987/#988); dogfood fixtures must bite (verify the hostile setup applied, M1); real-artifact parity arms beat fake-backed ones (ast-grep byteOffset).
  **A90–A93 (2026-08-09, world-class framing):** fail closed on unknown subcommands — never fall through to search (`bootstrap.py` `_normalize_search_invocation` prints search help for `tg edit-ready --help` exit 0; unknown commands must exit 2 with `nearest[]` on BOTH doors); "no core-Rust logic" never means "no native touch" — every Python/sidecar feature slice must enroll both front doors + the 4-site parity test or it is invisible through the managed native `tg.exe`; executed evidence must be escrowed to a key the verified principal does NOT hold (CI-held; stdout-hash+exit+duration; absent that → UNVERIFIED, never PASS) and verification must fail closed on tree drift (ticket carries base_sha+fingerprint); self-dogfood is self-consistency not demand — premise-check a plan's "banked/shipped" claims against origin/main before the design council reads it.
  **A94-A96 (2026-08-11, skill-library freshness):** skill/doc version stamps rot one release after the last refresh (21 stale stamps + 7 tier contradictions found ONE release later) - freshness is a maintenance sweep, not a one-time event: run the tensor-grep-release-drift-check skill after every release (version-stamp grep vs current tag, re-derived counts via _symbol_navigation_descriptor() / folder set, known-state facts, append-only SUPERSEDED for dated claims - deliberately NOT a pytest, numbers drift by design); a '**N skills** is VERIFIED CORRECT - do not fix it' note is PART of the contract it guards - adding a folder means updating the count AND the note's own re-derivation echo AND the AGENTS.md mirror in the same change; non-ASCII punctuation (em/en dashes) defeats byte-exact edit-tool matches - splice by line index in a python script file with assertions, never quote the line. **A97-A102 (2026-08-13, retention campaign):** interrupted-edits-may-have-applied (read back before retry); spot-check censuses claim only the checked file; verifiers must bind root+SHA+manifest; advertised workflow phases must execute; third flake = structural fix; brief facts are hypotheses. **A103-A110 (2026-08-13, backlog-closeout campaign):** snapshot builder bytes before baseline swaps; the A3 gate is a 10+ round real-finding loop ending on independent SHIP; normalize the path before a no-follow stat and own the residuals; silent skips are hazards (env-var panic promotion in CI); settle contested platform facts with a bounded pinned-toolchain probe and mark superseded laws; hash-freeze council rounds with named gates; capacity-1 handshakes only; amend only pre-push.
  **A111–A116 (2026-08-14, session-capture):** docs must not cite plan/spec paths
  missing from the merged tree, and committing a previously-untracked approved artifact records
  the pre-format witness hash AND the committed hash; a plan-frozen control threshold is met
  verbatim or the arm is CANNOT_MEASURE (recharacterizing the frozen number is a plan violation);
  claim only what the raw artifact discriminates (an undifferentiated `TimeoutError` cannot
  become a specific class in prose); a corrected census closes only after its location inventory
  is mechanically re-derived; wave receipts are per-row tables, never group sentences; never let
  `uv run` create a venv inside a bare worktree (run worktree tests from the main checkout's
  venv targeting worktree paths). **A117–A122 (2026-08-15, DD-006 design-packet closeout):** operator “skip Fable” waives that seat for the named docs packet only — not product code, spend, or CEO_GATED flips (extends A74); local `gh pr merge` failure is not remote truth when another worktree owns `main` (judge `mergedAt` / API); docs-only PR job skips are not a cheap main push; enclosing shell timeout must strictly exceed probe duration (+ grace); raising `request_queue_size` without a fail-closed aggregate pre-auth concurrency cap enlarges DoS admission; demand SATISFIED + design on main is not SHIPPED (parent DD-006 still needs PERF + HONESTY product code under a deliberate build go).
  **A123 AND EVERYTHING AFTER IT ARE DELIBERATELY NOT SUMMARISED HERE.** This prose enumeration
  stopped at A122 while `AGENTS.md` reached A153 -- 31 laws that nothing in this file pointed at,
  which is the exact "an enumeration in prose rots the moment the set grows" failure this repo has
  receipts for. Enumerate them from the source instead, never from this paragraph:
  `grep -nE '^- \*\*A[0-9]+ ' AGENTS.md` (count-free by design -- a number here is a third place
  to drift). Read the tail of that output at session start; it is where the newest receipts land.
- **Campaign Orchestration Disciplines (2026-07-08, extended 2026-07-16, 2026-07-22)** — running a
  multi-PR drain+build campaign so fixes *land*: the WIP cap, the self-firing drain-cron (beats a
  long-lived background drain), the mandatory adversarial security gate before merge,
  resume-a-dead-agent-from-transcript (on a transient 500), don't-kill-a-slow-build-on-staleness, the
  anti-hang test protocol, harvest, Fable-only-via-`Agent`, probe-liveness-via-`SendMessage`-before-
  `TaskStop`, the CPU-safe shared-server discipline (route CPU-heavy work to cloud subagents/CI, never
  this desktop — A12), treating a no-verdict council seat as a FAILED seat rather than a blocker (A10),
  and design-review-before-build (Fable plans → a thinktank certifies the plan → Sonnet builds TDD-first
  → a mandatory adversarial gate, A11). Nine further disciplines from the 2026-07-22 session-capture
  wave — rapid-window batch-merge, event-driven release watching, pin-first ranking gates,
  scheduler-independent concurrency tests, independent-gate-is-a-hypothesis, gate-nit folding,
  published-wheel closing dogfood, and the loop-4 accuracy gate (A13-A21) — are in `AGENTS.md`'s full
  list; this bullet is the gist, not the copy.
- **Adding a Language** — the `lang_registry.register_language` + `lang_<x>.py` module
  pattern (mirror `lang_go.py`, not inline `_rust_*`) and the 5 critical seams
  (most-forgotten: `_target_language_for_path`, the capsule confidence gate).
- **Optimization Discipline** — measure-first, cProfile the shipped wheel, byte-identical
  PROOF (enumerate + differential-fuzz), and the warm-dogfood-hides-a-cold-path-win trap.
- The ruff `--preview` (format only, not lint), line-ending, decode-the-structured-CI-failure-first,
  and release rules.

## Skills that apply here

**User-level composition is not listed here.** Load `~/.claude/skills/skill-library-map/` then 1-3 leaves. Plan vs answer-key vs execute vs verify is `compose-build-pipeline`.

- **Using `tg`**: `.claude/skills/tensor-grep/SKILL.md` (+ `REFERENCE.md`).
- **Carrying the project forward -- the in-repo skill library** (`.claude/skills/tensor-grep-*` + `code-search-and-retrieval-reference`, **37 skills**): the onboarding handbook so a new engineer or a Sonnet-class session can debug, extend, validate, and advance `tg` without the original authors. Each auto-loads by its `description`; load the one matching your task. Index by intent -- this exact bucket list is kept byte-identical with `AGENTS.md`'s skill index; `tests/unit/test_skill_index_sync.py` fails if either doc drifts from the real `.claude/skills/` folder set, and `tests/unit/test_skill_library_drift.py` additionally pins every `file:line` citation (must resolve to a git-tracked file, line in range) and the stated `**N skills**` count against the folders that sentence names. **Neither gate can tell you a skill is CORRECT** — they prove a citation resolves, not that the cited line still contains the claimed symbol. Anchors drift 14-500 lines while resolving perfectly; run `/tg-skill-audit` (`.claude/workflows/tg-skill-audit.js`) for that half, and never fix drift by re-stamping a new line number (see AGENTS.md, "Cite the SYMBOL, not the line").

  **And no gate we own compares a document to ITSELF.** Fix a fact → grep the WHOLE doc for the old anchor: a 2026-08-02 audit found `tensor-grep-benchmark-and-proof-toolkit` shipping a corrected citation AND its refuted duplicate 150 lines apart, in one file. **Re-stamping is the live failure mode, not a hypothetical:** the 2026-08-01 anchor pass re-stamped `#578` from `:603` to `:850`, and `:850` was already wrong the next day (real hits: 977/978/1158). Both of those skills now carry the grep with no line number at all.

  **The `**37 skills**` count above is VERIFIED CORRECT — do not "fix" it.** 38 folders exist; the bare `tensor-grep/` folder is usage-docs, deliberately uncounted. A sibling skill's frontmatter had already drifted to "26-skill library" (fixed 2026-08-02). Re-derive before changing: `ls .claude/skills/ | grep -c '^tensor-grep-'` (36) + `code-search-and-retrieval-reference`.

  **Never hand-count the language tiers** -- ask the product: `repo_map._symbol_navigation_descriptor()` returns **10 parser-backed** (c, cpp, csharp, go, java, javascript, php, python, rust, typescript) **/ 0 foundational**, 10 registered, no third tier -- the foundational tier is now EMPTY. Java (Task 10A, PR #927), then C# (Task 10B), then PHP (Task 10C), then C (Task 10D), then C++ (Task 10E, the final wave) moved from foundational to parser-backed, all in-file refs/callers only -- cross-file caller resolution still falls back to the text prefilter pending a package/source-root resolver. That number has been wrong four times, once inside a skill -- re-run the one-liner rather than trust this line.

  Skills by intent:
  - **Change safely:** `tensor-grep-change-control` (the gates), `tensor-grep-debugging-playbook`, `tensor-grep-failure-archaeology` (don't re-fight settled battles), `tensor-grep-validation-and-qa`, `tensor-grep-hermetic-hostile-tests` (env-independent gated tests + hostile fixtures that must BITE), `tensor-grep-cross-platform-path-confinement` (junction vs symlink vs drive-absolute confinement, Windows+POSIX), `tensor-grep-release-drift-check` (post-release sweep: version stamps, derived counts, known-state facts vs the current tag, SUPERSEDED append-only fix discipline), `tensor-grep-local-ci-parity-harness` (run the shared-box-banned lanes in a CPU-capped container; the 12 container-vs-runner divergences; act vs a hand-written harness).
  - **Understand:** `tensor-grep-architecture-contract`, `code-search-and-retrieval-reference` (domain theory), `tensor-grep-config-and-flags`, `tensor-grep-argv-normalization-and-shadowing` (front-door rewrites, `--` hygiene, shape-monotonic routing), `tensor-grep-index-fingerprint-freshness` (index reuse/staleness identity, M17).
  - **Operate:** `tensor-grep-build-and-env`, `tensor-grep-run-and-operate`, `tensor-grep-diagnostics-and-tooling`, `tensor-grep-docs-and-writing`, `tensor-grep-release-and-positioning`, `tensor-grep-workspace-dogfood` (multi-repo stress dogfood), `tensor-grep-enterprise-agent` (enterprise readiness gaps + agent hard-stops), `tensor-grep-worldclass-roadmap` (the edit-control-plane roadmap: S1 verify-edit escrow, S2-S7 contracts, H1), `tensor-grep-prepare` (one-call edit readiness), `tensor-grep-ledger` (advisory multi-agent claim/finding-reuse), `tensor-grep-find-and-route` (whole-repo hybrid find + route-test), `tensor-grep-multi-project-search` (scoped cross-repo search), `tensor-grep-enterprise-review-bundle` (review-bundle create/verify), `tensor-grep-gpu` (experimental GPU probes).
  - **Advance (SOTA):** `tensor-grep-semantic-search-campaign`, `tensor-grep-benchmark-and-proof-toolkit`, `tensor-grep-research-frontier`, `tensor-grep-research-methodology`, `tensor-grep-large-repo-scale-campaign` (bounding scale/deadline on large repos), `tensor-grep-demand-gate-measurement` (the bounded demand-gate measurement method with the DD-006 worked example), `tensor-grep-design-authorization-ladder` (demand→design packet→Sol→optional Fable waiver→deliberate build; A117/A122).
  - **Extend:** `tensor-grep-add-language` (the symbol-graph language-onboarding checklist).
  - **Orchestrate:** `tensor-grep-backlog-campaign` (the multi-PR drain+build campaign playbook), `tensor-grep-codex-gated-audit-loop` (the per-item codex-gated fix loop: RED→codex gate→re-audit→SHIP; env-independent gated tests).
- **Evidence discipline** (global, `~/.claude/skills/`) — **load one of these before trusting a green
  signal or acting on a measured number; they are the general form of the AGENTS.md evidence laws
  above, and this repo's dominant failure mode is a check that reports GREEN.**
  - **`detect-the-false-green`** — about to trust a passing suite, a zero-match grep, a clean gate
    run, or a count that confirms your prediction. Especially when the green licenses a claim
    ("fixed", "clean", "safe to delete") or the check has never been observed to fail.
  - **`author-a-probe-that-cannot-lie`** — BEFORE writing any script whose number you will act on
    (latency, throughput, cost, hit-rate, pass-rate). Positive control, blind-vs-busy empty results,
    arm interleaving, max-not-mean, shared-resource pollution windows. The dev box is shared, so the
    pollution section is not optional here.
- **Build/release discipline** (global, `~/.claude/skills/`): `dogfood-the-shipped-artifact`,
  `verify-plan-against-code` (its **Step 0** is the premise check: is the work still needed? A plan
  against a fixed bug has perfectly resolving citations), `supply-chain-hardening`,
  `worktree-fanout-verification-gate`,
  `anti-hang-test-protocol` (hang-class test hygiene: shell-timeout + fix-before-red-test),
  `instrumented-build-gate` (measure demand before building a speculative feature),
  `agent-liveness-probe` (probe via `SendMessage` before killing/`TaskStop`-ing a stalled subagent),
  `profile-guided-byte-identical-optimization` (find a lever on the shipped wheel + prove
  output byte-identical; the warm/cold measurement trap).
- **Post-release dogfood harness**: `scripts/dogfood/`.
- `.claude/skill_rules.json` is harness config for the skill-activation hook, not a product contract —
  it has no `SKILL.md` and is invisible to `test_skill_index_sync.py`.
