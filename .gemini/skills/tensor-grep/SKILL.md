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

## Default Workflow

1. Confirm the installed CLI is available:
   - `tg --version`
2. Start with direct source lookup:
   - `tg source SYMBOL REPO_PATH`
3. If you need symbol navigation:
   - `tg defs SYMBOL REPO_PATH`
   - `tg refs SYMBOL REPO_PATH`
4. If you need edit planning:
   - `tg blast-radius SYMBOL REPO_PATH`
   - `tg blast-radius-plan SYMBOL REPO_PATH`
5. Use the returned file/span candidates to make the smallest correct edit.
6. Run only the most relevant validation commands after the edit.

## Non-Interactive Mode

- In headless mode, do not ask for confirmation.
- After `tg` identifies the likely file/span, make the change directly instead of stopping at analysis.
- If direct editing is unavailable, emit a clean `git`-style unified diff only.

## Rules

- Prefer `tg` over repeated manual grep loops when working inside a real repository.
- Keep edits narrow and grounded in the files `tg` ranks highest.
- Do not expand context blindly if `tg` already identified the primary file and span.
- Use provider-backed modes only when a task is clearly about semantic ambiguity.
- Do not return “what task should I do?” or “want me to apply this?” style responses.

## Provider Modes

- Default: `native`
- Optional:
  - `tg defs SYMBOL REPO_PATH --provider lsp`
  - `tg blast-radius SYMBOL REPO_PATH --provider hybrid`

Use `lsp` or `hybrid` only if native lookup seems ambiguous or incomplete.

## Patch Guidance

- Prefer editing files directly if your tools allow it.
- If you must emit a patch, make it a `git`-style unified diff with `diff --git` headers and enough context lines to apply cleanly.
- Avoid unrelated files, caches, summaries, or prose.

## Reference

See [REFERENCE.md](REFERENCE.md) for current command patterns and examples.

