# Rebuild guide: `tg checkpoint` (create / list / undo)

> Verified against `origin/main` `7ee3a27e` (2026-08-20). Every symbol below was opened and read
> at that revision; every command output shown was actually run, not described from memory (the
> full session transcript that produced this doc is not part of the repo, but the exact commands
> are reproducible — run them yourself). Installed `tg` used for the live demo is `1.110.16`
> (`tg --version`), slightly behind the worktree's `main.py`; the field shapes matched what the
> source at `7ee3a27e` predicts, so the divergence (if any) is cosmetic. Treat the JSON in this
> doc as a real example, not a frozen contract — re-run `tg checkpoint create --json` yourself
> before depending on an exact field name.
>
> This is the template rebuild guide named in `docs/design/README.md`'s companion audit — the
> proof that a feature in this repo can be documented well enough for **a junior-level analyst to
> rebuild it from scratch**. Future guides should match this one's shape: problem statement, data
> flow, file-by-file contribution, contracts, registration sites, tests, and traps — not just an
> API reference.

## 1. The problem this feature solves

An agent (or a human) editing a repo through `tg` wants a **local, cheap, undo button** that does
not depend on git: create a snapshot of a file or directory's current state, keep editing, and
restore that exact state later if the edit went wrong. `tg`'s own `rewrite apply --checkpoint`
flag and `apply_policy.py`'s rollback path (`from tensor_grep.cli.checkpoint_store import
undo_checkpoint`, `src/tensor_grep/cli/apply_policy.py:12`) both build on this, and `tg prepare`'s
`rollback` field recommends running `tg checkpoint create <path>` before an edit
(`src/tensor_grep/cli/prepare_service.py`, the `result["rollback"]` block sourced from the
agent capsule).

It has to work in three scopes an editor actually hits:

1. **A single file** — the caller names one file, only that file is snapshotted.
2. **A directory inside a git worktree** — reuse git's own tracked/untracked file list instead of
   walking the filesystem by hand (faster, and matches what `git status` would show).
3. **A directory with no git repo at all** — fall back to a bounded filesystem walk.

And it has to survive the two ways a naive implementation of "copy files, then copy them back" is
wrong: a **crash or interrupt mid-copy must never leave the working tree half-restored**, and a
**malicious or corrupted checkpoint record must never let a restore write outside the checkpoint's
own root** (path traversal via a crafted `checkpoint_id` or a tampered `entries` manifest — this
surface is reachable from the CLI, the MCP `tg_checkpoint_undo` tool, and `apply_policy.py`'s
automatic rollback, so all three inherit whatever `checkpoint_store.py` gets wrong).

## 2. Data flow, end to end

```
tg checkpoint create <path>
        |
        v
main.py: checkpoint_create()                 <- CLI adapter (Typer command)
        |  (src/tensor_grep/cli/main.py, @checkpoint_app.command("create"))
        v
checkpoint_store.create_checkpoint(path)      <- pure business logic, no Typer/JSON here
        |
        +-- _detect_checkpoint_scope(path)    <- file? git worktree? plain directory?
        |
        +-- _snapshot_entries(scope)          <- which relative paths belong in this checkpoint
        |     +-- _git_snapshot_entries()         (git ls-files, when scope.mode == git-worktree-snapshot)
        |     +-- _filesystem_snapshot_entries()  (bounded os.walk, otherwise)
        |
        +-- _check_checkpoint_disk_budget()   <- refuse BEFORE writing anything if too big
        |
        +-- copy loop: shutil.copy2(..., follow_symlinks=False) into
        |     .tensor-grep/checkpoints/<checkpoint_id>/snapshot/<rel_path>
        |
        +-- _write_checkpoint_metadata()      <- .../metadata.json  (entries manifest, mode, root)
        |
        +-- index_lock(...) -> _load_index / _select_retained_checkpoints / _write_index
              .../index.json  (one row per checkpoint, newest-first, capped at TG_CHECKPOINT_MAX)

tg checkpoint undo <checkpoint_id> <path>
        |
        v
main.py: checkpoint_undo()
        v
checkpoint_store.undo_checkpoint(checkpoint_id, path)
        |
        +-- PRE-FLIGHT (read-only): resolve every entry's target AND source path,
        |   assert both stay inside their respective roots, verify every snapshot
        |   blob that should exist is present and readable. Abort here -> tree untouched.
        |
        +-- STAGING (still no working-tree mutation): copy every restorable file
        |   into a throwaway tempfile.TemporaryDirectory().
        |
        +-- COMMIT (mutates the working tree): remove files not in the snapshot,
        |   remove files the snapshot recorded as deleted, copy staged files over
        |   their targets -- recording (path, prior_bytes) for every destructive
        |   step so a failure partway through this phase can revert what it already did.
        |
        +-- best-effort empty-directory cleanup, discovery-cache refresh
