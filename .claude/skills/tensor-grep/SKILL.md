---
name: tensor-grep
description: Use tensor-grep for repository code search, symbol lookup, blast-radius analysis, and edit planning when solving codebase tasks or preparing patches.
---

# Tensor-Grep Skill

Use this skill when you need to locate code precisely, understand likely edit impact, or prepare a minimal patch in a real repository.

## When To Use

- You need the primary definition or source block for a symbol.
- You need references or likely callers before editing code.
- You need an edit plan, blast radius, or validation target instead of ad hoc grep loops.
- You are preparing a patch and want a smaller, more accurate context bundle.
- You need a fast codebase orientation capsule (central files, entry points, symbol map) before diving into symbol lookup.
- You need to find code by text/content relevance rather than an exact symbol name.
- You need to find code by MEANING when you cannot predict a matching keyword or regex — use `tg find "QUERY" [PATH]` (experimental): whole-repo hybrid BM25 + dense-embedding ranking, no pattern pre-filter required.
- You need to resume or persist cross-session repo-map context — use `tg session` to cache the repo-map, then call session-scoped commands (`tg session context-render`, `tg session edit-plan`, `tg session blast-radius-render`) without re-indexing on each invocation.

## Argument Order

All symbol commands are **path-first**: `tg <command> <REPO_PATH> <SYMBOL>`.
A reversed `<SYMBOL> <REPO_PATH>` call is auto-corrected with a stderr hint, and
a single `tg <command> <SYMBOL>` resolves against the current directory — but
prefer the canonical path-first form.

## Default Workflow

1. Confirm the installed CLI is available:
   - `tg --version`
0. (Unfamiliar repo) Orient — single repo preferred; workspace root works but is slower (~53s on v1.71.1):
   - `tg orient REPO_PATH`
   - `tg inventory REPO_PATH --json`
2. File deps (cheap):
   - `tg imports FILE` / `tg importers FILE [ROOT]` — absolute paths
3. Content search then source:
   - `tg search PATTERN REPO_PATH --rank`
   - Workspace-root search is refused in ~1s unless scoped (`--glob` / `--type` / `--max-depth`) or `--allow-broad-generated-scan`
   - `tg source REPO_PATH/src SYMBOL`
   - No good pattern/keyword to anchor on? `tg find "natural language query" REPO_PATH` (experimental) ranks the whole repo by BM25 + dense relevance instead of requiring a regex match at all. `result_incomplete: true` + exit 2 means the scan/ranking covered only PART of the repo (raise `--max-repo-files` / `--deadline`); a missing `rank_fallback_reason` means the dense leg ran, present means it degraded to BM25-only (still a legitimate result, just lexical-only).
4. Symbol navigation — prefer `src/` for complete callers (root often returns `partial`):
   - `tg callers REPO_PATH/src SYMBOL --deadline 15 --json`
   - `tg defs` / `tg refs` / `tg blast-radius` similarly
5. Agent capsule — **prefer `src/` for latency on v1.71.1** (whole-repo `tg agent` is ~26s NATIVE, exit 0; the 75s figure is a WSL `/mnt/c` 9p artifact, not a regression):
   - `tg agent REPO_PATH/src "task" --json`  # ~24s PASS
   - Avoid `tg agent REPO_PATH "task"` on large trees until root latency is stable
5b. Evidence receipt:
   - `tg evidence emit REPO_PATH --capsule capsule.json --query "task" --json --agent-id AGENT`
5c. Optional browsable map (still slow — do not block agent loops):
   - `tg codemap REPO_PATH --out /tmp/code-map --json`
6. Make the smallest correct edit from primary targets.
7. Run only the returned validation commands.
8. Cached loops: `tg session open --json REPO_PATH` then session-scoped commands.

## Registration-Audit Workflow (blast-radius before claiming done)

When you add an entity that must be registered in multiple places (a command, a flag, a route, a hook), enumerate ALL its registration sites BEFORE claiming the change is done — missing one fails *quietly*. The default audit path:

1. **Blast radius** — `tg callers PATH SYMBOL --json` lists every call site (file:line). On a real billing repo it surfaced 2 webhook handlers + 1 reconcile cron in ~1s — a 10-minute grep-and-read became a one-second decision.
   When the JSON has `"result_incomplete": true`, the call-site list was TRUNCATED by a scan/output cap — treat coverage as partial; do not conclude unlisted sites are safe. Human mode emits a loud stderr caveat.
2. **Pattern bugs** — `tg scan PATH --ruleset RULESET` runs a built-in security/compliance rule pack across those sites (see `tg rulesets` for pack names). `--config sgconfig.yml` and `--rule FILE` are separate options for a custom ast-grep config or a single rule file — not for built-in packs.
3. **Diagnostics** — `tg doctor --with-lsp`.

