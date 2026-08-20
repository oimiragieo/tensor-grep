# Rebuild guide: `tg ledger` (claim / release / list / record / find)

> Verified against `origin/main` `0b9d33f` (2026-08-20). Every symbol below was opened and read
> at that revision; every command output shown was actually run against the real, installed `tg`
> binary (`tg --version` = `1.110.16`), not described from memory. Treat the JSON in this doc as
> a real example, not a frozen contract — re-run the commands yourself before depending on an
> exact field name.
>
> This is the second rebuild guide in `docs/rebuild-guides/` (see `tg-checkpoint.md`, the worked
> template, and `README.md` for the convention).

## 1. The problem this feature solves

`tg ledger` is an advisory, code-scoped coordination plane for concurrent coding agents working
the same repository. It has two independent slices under one CLI sub-app:

- **Slice 1 — claims** (`tg ledger claim` / `release` / `list`): an agent ADVERTISES intent to
  edit a symbol or file. A claim is advisory only — it is NEVER a lock. `submit_claim` always
  returns normally on success, even when other agents hold live overlapping claims on the same
  symbol/file; those overlaps are reported back in an `overlaps` list for the caller to act on,
  never raised as an error. A dead agent's claim simply expires via TTL — there is no
  crash-recovery special case, because nothing was ever locked.
- **Slice 2 — findings** (`tg ledger record` / `find`, marked `EXPERIMENTAL` in the CLI's own
  `--help` text): one agent `record`s an already-computed evidence-receipt / blast-radius /
  context-pack / repo-map artifact; a sibling agent `find`s it by symbol and reuses it instead of
  recomputing — but only if it is still `fresh` (the recorded repo revision — `commit_sha` AND a
  dirty-tree hash — matches the CURRENT repo state) and the blob's bytes still hash to the
  recorded `receipt_sha256`. A tampered or corrupted blob raises `LedgerIntegrityError` (CLI exit
  2) rather than silently serving bad data.

Both slices share one design constraint worth stating up front, because it drove the feature's
one real historical bug (§5 below): every claim/list/release/record/find call names a `path`
argument, but that path is NOT simply where the on-disk index lives — see §5.

## 2. Data flow, end to end

```
tg ledger claim <path> --symbol S [--files F ...]
        |
        v
main.py: ledger_claim()                        <- CLI adapter (Typer command)
        |  (src/tensor_grep/cli/main.py,
        |   @ledger_app.command("claim"), main.py:12788-12789)
        v
ledger_store.submit_claim(path, symbols=..., files=..., ...)   (ledger_store.py:641)
        |  called at main.py:12845
        |
        +-- _ledger_physical_root(path)         <- canonicalize to the nearest .git ancestor
        |     (ledger_store.py:438)                 (the PATH-scope fix, see §5)
        |
        +-- _normalize_scope(path, root)        <- preserve the caller's ORIGINAL path
        |     (ledger_store.py:450)                 as this claim's "scope" (root-relative)
        |
        +-- validate: at least one of --symbol/--files (else LedgerUsageError)
        |
        +-- build a ClaimRecord (ledger_store.py:233)
        |
        +-- index_lock(index_path):             <- from tensor_grep.cli._index_lock
              _load_index(root)                     (ledger_store.py:527)
              _prune_expired(existing, now=now)      (ledger_store.py:550)
              _find_overlaps(live, record)            (ledger_store.py:592) -- BEFORE appending
              live.append(record.to_dict())
              _evict_oldest_over_cap(live)            (ledger_store.py:566)
              _write_index(root, live)                (ledger_store.py:543)

tg ledger list <path> [--symbol S] [--agent-id A]
        v
main.py: ledger_list()  (main.py:13014-13015)
        v
ledger_store.list_claims(path, symbol=symbol, agent_id=agent_id)   (main.py:13039)
        |  -- a pure read, mirrors session_store.list_sessions: prunes expired
        |     entries for DISPLAY only and never writes, so a bare `list` cannot
        |     itself create .tensor-grep/ledger/ (default-inert until first `claim`)
        +-- rolls scope UP via _scope_contains (ledger_store.py:479) -- see §5

tg ledger release <path> (--claim-id C | --symbol S) [--agent-id A]
        v
main.py: ledger_release()  (main.py:12906-12907)
        v
ledger_store.release_claim(...)   (main.py:12946)
        |  -- EXACT claim-id/symbol matching only, no rollup (see §5 for why)

tg ledger record <path> --receipt R --artifact-kind K --symbol S
        v
main.py: ledger_record()  (main.py:13083-13084)
        v
ledger_store.record_finding(...)   (main.py:13126, ledger_store.py:1219)
        |  -- same _ledger_physical_root + index_lock shape, separate on-disk
        |     subtree: findings/index.json + findings/blobs/<receipt_sha256>.json

tg ledger find <path> --symbol S
        v
main.py: ledger_find()  (main.py:13177-13178)
        v
ledger_store.find_findings(...)   (main.py:13223, ledger_store.py:1356)
        |  -- re-verifies the blob's bytes still hash to receipt_sha256 on every
        |     read (_verify_finding_blob, ledger_store.py:1314); checks freshness
        |     against the CURRENT repo revision, never trusts the stored one alone
```