```

The three-phase undo (pre-flight -> staging -> commit) is the mechanism that makes "crash-safe"
true: everything that can fail because of a *missing or unreadable* file happens in phases that
have not touched the working tree yet. Only the commit phase mutates real files, and it captures
enough (`committed_removes`, `committed_overwrites`, each holding the actual prior bytes) to revert
itself if it dies partway through.

## 3. Every file involved, and what each contributes

| File | Contributes |
|---|---|
| `src/tensor_grep/cli/checkpoint_store.py` (1,431 lines) | The core: scope detection, snapshot-entry enumeration, `create_checkpoint`, `undo_checkpoint`, `list_checkpoints`, discovery, path-containment guards, the two error types (`CheckpointCorruptError`, `CheckpointUndoUnsafeError`). |
| `src/tensor_grep/cli/checkpoint_retention.py` | Split out of `checkpoint_store.py` to keep it under this repo's 1,500-line file-size ratchet (see the module's own docstring, which explains exactly which functions were safe to move — see §6, Trap 5). Holds `_select_retained_checkpoints`/`_prune_checkpoint_records` (retention cap) and `_check_checkpoint_disk_budget` (the per-file/total/free-space pre-flight). |
| `src/tensor_grep/cli/_index_lock.py` | Shared atomic-write and cross-process/cross-thread file-lock primitives (`atomic_write_json`, `atomic_write_bytes`, `index_lock`) that `checkpoint_store.py` reuses rather than re-implementing its own. |
| `src/tensor_grep/cli/subprocess_policy.py` | `run_subprocess` / `configured_git_timeout_seconds` — the one place `git` is ever invoked from, with a timeout. |
| `src/tensor_grep/cli/main.py` | The Typer CLI adapter: `checkpoint_app` (a `typer.Typer()` sub-app, `main.py:266`), and the three commands `checkpoint_create` / `checkpoint_list` / `checkpoint_undo` (`main.py:14117`, `:14141`, `:14295`). Pure JSON/text formatting and exit-code mapping — no business logic. |
| `src/tensor_grep/cli/mcp_server.py` | The MCP adapter: `tg_checkpoint_create` / `tg_checkpoint_list` / `tg_checkpoint_undo` (`mcp_server.py:6212`, `:6263`, `:6311`), each wrapping the same `checkpoint_store` functions behind `_confine_mcp_path` (an MCP-root path-confinement gate the CLI does not need, because the CLI already trusts the local caller's `path` argument the way any local CLI does). |
| `src/tensor_grep/cli/apply_policy.py` | A **consumer**, not part of the feature itself: imports `undo_checkpoint` to power `tg rewrite apply`'s rollback path. |
| `src/tensor_grep/cli/prepare_service.py` | Another consumer: `tg prepare`'s `rollback` field recommends `tg checkpoint create <path>` before an edit (advisory only — it never calls `create_checkpoint` itself). |

Everything under `.tensor-grep/checkpoints/` on disk (the on-disk format) is described in §4.

## 4. On-disk format (verified by actually creating and inspecting one)

```
$ mkdir demo && cd demo
$ echo "hello world" > a.txt
$ mkdir sub && echo "nested" > sub/b.txt
$ tg checkpoint create . --json
```

produced (real output, only the absolute path shortened):

```json
{
  "checkpoint_id": "ckpt-20260820110732-7d0c7bf3",
  "mode": "filesystem-snapshot",
  "root": "...\\demo",
  "created_at": "2026-08-20T11:07:32.305938+00:00",
  "file_count": 2,
  "undo_argv": ["tg", "checkpoint", "undo", "ckpt-20260820110732-7d0c7bf3", "...\\demo"],
  "undo_command": "tg checkpoint undo ckpt-20260820110732-7d0c7bf3 ...\\demo",
  "skipped_nested_repos": [],
  "version": 1,
  "schema_version": 1
}
```

and left this real layout on disk (from `find .tensor-grep`):

```
.tensor-grep/
  checkpoint-discovery-cache.json
  checkpoints/
    index.json
    ckpt-20260820110732-7d0c7bf3/
      metadata.json
      snapshot/
        a.txt
        sub/
          b.txt
