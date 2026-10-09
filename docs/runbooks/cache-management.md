# Cache management

`tensor-grep` writes several kinds of local state. Identify the specific item before removing anything: search caches can be rebuilt, session files hold reusable context, and checkpoints contain saved file contents for rollback.

## Find the relevant state

Paths below are relative to a project root unless otherwise noted.

| Path | Purpose | Safe assumption |
|---|---|---|
| `.tg_index` | Native trigram index for eligible repeated text searches | Rebuildable search data |
| `.tg_cache/ast/project_data_v6.json` | AST project cache | Rebuildable AST data |
| `.tg_cache/symbols_v1/metadata.sqlite3` | Content-addressed whole-file symbol/import products and transactional generation/Merkle metadata | Rebuildable symbol data |
| `.tensor-grep/sessions/` | Persisted session metadata and snapshots | May contain context you intend to reuse |
| `.tensor-grep/checkpoints/` | Saved checkpoint metadata and file snapshots | Recovery data; preserve it unless you intend to remove those recovery points |

The names are similar, but `.tg_index` is a file and `.tg_cache/ast/` holds AST data. `tg calibrate` measures CPU/GPU crossover behavior; it does not create or warm either cache.

Repository-map builds reconcile bounded source contents and reuse unchanged whole-file
symbol products under `.tg_cache/symbols_v1/`. A selected file uses its discovered project
root for this cache; a directory uses the selected directory. Keys include content,
parser/schema versions, scope, parsing limits, and ancestor ignore configuration. This
works without Git and includes untracked source files. Additions, deletions, renames, and
same-size edits with preserved mtimes are reconciled on the next build. Runtime
`.tg_cache` directories are excluded from repository scan counts and file budgets.
Before publishing cache data, the owned `symbols_v1` directory receives a no-clobber
`.gitignore` containing `*`, keeping generated cache files out of Git worktree status.
Existing cache ignore files are preserved; links or unsafe metadata prevent persistence.
Project `.gitignore` and `.git/info/exclude` files are not modified.

Raw repository-map output (`tg map PATH --json`) includes `symbol_cache` receipts for the
content-reconciliation route, cache hits/misses, source bytes read, and generation Merkle
root. Derived symbol and capsule responses retain stable cache provenance and coverage
fields while omitting per-request `hits`, `misses`, and `bytes_reconciled` counters. Use
the raw map receipts to verify cold misses and warm hits or measure reconciliation;
do not infer cache inactivity from counters absent in `tg defs` or capsule output.
Existing session changesets lack a trusted
watcher sequence or overflow receipt, so they do not authorize skipping reconciliation.
The cache records verified per-file observations; it does not provide a filesystem-wide
snapshot. Files detected changing during extraction or final verification are omitted,
with `partial` and `symbol_cache_coverage` reporting the reason. Deadlines and file caps
remain active. Corrupt entries/databases are rebuilt and recovery is disclosed; refused
cache writes or lock contention retain freshly parsed results and disclose that the
generation was not persisted. No fixed latency is promised.

Cached symbol products carry a machine-private HMAC over the entry key and payload;
checkout-provided checksums cannot establish parser-backed evidence. The signing key
uses the OS account's private state location and is never stored in the checkout.
If it is unavailable or fails ownership, permissions, link, or confinement checks,
queries parse fresh sources and disclose that persistent reuse is unavailable.
Unsigned legacy entries are rebuilt. Generation/Merkle values describe observed
contents and are not signatures establishing parser provenance.

Sessions capture bounded content SHA-256 receipts tied to the map's source generation.
Warm retrieval validates captured files even when mtimes and sizes are unchanged;
legacy snapshots require refresh. A source changing between map construction and
snapshot capture prevents reuse of that uncertain session. Freshness checks,
refreshes, and warm graph building share one request deadline. An incomplete
inventory walk or unreadable source is reported as unverified rather than deleted
or fresh. Default context-cache requests still validate the captured scope; detecting
new files requires explicit refresh or `refresh_on_stale`, and stats disclose this
with `response_cache_added_file_detection = false`.

Persisted session maps, captured snapshots, prepared decisions, and their root/scope metadata carry a
separate machine HMAC. Modified or unsigned legacy session products are refused
before disk-loaded parser evidence is reused. Run `tg session refresh ID ROOT` to
derive and sign a fresh bounded map; an unauthenticated session cannot select a
different root or increase the default scan budget during that rebuild. If the
machine signing key is unavailable, persisted session reuse is refused with
refresh guidance; ordinary queries can still parse fresh sources.
Session payload reads are confined regular-file reads capped at 64 MiB. Linked,
non-object, invalid, or excessively nested JSON is refused before authentication;
explicit refresh reconstructs bounded state from sources.

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
- `.tg_cache/symbols_v1/` stores rebuildable whole-file symbol products and generation metadata.
- `.tensor-grep/sessions/` stores session snapshots and metadata.
- `.tensor-grep/checkpoints/` stores file contents for rollback.
- A daemon can also hold response data in memory while it runs.

These stores have different lifetimes and consequences. See [architecture](../architecture.md) for their roles, [session daemon protocol](../session_daemon_protocol.md) for session behavior, and the [contracts](../CONTRACTS.md) for canonical state guarantees.
