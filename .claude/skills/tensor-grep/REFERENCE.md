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
tg search PATTERN PATH
tg search PATTERN PATH --rank
tg orient REPO_PATH
```

## Useful Variants

```powershell
tg source REPO_PATH SYMBOL --json
tg defs REPO_PATH SYMBOL --provider native --json
tg refs REPO_PATH SYMBOL --provider lsp --json
tg blast-radius REPO_PATH SYMBOL --provider hybrid --json
tg blast-radius-plan REPO_PATH SYMBOL --provider native --json
tg search PATTERN PATH --rank
tg search PATTERN PATH --rank --json
tg orient REPO_PATH
tg orient REPO_PATH --json
tg orient REPO_PATH --max-tokens 6000 --max-central-files 15
```

## Practical Sequence

```powershell
tg source C:\repo open_file
tg blast-radius C:\repo open_file
tg blast-radius-plan C:\repo open_file
```

Use the top-ranked file/span first. Only broaden to refs/callers if the primary file is still ambiguous.

## Orient-First Sequence (unfamiliar repo)

```powershell
tg orient C:\repo
tg source C:\repo <symbol-from-orient-output>
tg blast-radius C:\repo <symbol-from-orient-output>
```

Use `tg orient` when you do not yet know which files or symbols matter. The capsule gives you central files (import in-degree), entry points, and a symbol map — pick the right symbol, then proceed with source/blast-radius.

## Search-Then-Source Sequence (unknown symbol name)

```powershell
tg search "pattern" C:\repo --rank
tg source C:\repo <symbol-from-top-hit>
```

Use when the symbol name is unknown but the concept or text is known. `--rank` (alias `--bm25`) re-ranks ripgrep hits by BM25 content relevance — pure Python, no API key, no GPU.
