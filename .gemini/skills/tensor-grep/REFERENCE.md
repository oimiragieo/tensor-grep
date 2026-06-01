# Tensor-Grep Reference

## Core Commands

```powershell
tg --version
tg "PATTERN" REPO_PATH
tg -t js "PATTERN" REPO_PATH
tg --count-matches "PATTERN" REPO_PATH
tg search --format rg "PATTERN" REPO_PATH
tg agent REPO_PATH --query "change behavior" --json
tg edit-plan REPO_PATH --query "change behavior" --json
tg context-render REPO_PATH --query "feature flow" --json
tg source REPO_PATH SYMBOL
tg defs REPO_PATH SYMBOL
tg refs REPO_PATH SYMBOL
tg callers REPO_PATH SYMBOL
tg blast-radius REPO_PATH SYMBOL
tg blast-radius-plan REPO_PATH SYMBOL
```

## Useful Variants

```powershell
tg source REPO_PATH SYMBOL --json
tg defs REPO_PATH SYMBOL --provider native --json
tg refs REPO_PATH SYMBOL --provider lsp --json
tg blast-radius REPO_PATH SYMBOL --provider hybrid --json
tg blast-radius-plan REPO_PATH SYMBOL --provider native --json
tg search --format rg --sort path "PATTERN" REPO_PATH
tg search --json "PATTERN" REPO_PATH
tg search --format rg --json "PATTERN" REPO_PATH
tg session open REPO_PATH --json
tg session edit-plan SESSION_ID REPO_PATH --query "change behavior" --daemon --json
```

## Practical Sequence

```powershell
tg source C:\repo open_file --json
tg blast-radius C:\repo open_file --json
tg blast-radius-plan C:\repo open_file --json
```

Use the top-ranked file/span first. Only broaden to refs/callers if the primary file is still ambiguous.

Notes:

- Symbol commands use path-first positional order: `tg <command> REPO_PATH SYMBOL`.
- `tg search --format rg --json` emits ripgrep JSON Lines. Plain `tg search --json` is tensor-grep aggregate JSON.
- LSP and GPU surfaces are experimental until their proof fields say otherwise.