**No MCP tool wraps `tg ledger`.** Verified: `grep -c "ledger" src/tensor_grep/cli/mcp_server.py`
returns `0`. Contrast with `tg-checkpoint.md`, whose feature DOES have MCP adapters — this guide
covers the CLI surface only.

## 3. Every file involved

| File | Contributes |
|---|---|
| `src/tensor_grep/cli/ledger_store.py` (1,417 lines) | All business logic: PATH canonicalization, the `ClaimRecord`/`FindingRecord` data types (ledger_store.py:233, :253), `submit_claim`/`release_claim`/`list_claims` (Slice 1), `record_finding`/`find_findings` (Slice 2), and every `LedgerError` subclass. |
| `src/tensor_grep/cli/_index_lock.py` | Shared atomic-write and cross-process/cross-thread file-lock primitives (`atomic_write_json`, `index_lock`) — ledger reuses these rather than re-implementing locking, the same way `checkpoint_store.py` does. |
| `src/tensor_grep/cli/session_store.py` | `_resolve_root` — the literal (non-canonicalized) path resolution `_ledger_physical_root` builds on top of. |
| `src/tensor_grep/cli/evidence_receipt.py` | `_repo_revision_identity` — captures `commit_sha`/`dirty`/`dirty_tree_sha256`, used to stamp every claim's `revision` field and every finding's freshness check. Both `record`/`find` call it with `exclude_prefixes=(".tensor-grep/ledger",)` so the ledger's own on-disk writes never make the repo look dirty against itself (mirrors the same param on `tg codemap --check`). |
| `src/tensor_grep/cli/evidence_signing.py` | `receipt_digest` (content-hash a finding's artifact bytes) and `verify_receipt` — Slice 2 findings are content-addressed and integrity-checked using the same functions `tg evidence` itself uses. |
| `src/tensor_grep/cli/main.py` | The Typer CLI adapter — five commands under `ledger_app` (registered via `app.add_typer(ledger_app, name="ledger")` at main.py:10856; the sub-app itself defined at main.py:552). Pure JSON/text formatting and exit-code mapping — no business logic. |

## 4. On-disk format (verified by actually creating and inspecting one)

Demo dir: a fresh `git init` repo with one committed file `a.txt`.

```
$ tg ledger claim . --symbol foo_bar --agent-id agentA --note "editing foo" --json
```

Real (trimmed) output:

```json
{
  "version": 1,
  "schema_version": 1,
  "routing_backend": "Ledger",
  "routing_reason": "ledger-claim",
  "sidecar_used": false,
  "ledger_schema_version": 1,
  "advisory": true,
  "claim": {
    "ledger_schema_version": 1,
    "kind": "claim",
    "claim_id": "claim-20260820210706875425-4e7fc092",
    "agent_id": "agentA",
    "symbols": ["foo_bar"],
    "files": [],
    "scope": ".",
    "intent": "edit",
    "note": "editing foo",
    "created_at": "2026-08-20T21:07:06.875425+00:00",
    "expires_at": "2026-08-20T21:22:06.875425+00:00",
    "ttl_seconds": 900,
    "revision": {
      "status": "present",
      "commit_sha": "eccaa8a43cf72fad51487ed8b08df56b41f138b4",
      "branch": "master",
      "dirty": false,
      "dirty_tree_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "dirty_file_count": 0
    }
  },
  "overlaps": []
}
```

Note explicitly — a real, load-bearing observation, not filler: the outer envelope carries
**both** `schema_version` (the shared tg CLI envelope) **and** `ledger_schema_version`
(`LEDGER_SCHEMA_VERSION = 1`, ledger_store.py:117) — two different version fields with different
meanings, easy to conflate in a naive rebuild.

Real on-disk layout after that single claim (`find .tensor-grep -maxdepth 6`):

```
.tensor-grep/
  ledger/
    claims/
      index.json
```

(No `.tensor-grep/ledger/findings/` subtree until the first `record` — same "default-inert until
first write" posture as `checkpoint_store.py`.)

`index.json`'s content at this point is a **JSON array**, one object per claim, the same shape as
the `claim` object above — i.e. the index IS the array of claim records, no wrapper object (cite
`_load_index`/`_write_index`, ledger_store.py:527, :543).

## 5. The PATH-scope footgun (the load-bearing trap; demonstrated live)

This section documents a real shipped bug (PR #706) and its fix, both currently guarded by
tests.

**The bug, as it shipped.** From the module's own docstring
(`src/tensor_grep/cli/ledger_store.py`, the "PATH scoping" paragraph): `claim core/hooks` and
`list .` each independently resolved `root` to a DIFFERENT physical directory
(`core/hooks/.tensor-grep/...` vs `./.tensor-grep/...`), so a claim filed from one subtree was
invisible to a list/release call from another subtree of the SAME repository, with no error or
signal.

**The fix.** `_ledger_physical_root(path)` (ledger_store.py:438) canonicalizes to the nearest
`.git` ancestor via `_discover_repo_root` (ledger_store.py:398) — git is treated as the one
unambiguous repo-boundary signal. Every claim/list/release/record/find call for the SAME
repository now reads and writes the SAME physical index regardless of which subtree `path`
names. The caller's original `path` is preserved SEPARATELY as that claim's `scope`
(root-relative, POSIX-normalized via `_normalize_scope`, ledger_store.py:450) — it is not lost,
just no longer used to pick a physical directory. Falls back to today's literal-path behavior,
unchanged, when no `.git` is found (a non-git working directory is not regressed — see §7).

**Rollup semantics on `list`.** Listing a broader/ancestor path shows every live claim scoped to
it OR to any descendant subtree, via `_scope_contains` (ledger_store.py:479) — segment-wise
containment (`PurePosixPath.parts`), explicitly NOT a raw string-prefix test (the function's own
docstring gives `"core/ho"` must not match `"core/hoodie"` as the reason). Deliberately
ONE-DIRECTIONAL: listing a narrower path does not show claims scoped to its ancestors.
`release_claim` does NOT get this rollup — an exact `--claim-id`/`--symbol` match only, because
rollup-matching a release could silently drop an unrelated sibling agent's claim that happens to
share a symbol name under a shared ancestor scope. When a release matches nothing, the response
names what IS live elsewhere (`unmatched_reason` / `live_claims_elsewhere`) instead of an
indistinguishable `released_count: 0`.

**Live demonstration — this exact scenario was actually run:**

```
$ tg ledger claim . --symbol foo_bar --agent-id agentA --json      # from repo root
# scope: "."
$ cd core/hooks
$ tg ledger claim . --symbol baz_qux --agent-id agentB --json      # from a subtree
# scope: "core/hooks"
$ cd ../..
$ tg ledger list . --json      # from repo root -- sees BOTH claims
# "count": 2
$ tg ledger list core --json   # from an intermediate ancestor -- sees only the descendant claim
# "count": 1  (agentB's claim, scope "core/hooks")
```

This proves the fix end to end: a claim filed from a subtree is visible to a list call from the
root (and, at an intermediate ancestor, rolls up correctly to only the descendant claim) — exactly
the failure mode #706 fixed.

**Also demonstrated: overlap reporting and release honesty.** A third claim on the same symbol
from a different agent:

```
$ tg ledger claim . --symbol foo_bar --agent-id agentC --json
```

returned `"overlaps": [{"claim_id": "...4e7fc092", "agent_id": "agentA", ..., "revision_matches":
true}]` — agentA's live claim, reported back, not raised as an error (never blocks, per §1).
Releasing a non-existent selector afterward:

```
$ tg ledger release . --symbol nope_symbol --agent-id agentA --json
```

returned `"released_count": 0`, `"unmatched_reason": "No live claim matched the given
--claim-id/--symbol; 2 live claim(s) exist in this repository -- see live_claims_elsewhere."`,
with `live_claims_elsewhere` naming both remaining live claims (agentC's `.`-scoped and agentB's
`core/hooks`-scoped) — the honesty fields described above, real output.

**Slice 2 inherited the identical bug, fixed later.** From the module docstring: Slice 2
(`record_finding`/`find_findings`) originally kept the old literal-path resolution deliberately
("per the same footgun it has not (yet) been reported for") — then WAS reported, twice, in live
external dogfoods (v1.101.7 and v1.101.9), with the identical symptom: `record` from the repo
root and `find` from a subtree resolved to different physical indices, so a lookup returned
nothing and a sibling agent silently recomputed an artifact that was already on disk. Both entry
points now use `_ledger_physical_root` on the same terms as Slice 1. This is a genuine "same bug
class shipped twice, months apart, because the justification for skipping the fix the first time
quietly expired" lesson.

**Live demonstration of the Slice-2 fix (also actually run):** a finding was `record`ed from the
repo root (via `tg evidence emit --query foo_bar --out receipt.json` then `tg ledger record .
--receipt receipt.json --artifact-kind evidence-receipt --symbol foo_bar --agent-id agentA
--json`); `tg ledger find . --symbol foo_bar --json` from a DIFFERENT subtree (`core/hooks`)
returned the SAME finding: `"count": 1`, `"any_fresh": true` — record-at-root, find-from-subtree
round-trips correctly.

## 6. Contracts and traps a naive rebuild gets wrong

1. **The PATH-scope footgun** — already covered in full in §5.

2. **A `--files` entry naming a path outside the repo root, or an absolute path, must be refused
   before anything is written — not silently normalized into something inside the root.**
   Actually demonstrated:

   ```
   $ tg ledger claim . --files "../../etc/passwd" --json
   ```

   real output: `{"...": "...", "advisory": true, "error": {"code": "fail_closed", "message":
   "Refusing claim --files entry outside repo root: '../../etc/passwd'"}}`, exit code 2. The same
   refusal, same `fail_closed` error code, same exit 2, fires for an absolute path. Cite
   `_normalize_relative_file` (ledger_store.py:374) as the guard and `LedgerTraversalError`
   (ledger_store.py:202) as the raised type.

3. **A tampered or corrupted findings blob must fail closed, never be silently served.**
   Actually demonstrated: after recording a finding successfully, its blob file at
   `.tensor-grep/ledger/findings/blobs/<receipt_sha256>.json` was overwritten with unrelated
   content (`{"tampered": true}`), then:

   ```
   $ tg ledger find . --symbol foo_bar --json
   ```

   real output: `{"...": "...", "advisory": true, "error": {"code": "fail_closed", "message":
   "Finding finding-20260820210740411003-662f8209 blob content does not match its recorded
   receipt_sha256 (tampered or corrupted)."}}`, exit code 2 (verified: the shell exit status after
   the command was `2`). Cite `LedgerIntegrityError` (ledger_store.py:223) and the blob-hash
   re-check at `_verify_finding_blob` (ledger_store.py:1314) — the recorded `receipt_sha256` is
   re-derived from the blob's actual bytes on every read, not trusted from the index alone.

4. **A retention cap must evict by actual creation time, not by list/insertion order.** Naive:
   `records[:max]` after prepending the newest. Under concurrent writers, lock-arrival order does
   not reliably match creation order (the timestamp is stamped before the lock is acquired). Fix:
   `_evict_oldest_over_cap` (ledger_store.py:566) sorts explicitly by `created_at` before slicing,
   capped at `_MAX_LIVE_CLAIMS = 256` (ledger_store.py:133). The function's own comment explains
   why `max_records` is resolved from the module constant `_MAX_LIVE_CLAIMS` INSIDE the function
   body rather than as a parameter default: a default-argument value binds once at
   function-definition time, so a test doing `monkeypatch.setattr(ledger_store, "_MAX_LIVE_CLAIMS",
   N)` would silently have no effect if the constant were captured as a default instead.

5. **An unavailable git identity on either side of a comparison must report "unknown", never a
   guessed match/mismatch.** `_revision_matches` (ledger_store.py:580) returns `None` — not
   `True` or `False` — whenever either side's revision `status` is not `"present"`. The function's
   own docstring gives the reasoning: an honest "unknown" beats a fabricated match/mismatch,
   mirroring how the rest of the codebase treats an unavailable evidence block. Contrast with the
   non-git-repo case demonstrated in §7: `list` from an unrelated non-git directory correctly
   returns `count: 0` rather than crashing or fabricating a match.

## 7. Non-git fallback (real, run)

A fresh, non-git directory with a subdirectory. `tg ledger claim . --symbol z --agent-id a1
--json` from the top returns `scope: "."` (same as the git case — `_normalize_scope` degrades
gracefully). But `tg ledger list .` run from the SUBDIRECTORY (no `.git` anywhere at all) returns
`count: 0` — i.e. without a `.git` boundary to canonicalize against, the old literal-path
behavior applies unchanged: the subdirectory resolves to its own separate physical
`.tensor-grep/` root, distinct from the parent's. This is a deliberate, documented
non-regression, not a gap: the fix in §5 only activates inside a git worktree; a non-git
directory tree keeps the pre-fix, literal-path semantics for every entry point.

## 8. What "done" looks like — validating a rebuild

```
python -m pytest tests/unit/test_ledger_store.py tests/unit/test_ledger_cli.py \
    tests/unit/test_ledger_concurrency.py tests/unit/test_findings_ledger_is_repo_scoped.py -q
```

Real result when run against this revision: **135 passed** (no skips, no failures). A handful of
test names are close to a spec on their own:

- `test_ledger_store.py::test_claim_refuses_files_outside_root` (trap 2)
- `test_ledger_store.py::test_claim_subpath_rolls_up_into_root_list` (§5 rollup)
- `test_ledger_store.py::test_old_format_claim_missing_scope_defaults_to_root_and_stays_visible`
  (an on-disk-compatibility trap not otherwise expanded in this guide: a claim written by a
  pre-fix binary, before `scope` existed, defaults to `"."` — the maximally visible scope — so it
  stays visible under the new rollup filter instead of silently vanishing)
- `tests/unit/test_findings_ledger_is_repo_scoped.py::test_a_finding_recorded_at_the_root_is_found_from_a_subtree`
  (§5 Slice-2 fix)
- `tests/unit/test_ledger_cli.py::test_ledger_find_exit_two_on_tampered_blob` (trap 3)
- `tests/unit/test_ledger_concurrency.py::test_concurrent_claim_no_lost_insert` (locking)

Then the two governance gates every docs-only change in this repo owes:

```
python -m pytest tests/unit/test_public_docs_governance.py tests/unit/test_skill_library_drift.py -q
```

## 9. Explicitly out of scope for this guide

- `tg ledger release`'s exact selector-matching semantics (claim-id vs symbol vs agent-scoping)
  are covered only enough to explain the rollup asymmetry in §5; full selector-combination
  behavior is not traced line by line.
- The `_index_lock.py` locking primitive itself (cross-process/cross-thread file locking,
  stale-lock reclaim) is treated as a given, shared dependency — not re-derived here. See
  `tg-checkpoint.md` if a locking deep-dive is needed (`checkpoint_store.py` uses the same
  primitive).
- No MCP tool wraps `tg ledger` (verified, §2/§3) — this guide covers only the CLI surface.
- `--artifact-kind` values other than `evidence-receipt` (`blast-radius`, `context-pack`,
  `repo-map`) were not independently exercised in this guide's live demo; only their presence in
  `tg ledger record --help`'s option list is cited.
