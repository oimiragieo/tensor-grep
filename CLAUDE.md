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
- The ruff `--preview` (format only, not lint), line-ending, decode-the-structured-CI-failure-first,
  and release rules.

## Skills that apply here

- **Using `tg`**: `.claude/skills/tensor-grep/SKILL.md` (+ `REFERENCE.md`).
- **Build/release discipline** (global, `~/.claude/skills/`): `dogfood-the-shipped-artifact`,
  `verify-plan-against-code`, `supply-chain-hardening`, `worktree-fanout-verification-gate`.
- **Post-release dogfood harness**: `scripts/dogfood/`.
