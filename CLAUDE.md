# CLAUDE.md

Claude Code guidance for the **tensor-grep** repository.

> **All agent + contributor guidance lives in [AGENTS.md](AGENTS.md) — read it first.**
> Claude Code auto-loads this `CLAUDE.md`; `AGENTS.md` (read by other agents) holds the full rules, so
> this file points there to keep them DRY.

`AGENTS.md` covers, among other things:

- **Adding a Command or Flag** — the four registration sites for a new `tg` command and the two front
  doors for a new search flag (miss one and it silently misroutes to ripgrep).
- **Dogfood the Real Binary, Not CliRunner** — `CliRunner` bypasses the `bootstrap` front door; verify
  the shipped binary. Separately: dogfood precision/heuristic features (classifiers, ranking weights)
  against a REAL, LARGE corpus, not just fixtures — fixture-green can't surface real vocabulary noise
  (the `tg find` whitespace classifier passed a synthetic literal-golden slice but mis-boosted 5/6 real
  identifiers when dogfooded).
- **Verify AI-Drafted Plans Against the Real Code** — cite `file:line` for every seam claim before
  building.
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
- **Local Dev Gotchas (Windows, hard-won)** — backticks in `git commit -m` run command substitution
  (use `-F`/heredoc); cargo/rustc off `PATH` and a "hanging" Rust build is slow LTO that finishes;
  verify FFI/bridge changes against the REAL extension (not mocks); apply post-merge fixes by SYMBOL
  not line number; a dependency upper-cap can silently downgrade the whole install on a newer Python.
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
- The ruff `--preview` (format only, not lint), line-ending, decode-the-structured-CI-failure-first,
  and release rules.

## Skills that apply here

- **Using `tg`**: `.claude/skills/tensor-grep/SKILL.md` (+ `REFERENCE.md`).
- **Carrying the project forward -- the in-repo skill library** (`.claude/skills/tensor-grep-*` + `code-search-and-retrieval-reference`, **26 skills**): the onboarding handbook so a new engineer or a Sonnet-class session can debug, extend, validate, and advance `tg` without the original authors. Each auto-loads by its `description`; load the one matching your task. Index by intent -- this exact bucket list is kept byte-identical with `AGENTS.md`'s skill index; `tests/unit/test_skill_index_sync.py` fails if either doc drifts from the real `.claude/skills/` folder set:
  - **Change safely:** `tensor-grep-change-control` (the gates), `tensor-grep-debugging-playbook`, `tensor-grep-failure-archaeology` (don't re-fight settled battles), `tensor-grep-validation-and-qa`.
  - **Understand:** `tensor-grep-architecture-contract`, `code-search-and-retrieval-reference` (domain theory), `tensor-grep-config-and-flags`.
  - **Operate:** `tensor-grep-build-and-env`, `tensor-grep-run-and-operate`, `tensor-grep-diagnostics-and-tooling`, `tensor-grep-docs-and-writing`, `tensor-grep-release-and-positioning`, `tensor-grep-workspace-dogfood` (multi-repo stress dogfood), `tensor-grep-enterprise-agent` (enterprise readiness gaps + agent hard-stops), `tensor-grep-prepare` (one-call edit readiness), `tensor-grep-ledger` (advisory multi-agent claim/finding-reuse), `tensor-grep-find-and-route` (whole-repo hybrid find + route-test), `tensor-grep-multi-project-search` (scoped cross-repo search), `tensor-grep-enterprise-review-bundle` (review-bundle create/verify), `tensor-grep-gpu` (experimental GPU probes).
  - **Advance (SOTA):** `tensor-grep-semantic-search-campaign`, `tensor-grep-benchmark-and-proof-toolkit`, `tensor-grep-research-frontier`, `tensor-grep-research-methodology`, `tensor-grep-large-repo-scale-campaign` (bounding scale/deadline on large repos).
  - **Orchestrate:** `tensor-grep-backlog-campaign` (the multi-PR drain+build campaign playbook).
- **Build/release discipline** (global, `~/.claude/skills/`): `dogfood-the-shipped-artifact`,
  `verify-plan-against-code`, `supply-chain-hardening`, `worktree-fanout-verification-gate`,
  `anti-hang-test-protocol` (hang-class test hygiene: shell-timeout + fix-before-red-test),
  `instrumented-build-gate` (measure demand before building a speculative feature),
  `agent-liveness-probe` (probe via `SendMessage` before killing/`TaskStop`-ing a stalled subagent).
- **Post-release dogfood harness**: `scripts/dogfood/`.
- `.claude/skill_rules.json` is harness config for the skill-activation hook, not a product contract —
  it has no `SKILL.md` and is invisible to `test_skill_index_sync.py`.
