# Tensor-Grep Reference

## Core Commands

```powershell
tg --version
tg source SYMBOL REPO_PATH
tg defs SYMBOL REPO_PATH
tg refs SYMBOL REPO_PATH
tg callers SYMBOL REPO_PATH
tg blast-radius SYMBOL REPO_PATH
tg blast-radius-plan SYMBOL REPO_PATH
```

## Useful Variants

```powershell
tg source SYMBOL REPO_PATH --json
tg defs SYMBOL REPO_PATH --provider native --json
tg refs SYMBOL REPO_PATH --provider lsp --json
tg blast-radius SYMBOL REPO_PATH --provider hybrid --json
tg blast-radius-plan SYMBOL REPO_PATH --provider native --json
```

## Practical Sequence

```powershell
tg source open_file C:\repo
tg blast-radius open_file C:\repo
tg blast-radius-plan open_file C:\repo
```

Use the top-ranked file/span first. Only broaden to refs/callers if the primary file is still ambiguous.