For registration-completeness specifically: `tg callers PATH REGISTRATION_FUNCTION` lists *callable* registrations — but the call graph can't see set/list/decorator registrations (allow-lists, `@router.post`, dispatch tables), which are often the missed site, so grep / `tg scan` those too. Your new entry must appear in ALL sites. (General principle: `verify-plan-against-code` Hard Rule 6; call-graph blind spots: `tensor-grep-code-audit` P7.)
A resolved zero-caller result is NOT dead code either — the call graph can't see set/list/decorator/dispatch-table registrations; cross-check with `tg scan` or grep before removing a zero-caller symbol. As of v1.17.1 the registration-completeness checker (`extract_members`) is string/comment-aware, so `#`-commented entries are no longer surfaced as false members.

## Non-Interactive Mode

- When running in `claude -p` or other non-interactive automation, do not ask for confirmation.
- Use `tg` against the repository path that was added via `--add-dir`.
- After `tg` identifies the likely file/span, make the change directly instead of stopping at analysis.
- If direct editing is unavailable, emit a clean `git`-style unified diff only.

## Rules

- Prefer `tg` over repeated manual grep loops when working inside a real repository.
- Run `tg orient REPO_PATH` first when entering an unfamiliar repo — it gives centrality, entry points, and a symbol map in one call, and costs no API key or GPU.
- Use `tg search PATTERN PATH --rank` for content/text search; prefer it over raw grep loops when relevance ranking matters. The `--bm25` flag is an alias for `--rank`.
- Keep edits narrow and grounded in the files `tg` ranks highest.
- Do not expand context blindly if `tg` already identified the primary file and span.
- Use provider-backed modes only when a task is clearly about semantic ambiguity.
- In non-interactive mode, do not return “want me to apply this?” style responses.

## Known Issues

**Unscoped / multi-project workspace search refuses fast (v1.71.1).** `tg search TODO` from `/mnt/c/dev/projects` → exit 2 in ~1.1s with a clear safety-guard message (no more 60s hang). Scope with a project path, `--glob`, `--type`, `--max-depth`, or pass `--allow-broad-generated-scan` only when intentional.

**Prefer `REPO/src` for complete callers (still true on v1.71.1).** `tg callers tensor-grep/src … --deadline 15` → complete (3 callers, ~6s). Same symbol on the repo root → exit 2 / `partial: true` with 0 callers. Prefer narrowed PATH for exhaustive graph answers.

**Whole-repo `tg agent` is WSL-slow, NOT natively regressed on v1.71.1.** Native whole-repo runs ~26s (tensor-grep, exit 0, valid capsule); the 75s WSL `/mnt/c` timeout is a 9p-latency artifact -- reproduce natively before calling it a regression. Prefer `src/` (~24s) for latency in agent loops.

**Large JS/TS trees can still hang agent.** `tg agent` on `agent-studio` timed out at 60s in the v1.71.1 workspace sweep — narrow PATH further or raise budget only when needed.

**Workspace `tg orient .` works** (~53s on v1.71.1). Per-repo orient is faster (~24s).

**`tg codemap` is agent-loop-safe since #153 (v1.71.0).** Native: src `~15s`, whole-repo `~41s` (844 files), both `partial=false` complete; the default wall-clock deadline bounds it (returns `partial=true`, never hangs). The old "90s timeout" was WSL `/mnt/c` 9p amplification. Default `--out` is `docs/code-map`.

**`tg inventory --deadline` returns a floor** (workspace 525 files incomplete at 30s on v1.71.1). Prefer per-repo inventory without a tight deadline when totals must be trusted.

**`tg imports` / `tg importers`:** absolute paths; importers may be `partial` / empty under deadline even when reverse deps exist.

**`tg evidence emit`:** subcommand required; aggregates prior capsules (no re-scan unless `--recompute`).

**`tg classify`:** `FILE_PATH` only; default format already JSON (no `--json` flag).

**`tg checkpoint create`:** scope to `src/` on large trees.

**`tg scan` works again on WSL (v1.71.1)** for built-in rulesets (~1.4s PASS), but may warn about unreadable Windows-style paths under the Linux mount (`os error 3`). Treat findings as best-effort when those warnings appear; doctor `available: true` is still not a perfect runnable proof across shims.

## Provider Modes

- Default: `native`
- Optional:
  - `tg defs REPO_PATH SYMBOL --provider lsp`
  - `tg blast-radius REPO_PATH SYMBOL --provider hybrid`

Use `lsp` or `hybrid` only if native lookup seems ambiguous or incomplete.

## Patch Guidance

- Prefer editing files directly if your tools allow it.
- If you must emit a patch, make it a `git`-style unified diff with `diff --git` headers and enough context lines to apply cleanly.
- Avoid unrelated files, caches, summaries, or prose.

## Reference

See [REFERENCE.md](REFERENCE.md) for current command patterns and examples.
