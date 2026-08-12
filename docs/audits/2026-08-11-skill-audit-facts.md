# VERIFIED SKILL-AUDIT FACTSHEET (map-ledger) — 2026-08-11

> This is the ground-truth ledger for the skill-accuracy audit. Every claim below was re-derived
> LIVE from origin/main `a6242bb` (v1.110.14) on 2026-08-11, NOT transcribed from any doc. If a
> skill contradicts any row here, the SKILL IS WRONG — fix the skill.

## Current release state
- Latest tagged release: **v1.110.14** (tag `a6242bb`, PyPI serving 1.110.14, verified via
  cache-bypassed pypi.org query). Prior: v1.110.13 (A90), v1.110.12 (M17), v1.110.11 (M16).
- Skills that name a version in their dogfood/CUJ section must be ≥ v1.110.14 (or say
  "v1.110.14+").

## This session's product changes (v1.110.13 + v1.110.14)
1. **A90 fail-closed unknown commands (v1.110.13, PR #997):** unknown top-level commands now exit
   2 with `error.code=unknown_command` (+ `nearest[]`) on BOTH front doors. Specifically:
   - `tg edit-ready --help` / `--json` → exit 2, unknown_command (stdout EMPTY, stderr only).
   - Bare `tg PATTERN` / `tg PATTERN PATH` / reserved+positional-without-flag / `tg search <x>`
     remain SEARCH (never refused).
   - `RESERVED_TOP_LEVEL_COMMANDS = {edit-ready, verify-edit, workspace}` in commands.py —
     roadmap commands that must never be faked by search fall-through.
   - `nearest[]` = Levenshtein ≤ 3, hides `__` internal names, cap 5.
   - The old behavior (unknown → search help exit 0) is GONE. Any skill still describing
     "unknown command falls through to search" is WRONG.
2. **Doctor PATH-honesty (v1.110.14, PR #1000):** `tg doctor --json` schema now **3** (was 2).
   New fields: `pypi_latest` (best-effort probe), `installed_behind_pypi` (bool|None),
   `shadow_launchers[]` (foreign/mismatch/unverifiable routes), `installation_health`
   (foreign_launcher > unverifiable_version > launcher_version_mismatch > stale_install >
   unknown_pypi > ok). Human `tg doctor` prints a warning line when health != ok.
   New env knob: **`TG_DOCTOR_OFFLINE=1`** — disables the PyPI probe (doctor reports
   unknown_pypi instead of network; test/offline escape hatch). Any skill listing doctor env
   vars should include it; any skill stating doctor_schema_version == 2 is WRONG.
3. **Language symbol-graph tiers (Task 10E final wave, pre-session but verify):**
   `_symbol_navigation_descriptor()` returns:
   `parser-backed-refs-callers: c cpp csharp go java javascript php python rust typescript`
   `foundational-defs-imports-only:` (EMPTY).
   → **ALL 10 registered languages are parser-backed; foundational is EMPTY.**
   Any skill claiming "5 parser-backed / 5 foundational", "8 parser-backed", or a non-empty
   foundational tier is WRONG (several code-search-and-retrieval-reference lines contradict).

## Skill inventory (33 SKILL.md files, verified 2026-08-11)
code-search-and-retrieval-reference, tensor-grep, tensor-grep-add-language,
tensor-grep-architecture-contract, tensor-grep-argv-normalization-and-shadowing,
tensor-grep-backlog-campaign, tensor-grep-benchmark-and-proof-toolkit, tensor-grep-build-and-env,
tensor-grep-change-control, tensor-grep-codex-gated-audit-loop, tensor-grep-config-and-flags,
tensor-grep-cross-platform-path-confinement, tensor-grep-debugging-playbook,
tensor-grep-diagnostics-and-tooling, tensor-grep-docs-and-writing, tensor-grep-enterprise-agent,
tensor-grep-enterprise-review-bundle, tensor-grep-failure-archaeology, tensor-grep-find-and-route,
tensor-grep-gpu, tensor-grep-hermetic-hostile-tests, tensor-grep-index-fingerprint-freshness,
tensor-grep-large-repo-scale-campaign, tensor-grep-ledger, tensor-grep-multi-project-search,
tensor-grep-prepare, tensor-grep-release-and-positioning, tensor-grep-research-frontier,
tensor-grep-research-methodology, tensor-grep-run-and-operate, tensor-grep-semantic-search-campaign,
tensor-grep-validation-and-qa, tensor-grep-workspace-dogfood.

## Index docs
- AGENTS.md "Carrying the project forward" bucket list + CLAUDE.md mirror + root SKILL.md/REFERENCE.md
  must list the same set the governance test indexes (test_skill_index_sync.py / test_skill_library_drift.py
  pass on origin/main; the 32-in-index vs 33-on-disk is the test's own convention — do NOT "fix"
  the count unless the test changes).
- Never hand-count the language tier; derive via
  `python -c "from tensor_grep.cli import repo_map as r; print(r._symbol_navigation_descriptor())"`.