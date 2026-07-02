# CLAUDE.md

Claude Code guidance for the **tensor-grep** repository.

> **All agent + contributor guidance lives in [AGENTS.md](AGENTS.md) — read it first.**
> Claude Code auto-loads this `CLAUDE.md`; `AGENTS.md` (read by other agents) holds the full rules, so
> this file points there to keep them DRY.

`AGENTS.md` covers, among other things:

- **Adding a Command or Flag** — the four registration sites for a new `tg` command and the two front
  doors for a new search flag (miss one and it silently misroutes to ripgrep).
- **Dogfood the Real Binary, Not CliRunner** — `CliRunner` bypasses the `bootstrap` front door; verify
  the shipped binary.
- **Verify AI-Drafted Plans Against the Real Code** — cite `file:line` for every seam claim before
  building.
- **Backend Fail-Closed Contract** — raise `BackendExecutionError` on failure; never return an empty
  result or silently swap engines for a contract flag (e.g. `--pcre2`).
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
- The ruff `--preview` (format only, not lint), line-ending, decode-the-structured-CI-failure-first,
  and release rules.

## Skills that apply here

- **Using `tg`**: `.claude/skills/tensor-grep/SKILL.md` (+ `REFERENCE.md`).
- **Carrying the project forward (in-repo onboarding library, 16 skills)**: `.claude/skills/tensor-grep-*`
  + `code-search-and-retrieval-reference` — the retirement handbook so a new engineer or a Sonnet-class
  session can debug, extend, validate, and advance `tg`. Change: `change-control`, `debugging-playbook`,
  `failure-archaeology`, `validation-and-qa`. Understand: `architecture-contract`,
  `code-search-and-retrieval-reference`, `config-and-flags`. Operate: `build-and-env`, `run-and-operate`,
  `diagnostics-and-tooling`, `docs-and-writing`, `release-and-positioning`. Advance:
  `semantic-search-campaign`, `benchmark-and-proof-toolkit`, `research-frontier`, `research-methodology`.
  Each auto-loads by task; this is the index.
- **Build/release discipline** (global, `~/.claude/skills/`): `dogfood-the-shipped-artifact`,
  `verify-plan-against-code`, `supply-chain-hardening`, `worktree-fanout-verification-gate`.
- **Post-release dogfood harness**: `scripts/dogfood/`.
