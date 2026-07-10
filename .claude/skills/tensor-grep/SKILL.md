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
2. For a **single file's import edges** (cheap — no repo scan):
   - `tg imports FILE` — what FILE imports, resolved to target files where possible
   - `tg importers FILE [ROOT]` — who imports FILE (bounded reverse lookup; use `--deadline` on large roots)
   Prefer these over `tg map`/`tg orient` for one-file dependency questions.
3. Start with direct source lookup:
   - `tg source REPO_PATH SYMBOL`
3a. If the symbol name is unknown, find it by content first:
   - `tg search PATTERN REPO_PATH --rank`
   BM25 re-ranks results by per-chunk relevance — no API key, no GPU required.
   Then feed the top hit into `tg source`.
4. If you need symbol navigation:
   - `tg defs REPO_PATH SYMBOL`
   - `tg refs REPO_PATH SYMBOL`
5. If you need edit planning:
   - `tg blast-radius REPO_PATH SYMBOL`
   - `tg blast-radius-plan REPO_PATH SYMBOL`
6. Use the returned file/span candidates to make the smallest correct edit.
7. Run only the most relevant validation commands after the edit.
8. For repeated-edit loops or memory-backed work across sessions, open a cached session first:
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

**Unscoped search on a multi-project workspace often hangs until the 60s ripgrep timeout (v1.58.9 dogfood).** Skills previously claimed vendored-root refusal in <1s; on `/mnt/c/dev/projects` (which has a top-level `node_modules`) `tg search TODO` still burned the full timeout with no early refuse message. Always scope to a path — `tg search PATTERN REPO` completes in under a second on typical repos.

**Scoped file dependencies (`tg imports` / `tg importers`, shipped #74).** Use `tg imports FILE` for forward edges (O(1) — parses one file) and `tg importers FILE [ROOT]` for reverse edges (bounded repo scan). Do **not** pay for whole-repo `tg map`/`tg orient` when the question is "what does this file import?" or "who imports this file?".

**`--deadline` is best-effort, not a hard SLA (v1.58.x dogfood).** Graph scans may still exceed the requested budget. On v1.58.9, `callers`/`blast-radius`/`impact` with `--deadline 15–20` typically finish in ~17–24s and correctly exit `2` with `partial: true` when truncated. Treat `partial`/`result_incomplete` as the honesty signal. Narrow `PATH` or warm `tg session daemon start` before trusting caller graphs on large trees.

**Workspace-root `tg inventory --deadline` can return zero files (v1.58.9 dogfood).** On a multi-project parent like `/mnt/c/dev/projects`, a short `--deadline` may expire before any files are counted (`totals.files=0`, `truncation_cause=deadline`). Prefer `tg inventory REPO` per project, or raise the deadline substantially for the workspace parent.

**Unscoped search on this workspace still hits the 60s ripgrep timeout (v1.58.9).** Even with a top-level `node_modules`, `tg search TODO` from `/mnt/c/dev/projects` timed out at ~60s (exit 124) instead of refusing in <1s. Always scope to a repo path.

**WSL + `/mnt/c/` path quirks.** Some native-backend searches report `path_not_found` for absolute `/mnt/c/dev/...` paths even when the directory exists; relative paths from the repo cwd often work. If `tg search` returns `path_not_found`, `cd` into the parent and pass a relative path.

**`tg importers` path resolution.** Prefer absolute paths for both `FILE` and `ROOT` (v1.58.9 dogfood: absolute paths succeed; relative `FILE`+`ROOT` from a parent cwd can still double-resolve). Or `cd` into `ROOT` and pass `FILE` relative to that cwd only.

**`tg classify` takes `FILE_PATH` only — no `--json` flag.** Default `--format` is already `json`. Do not pass `--json` (Typer usage error). There is no stdin/`--text` mode yet.

**`tg checkpoint create` on a whole large repo can fail** when the tree contains awkward paths (v1.58.9: `Is a directory` under `benchmarks/external_repos/chalk`). Scope the checkpoint to `src/` or a narrower editable subtree.

**AST scan on WSL when ast-grep is a Windows npm shim.** `tg scan` may fail with exit 127 if `doctor` resolves `ast-grep` to a Windows path (`/mnt/c/Users/.../npm/ast-grep`) whose shebang cannot execute under WSL. Install a Linux-native `ast-grep` on PATH or run scan from Windows. Doctor may still report `ast_grep.available: true` — availability ≠ runnable under WSL.

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
