# Tensor-Grep Reference

> Argument order is **path-first**: `tg <command> <REPO_PATH> <SYMBOL>`.
> If you reverse them (`<SYMBOL> <REPO_PATH>`) tensor-grep auto-corrects and
> prints a hint, but write path-first to avoid the extra round trip. A bare
> `tg <command> <SYMBOL>` resolves the symbol against the current directory.

## Core Commands

```powershell
tg --version
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
```

## Practical Sequence

```powershell
tg source C:\repo open_file
tg blast-radius C:\repo open_file
tg blast-radius-plan C:\repo open_file
```

Use the top-ranked file/span first. Only broaden to refs/callers if the primary file is still ambiguous.
