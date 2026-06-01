---
name: tensor-grep
description: Use tensor-grep for repository code search, symbol lookup, blast-radius analysis, and edit planning when solving codebase tasks or preparing patches.
---

# Tensor-Grep Skill

Use this skill when you need to locate code precisely, understand likely edit impact, or prepare a minimal patch in a real repository. Treat `tg` as an agentic code-intelligence layer over a validated `rg`-compatible subset, not as a blanket faster-grep, full ast-grep, GPU, or LSP claim.

## When To Use

- You need the primary definition or source block for a symbol.
- You need references or likely callers before editing code.
- You need an edit plan, blast radius, or validation target instead of ad hoc grep loops.
- You are preparing a patch and want a smaller, more accurate context bundle.

## Default Workflow

1. Confirm the installed CLI is available:
   - `tg --version`
2. Start with direct text search when you need rg-shaped output:
   - `tg search --format rg "PATTERN" REPO_PATH`
   - `tg search --format rg --sort path "PATTERN" REPO_PATH`
   - Root shortcuts are also valid for common search flags: `tg "PATTERN" REPO_PATH`, `tg -t js "PATTERN" REPO_PATH`, and `tg --count-matches "PATTERN" REPO_PATH`.
3. Use agent/context commands for task routing:
   - `tg agent REPO_PATH --query "change invoice tax" --json`
   - `tg edit-plan REPO_PATH --query "change invoice tax" --json`
   - `tg context-render REPO_PATH --query "invoice flow" --json`
4. Use symbol commands in path-first order:
   - `tg defs REPO_PATH SYMBOL --json`
   - `tg source REPO_PATH SYMBOL --json`
   - `tg refs REPO_PATH SYMBOL --json`
   - `tg callers REPO_PATH SYMBOL --json`
   - `tg blast-radius REPO_PATH SYMBOL --json`
   - `tg blast-radius-plan REPO_PATH SYMBOL --json`
5. Use cached sessions for repeated edit loops:
   - `tg session open REPO_PATH --json`
   - `tg session edit-plan SESSION_ID REPO_PATH --query "change behavior" --json`
   - `tg session edit-plan SESSION_ID REPO_PATH --query "change behavior" --daemon --json`
6. Use the returned file/span candidates to make the smallest correct edit.
7. Run only the most relevant validation commands after the edit.

## Non-Interactive Mode

- In headless mode, do not ask for confirmation.
- After `tg` identifies the likely file/span, make the change directly instead of stopping at analysis.
- If direct editing is unavailable, emit a clean `git`-style unified diff only.

## Rules

- Prefer `tg` over repeated manual grep loops when working inside a real repository.
- Prefer `--format rg` for automation that expects ripgrep-shaped text output.
- Preserve common root search shortcut syntax when using the top-level entrypoint: `tg PATTERN PATH`, `tg -t js PATTERN PATH`, and `tg --count-matches PATTERN PATH` are treated as `tg search ...`.
- Keep edits narrow and grounded in the files `tg` ranks highest.
- Inspect `ambiguity` before editing; `tie_requires_confirmation` is a stop sign for autonomous edits.
- Do not expand context blindly if `tg` already identified the primary file and span.
- Use provider-backed modes only when a task is clearly about semantic ambiguity, and treat LSP as experimental unless `lsp_proof=true`.
- Keep GPU experimental unless a result proves `NativeGpuBackend`, `sidecar_used=false`, 1GB/5GB correctness, and speed wins over both `rg` and `tg_cpu`.
- Scope broad searches with paths, `--glob`, `--type`, or `--max-depth`; unbounded generated-root scans can be refused unless explicitly opted in.
- Do not return “what task should I do?” or “want me to apply this?” style responses.

## Provider Modes

- Default: `native`
- Optional:
  - `tg defs REPO_PATH SYMBOL --provider lsp --json`
  - `tg blast-radius REPO_PATH SYMBOL --provider hybrid --json`

Use `lsp` or `hybrid` only if native lookup seems ambiguous or incomplete. Provider availability is not semantic proof; check `health_status`, `lsp_proof`, and fallback fields.

## Windows Notes

- In PowerShell, single-quote patterns containing `$NAME` or escape `$`.
- In `cmd.exe`, quote or caret-escape metacharacters such as `|` and `&`.
- For MCP or stdio wiring on Windows, prefer the managed native `tg.exe` path reported by `tg doctor --json` over a `.ps1` shim.

## Patch Guidance

- Prefer editing files directly if your tools allow it.
- If you must emit a patch, make it a `git`-style unified diff with `diff --git` headers and enough context lines to apply cleanly.
- Avoid unrelated files, caches, summaries, or prose.

## Reference

See [REFERENCE.md](REFERENCE.md) for current command patterns and examples.

