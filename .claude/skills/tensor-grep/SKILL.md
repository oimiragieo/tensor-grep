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