```

`index.json` (real content, one row per checkpoint, newest first):

```json
[
  {
    "version": 1, "checkpoint_id": "ckpt-20260820110732-7d0c7bf3",
    "mode": "filesystem-snapshot", "root": "...\\demo",
    "created_at": "2026-08-20T11:07:32.305938+00:00", "file_count": 2
  }
]
```

`checkpoints/<id>/metadata.json` (real content):

```json
{
  "version": 1, "checkpoint_id": "ckpt-20260820110732-7d0c7bf3",
  "mode": "filesystem-snapshot", "root": "...\\demo", "scope": "tree",
  "original_path": "...\\demo",
  "created_at": "2026-08-20T11:07:32.305938+00:00", "file_count": 2,
  "entries": {"a.txt": true, "sub/b.txt": true},
  "skipped_nested_repos": [], "active": true
}
```

`entries` is the manifest that drives undo: each key is a checkpoint-root-relative path, and the
value is whether that path **existed** when the checkpoint was taken (`true` -> restore it from
`snapshot/`; `false` -> the checkpoint recorded that this path did NOT exist yet, so undo must
delete it if a later edit created it). `index.json` is a **cache** for fast listing — it can always
be rebuilt from the `metadata.json` files under `checkpoints/*/` (see
`_rebuild_index_from_checkpoint_metadata`, `checkpoint_store.py:582`), which is the source of
truth.

## 5. Undo, demonstrated (the divergence-detection field, real output)

Continuing the same demo: modify `a.txt`, delete `sub/b.txt`, add a new untracked `c.txt`, then
undo:

```
$ echo "modified content" > a.txt
$ rm sub/b.txt
$ echo "new file" > c.txt
$ tg checkpoint undo ckpt-20260820110732-7d0c7bf3 . --json
```

Real output:

```json
{
  "checkpoint_id": "ckpt-20260820110732-7d0c7bf3",
  "mode": "filesystem-snapshot",
  "root": "...\\demo",
  "restored_files": 2,
  "removed_paths": 1,
  "diverged_paths": ["a.txt"],
  "version": 1,
  "schema_version": 1
}
```

After this: `a.txt` is back to `hello world` (its checkpoint content, even though it was edited
*after* the checkpoint — undo always wins, that is what it is for); `sub/b.txt` is restored;
`c.txt` (never part of the checkpoint) is removed. `restored_files=2` counts the two entries copied
back from the snapshot; `removed_paths=1` counts `c.txt`. `diverged_paths` is the honesty field
added in task #308 (`_paths_modified_since_checkpoint`, `checkpoint_store.py:1163`): it names every
path whose *mtime* is newer than the checkpoint's `created_at`, i.e. every file whose
post-checkpoint edits undo is about to discard. It is not a warning that something went wrong —
discarding post-checkpoint work is undo's entire job — it answers "what did I just lose?", which
matters most when a *second* agent edited the same tree between checkpoint and undo.

## 6. Contracts and traps a naive rebuild gets wrong

A straightforward "copy the directory, copy it back" implementation would pass a quick smoke test
and then fail in exactly these ways — each is a real, currently-guarded hazard in
`checkpoint_store.py`, with a comment at the guard citing why:

1. **Crash mid-restore must not half-apply.** Naive: overwrite files one at a time as you restore
   them. If the process dies on file 50 of 100, the tree is an unrecoverable mix of old and new
   content. Fix: the three-phase pre-flight/staging/commit split in §2 — nothing touches the
   working tree until every file to be restored has already been staged successfully, and the
   commit phase records enough (`committed_removes`, `committed_overwrites` — pairs of
   `(path, prior_bytes)`, not just paths) to revert itself on a later failure
   (`checkpoint_store.py:1207`, `undo_checkpoint`'s `except Exception:` block).

2. **An unreadable file must abort, not silently destroy content.** Naive: `path.unlink()` before
   checking whether the file could be read. If the read would have failed, its content is now gone
   forever with no way to recreate it for a revert. Fix: `_bytes_or_abort_undo`
   (`checkpoint_store.py:114`) reads a file's bytes *before* any unlink/overwrite touches it, and
   raises `CheckpointUndoUnsafeError` — not a silent skip — if the read fails, so the destructive
   step is never taken without first having a copy to restore from.

3. **A crafted `checkpoint_id` or a tampered `entries` manifest must not escape the checkpoint
   root.** Naive: `root / entries[key]`. If `entries` (read from disk, and reachable from the MCP
   `tg_checkpoint_undo` tool and `apply_policy.py`'s automatic rollback — not just a trusted human
   CLI call) contains `"../../etc/passwd"` or an absolute path, that composition writes or deletes
   outside the checkpoint scope. Fix: `_resolve_within_root` (`checkpoint_store.py:162`) rejects
   any absolute path or `..` component, then resolves and asserts containment under the resolved
   root — applied to **both** the undo target path and the checkpoint id itself
   (`_checkpoint_dir`, `checkpoint_store.py:714`, reuses the same helper so a traversal-shaped
   `checkpoint_id` is refused before any metadata file is even opened).

4. **A symlinked (or, on Windows, junctioned) *ancestor* directory is a second escape route the
   leaf-only check above misses.** Naive: check only whether the final path component is a
   symlink (what `shutil.copy2(..., follow_symlinks=False)` alone gives you). If some *parent*
   directory in the path is a symlink pointing outside the root, the OS still transparently
   traverses it — so a leaf-only check reads bytes from (create side) or writes bytes to (undo
   side) somewhere outside the checkpoint entirely. Fix: `_resolve_parent_within_root`
   (`checkpoint_store.py:180`) resolves and asserts containment on the **parent chain only**,
   leaving the leaf's raw identity untouched, so a legitimately-tracked leaf symlink is still
   stored *as a link* rather than refused or dereferenced. Used on the create side; the same
   containment check is applied to undo's snapshot *source* path too
   (`undo_checkpoint`'s pre-flight, "audit H3" comment).

5. **A single huge file or a legitimately huge repo must not fill the disk.** Naive: copy
   everything, no size check. Fix: `_check_checkpoint_disk_budget`
   (`checkpoint_retention.py:144`) stats every entry before any copy happens and refuses with
   `CheckpointBudgetExceededError` if a single file exceeds `TG_CHECKPOINT_MAX_FILE_BYTES`
   (default 512 MiB), the cumulative snapshot exceeds `TG_CHECKPOINT_MAX_TOTAL_BYTES` (default
   4 GiB), or the copy would leave less than `TG_CHECKPOINT_FREE_SPACE_MARGIN_BYTES` (default
   256 MiB) free — all three are env-overridable so a repo with legitimately large tracked assets
   is not permanently blocked.

6. **A `KeyboardInterrupt` mid-copy must not orphan a half-written checkpoint directory.** Naive:
   `except Exception:` around the copy loop. `KeyboardInterrupt` and `SystemExit` subclass
   `BaseException` directly, not `Exception`, so that handler never fires on Ctrl+C and a
   half-populated `checkpoints/<id>/` directory is left behind forever. Fix:
   `create_checkpoint`'s cleanup wraps `except BaseException:` (`checkpoint_store.py`, the
   "audit #125a" comment), removing the whole per-checkpoint directory before re-raising.

7. **A git submodule (a gitlink, mode `160000`) is a real directory on disk that `git ls-files`
   reports as a single opaque path, not an expandable file list.** Naive: treat every path
   `git ls-files` returns as a file and `shutil.copy2()` it — crashes (`IsADirectoryError` /
   `PermissionError`) the first time a repo has a submodule. Fix: `_git_snapshot_entries`
   (`checkpoint_store.py:624`) checks `candidate.is_dir() and not candidate.is_symlink()` and
   routes any such path to `skipped_nested_repos` (surfaced in the CLI/MCP JSON output) instead
   of crashing or silently dropping it.

8. **The retention cap must sort by `created_at`, not insertion order.** Naive: `records[:limit]`
   after inserting the newest at index 0. Under concurrent writers, the timestamp is stamped
   *before* `index_lock` is acquired, so lock-arrival order does not reliably match creation
   order — trusting list position can prune a genuinely newer checkpoint. Fix:
   `_select_retained_checkpoints` (`checkpoint_retention.py:43`) re-sorts by `created_at`
   immediately before slicing.

## 7. What "done" looks like — validating a rebuild

Run the real tests, which exist precisely because the naive versions of the eight traps above were
each shipped and caught once already:

```
python -m pytest tests/unit/test_checkpoint_atomic_undo.py tests/unit/test_checkpoint_cli.py \
    tests/unit/test_checkpoint_containment.py tests/unit/test_checkpoint_create_ancestor_confinement.py \
    tests/unit/test_checkpoint_disk_budget.py -q
```

Each file's test names are close to a spec on their own — e.g.
`test_checkpoint_atomic_undo.py::test_undo_commit_failure_restores_a_removed_file` (trap 1),
`test_checkpoint_containment.py::test_rust_created_out_of_root_symlink_checkpoint_fails_closed_on_undo`
(trap 4), `test_checkpoint_disk_budget.py::test_create_checkpoint_refuses_file_over_per_file_cap`
(trap 5), `test_checkpoint_cli.py::test_create_checkpoint_skips_git_submodule_without_crashing`
(trap 7). See `docs/rebuild-guides/verification-checklist.md` for the general form of this step.

## 8. Explicitly out of scope for this guide

- The Rust-side `replace_in_place` symlink hardening referenced in
  `docs/design/2026-08-13-replace-in-place-symlink-threat-model.md` is a **different** feature
  (native in-place rewrite) that happens to share the symlink-containment concern — it is not part
  of `tg checkpoint` and this guide does not cover it.
- `checkpoint-discovery-cache.json` (the bounded-discovery-scope cache used by `tg checkpoint list
  --discover`) is covered only enough to explain what it is in §4; its full discovery-bounding
  logic (`_bounded_discovery_cache_roots_for_checkpoint`, `_refresh_bounded_discovery_caches_for_root`)
  is not traced line by line here.
- This guide does not re-verify the MCP `_confine_mcp_path` gate's own correctness — only that it
  exists and wraps the same `checkpoint_store` calls the CLI uses.
