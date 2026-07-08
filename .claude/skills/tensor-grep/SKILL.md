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
- You need to resume or persist cross-session repo-map context — use `tg session` to cache the repo-map, then call session-scoped commands (`tg session context-render`, `tg session edit-plan`, `tg session blast-radius-render`) without re-indexing on each invocation.

## Argument Order

All symbol commands are **path-first**: `tg <command> <REPO_PATH> <SYMBOL>`.
A reversed `<SYMBOL> <REPO_PATH>` call is auto-corrected with a stderr hint, and
a single `tg <command> <SYMBOL>` resolves against the current directory — but
prefer the canonical path-first form.

## Default Workflow

1. Confirm the installed CLI is available:
   - `tg --version`
0. (Unfamiliar repo) Orient before searching:
   - `tg orient REPO_PATH`
   Returns central files (by import in-degree), entry points, symbol map, and AST snippets in one call.
   - `tg inventory REPO_PATH --json` for a fast first-contact manifest (file/byte counts by language and by
     category, largest files, binary split) — walk-only, no AST parse, so it is cheap on a large repo.
2. Start with direct source lookup:
   - `tg source REPO_PATH SYMBOL`
2a. If the symbol name is unknown, find it by content first:
   - `tg search PATTERN REPO_PATH --rank`
   BM25 re-ranks results by per-chunk relevance — no API key, no GPU required.
   Then feed the top hit into `tg source`.
3. If you need symbol navigation:
   - `tg defs REPO_PATH SYMBOL`
   - `tg refs REPO_PATH SYMBOL`
4. If you need edit planning:
   - `tg blast-radius REPO_PATH SYMBOL`
   - `tg blast-radius-plan REPO_PATH SYMBOL`
5. Use the returned file/span candidates to make the smallest correct edit.
6. Run only the most relevant validation commands after the edit.
7. For repeated-edit loops or memory-backed work across sessions, open a cached session first:
   - `tg session open --json REPO_PATH` returns a `session_id` — capture it.
   Then pass that `session_id` as the required first argument to the session-scoped variants
   (instead of the equivalent top-level commands):
   `tg session context-render SESSION_ID`, `tg session edit-plan SESSION_ID`,
   `tg session blast-radius-render SESSION_ID`, `tg session blast-radius-plan SESSION_ID`,
   `tg session blast-radius SESSION_ID`.
   Refresh the cache after file changes with `tg session refresh SESSION_ID`.
   Inspect cached sessions with `tg session list` / `tg session show`.
   Manage the warm localhost daemon with `tg session daemon`.

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

**Unscoped search on a vendored root refuses instantly; it no longer just "hangs then times out."** `tg search PATTERN` with no path against a root whose top level contains a vendored dir (`node_modules`, `vendor`, `external_repos`, `third_party`) is refused in **under 1 second** (exit 2) by a top-level-only directory probe that never starts a walk (`_should_refuse_unbounded_vendored_root_scan`). When the root has no such top-level dir but is still large/unscoped, the native per-file search walk carries its own wall-clock bound and returns a flagged partial (`result_incomplete` + a stderr warning) instead of hanging; the rg-passthrough path is separately bounded by `TG_RG_TIMEOUT_SECONDS` (default 60 s, lowered from 600 s in #288). These bounds fire *before* the 60 s timeout on the common case, so don't present "fails fast after ~60 s" as the primary behavior. WORKAROUND (still the right default habit): always scope to a path — `tg search PATTERN C:\repo` completes in ~0.4 s and skips all of the above entirely.

**No scoped file-dependency primitive yet (v1.49.x).** There is no `tg imports` / `tg importers` / `tg deps <file>` command — the only import-graph view is whole-repo `tg map`/`tg orient`. On a real tokens-per-correct-answer benchmark, that made `tg` roughly **10x more expensive than a plain grep/read of the file's own import lines** for a "what does this file import" question (P4-class task) — the opposite of tg's win on definition-lookup. Until a scoped primitive ships, prefer `grep`/`Read` of `X`'s own import statements over `tg map` for a single-file dependency question; reserve `tg map`/`tg orient` for whole-repo architecture questions.

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
