# Cache management

`tensor-grep` writes several kinds of local state. Identify the specific item before removing anything: search caches can be rebuilt, session files hold reusable context, and checkpoints contain saved file contents for rollback.

## Find the relevant state

Paths below are relative to a project root unless otherwise noted.

| Path | Purpose | Safe assumption |
|---|---|---|
| `.tg_index` | Native trigram index for eligible repeated text searches | Rebuildable search data |
| `.tg_cache/ast/project_data_v6.json` | AST project cache | Rebuildable AST data |
| `.tensor-grep/sessions/` | Persisted session metadata and snapshots | May contain context you intend to reuse |
| `.tensor-grep/checkpoints/` | Saved checkpoint metadata and file snapshots | Recovery data; preserve it unless you intend to remove those recovery points |

The names are similar, but `.tg_index` is a file and `.tg_cache/ast/` holds AST data. `tg calibrate` measures CPU/GPU crossover behavior; it does not create or warm either cache.

## Inspect before clearing

Use `tg doctor --json` to inspect installation and local diagnostic information. For session state, use the session commands rather than browsing into or deleting the store:

```text
tg session list . --json
```

For checkpoints, list the checkpoints associated with a file before any cleanup:

```text
tg checkpoint list ./src/invoice.py --json
```

Checkpoint metadata and snapshots live under `.tensor-grep/checkpoints/`. Deleting that directory removes rollback material. Session state is stored separately under `.tensor-grep/sessions/`; avoid deleting the whole `.tensor-grep/` directory as a cache-clearing shortcut.

## Rebuild a stale search cache

From the project root, use `tg search --index` to explicitly build or refresh the trigram index for that root. For example:

```text
tg search --index -F -n 'KNOWN_TEXT' .
```

Replace `KNOWN_TEXT` with a literal expected in the project. The search builds `.tg_index` when it is absent and updates it when the recorded file state is stale. The query's match result does not determine whether the index is built. To force a full rebuild, first close any running `tg` work that may be using the project, then remove only the index file:

**PowerShell:**

```powershell
Remove-Item -LiteralPath '.tg_index' -Force
```

**Bash:**

```bash
rm -f ./.tg_index
```

After removal, rerun the explicit `tg search --index` command above. If the problem concerns AST data, inspect and remove only `.tg_cache/ast/project_data_v6.json`; a later AST workflow can rebuild it. Do not remove session or checkpoint state to refresh either search cache. If you are unsure which path is affected, keep the files and ask your project maintainer before deleting anything.

## Keep state separate

- `.tg_index` narrows candidates for some repeated text searches.
- `.tg_cache/ast/` stores parsed project data for AST workflows.
- `.tensor-grep/sessions/` stores session snapshots and metadata.
- `.tensor-grep/checkpoints/` stores file contents for rollback.
- A daemon can also hold response data in memory while it runs.

These stores have different lifetimes and consequences. See [architecture](../architecture.md) for their roles, [session daemon protocol](../session_daemon_protocol.md) for session behavior, and the [contracts](../CONTRACTS.md) for canonical state guarantees.
