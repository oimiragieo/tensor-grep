---
name: tensor-grep-multi-project-search
description: Use when searching across a multi-project workspace root (many sibling repos) with tensor-grep — bare unscoped search is refused; scope with --glob/--type/--max-depth or opt in with --allow-broad-generated-scan; prefer per-repo paths for agent/symbol/find work; pass explicit --deadline on whole-repo agent; narrow mega-repo PATH when result_incomplete.
---

# tensor-grep multi-project workspace search

Use this when the cwd is a **workspace parent** (e.g. `/mnt/c/dev/projects`) containing many unrelated repos, not a single git root.

Verified against **tg 1.95.0** (2026-07-24; prior full dogfood 2026-07-21 WSL sweep at v1.91.0).

## Do this

```bash
# Cross-repo text search (scoped explicitly — see "Do not do this" for the defaulted-PATH refusal):
tg search PATTERN . --glob "*.py" --max-depth 3 --json
tg search PATTERN . --glob "*.js" --max-depth 3 --json
# Prefer per-repo for --type ts (workspace-wide --type ts --max-depth 4 TIMED OUT @45s)

# Pick a project, then prefer src/ for agent / callers / find:
tg search PATTERN my-repo --rank --json
tg find "intent phrase" my-repo/src --deadline 20 --json
tg orient my-repo --ignore "node_modules/**" --json
tg orient . --ignore "node_modules/**" --json   # bounded via scan_limit(2000)+centrality (~4.9s on a 300k+-file workspace); still prefer per-repo for a repo-focused capsule
tg agent my-repo/src "task" --json              # preferred (~16s PASS)
tg agent my-repo "task" --deadline 20 --json    # partial capsule OK; honor ask_user_before_editing
# Do NOT rely on bare `tg agent my-repo` default 60s cold bound on WSL (TIMEOUT empty @75s)

# Mega-repo truncation mitigation:
tg agent my-repo/subdir "task" --json           # e.g. agent-studio/.claude/lib/routing
```

## Do not do this

```bash
tg search PATTERN          # refused in ~1.7s on a defaulted PATH over 1500 files (exit 2) — not "zero TODO", and NOT a silent 60s timeout
tg search PATTERN --glob "*.py"   # --glob does NOT bypass the refusal when PATH is still defaulted (see below)
tg agent . "task"          # too broad across sibling projects
tg callers . SYMBOL        # incomplete graphs; prefer REPO/src
tg codemap my-repo         # still TIMEOUT on WSL agent loops
tg search TODO . --type ts --max-depth 4   # can TIMEOUT on large workspaces
```

**The bare-`tg search` refusal (A9, v1.92.3) is a single generic mechanism, not a special multi-project
case.** `IMPLICIT_SEARCH_WALK_FILE_CEILING = 1500` is one constant, single-sourced in
`io/directory_scanner.py`, checked coherently across all 3 doors (the Python bootstrap probe, `main.py`,
and the Rust `rg_passthrough.rs`) — it fires whenever the scan PATH was **defaulted** (no path argument
given at all) and the resulting root is over 1500 files, workspace-parent or not. Before this shipped,
the same shape was a **silent ~60s timeout**, not a fast refusal — don't describe the old magnitude as
current.

The bypasses in "Do this" above (`--max-depth`, an explicit PATH like `.` or `my-repo`) work because they
give the walk an explicit scope — they are the SAME gate's escape hatches, not a separate multi-project
allowance. **`--glob`/`--type` alone do NOT bypass the ceiling** when the path itself is still defaulted
— only an explicit PATH, `--max-depth`, or the deliberate opt-in `--allow-broad-generated-scan` count as
scoping the walk.

## How to read exit codes

| Exit | Meaning in this CUJ |
| --- | --- |
| `0` | Complete enough for the scoped ask |
| `2` | Incomplete / refused / deadline partial — parse JSON; do not treat as full coverage |
| timeout / empty | Prefer narrower PATH or explicit `--deadline`; skip `codemap` |

## Related

- `tensor-grep`, `tensor-grep-find-and-route`, `tensor-grep-workspace-dogfood`, `tensor-grep-enterprise-agent`
