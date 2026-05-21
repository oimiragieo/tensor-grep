# Tensor-Grep Reference

## Core Commands

```powershell
tg --version
tg search --format rg "PATTERN" REPO_PATH
tg agent REPO_PATH --query "change behavior" --json
tg edit-plan REPO_PATH --query "change behavior" --json
tg context-render REPO_PATH --query "feature flow" --json
tg source --symbol SYMBOL REPO_PATH
tg defs --symbol SYMBOL REPO_PATH
tg refs --symbol SYMBOL REPO_PATH
tg callers --symbol SYMBOL REPO_PATH
tg blast-radius --symbol SYMBOL REPO_PATH
tg blast-radius-plan --symbol SYMBOL REPO_PATH
```

## Useful Variants

```powershell
tg source --symbol SYMBOL REPO_PATH --json
tg defs --symbol SYMBOL REPO_PATH --provider native --json
tg refs --symbol SYMBOL REPO_PATH --provider lsp --json
tg blast-radius --symbol SYMBOL REPO_PATH --provider hybrid --json
tg blast-radius-plan --symbol SYMBOL REPO_PATH --provider native --json
tg search --format rg --sort path "PATTERN" REPO_PATH
tg search --json "PATTERN" REPO_PATH
tg search --format rg --json "PATTERN" REPO_PATH
tg session open REPO_PATH --json
tg session edit-plan SESSION_ID REPO_PATH --query "change behavior" --daemon --json
```

## Practical Sequence

```powershell
tg source --symbol open_file C:\repo --json
tg blast-radius --symbol open_file C:\repo --json
tg blast-radius-plan --symbol open_file C:\repo --json
```

Use the top-ranked file/span first. Only broaden to refs/callers if the primary file is still ambiguous.

Notes:

- Symbol commands use `--symbol`; do not pass the symbol positionally.
- `tg search --format rg --json` emits ripgrep JSON Lines. Plain `tg search --json` is tensor-grep aggregate JSON.
- LSP and GPU surfaces are experimental until their proof fields say otherwise.
