> **STALE (2026-08-30 closeout).** PR 1123 is MERGED (`e6ba187`), not OPEN. Next-slice HYGIENE-FORMAT is RETIRED. Governance 97 was not re-run locally. Unsettled rows live in `backlog.md`, not memory. Composition: `compose-build-pipeline`.

# DOCS-RECONCILE — Answer key (wayfinder)

**Slice:** Stamp `docs/TASK_BOARD.md` + `docs/SESSION_HANDOFF.md` after SEC-001 + ENV-SYNC.

## Done when (all must pass)

| Check | Command | Pass criterion |
|---|---|---|
| Reconcile stamp | `rg -n 'post-\*\*v1\.113\.5\*\*' docs/TASK_BOARD.md` | First stamp is v1.113.5 (tolerance gate reads first match) |
| Canonical index | `rg 'Canonical status index version: 2026-08-30.1' docs/TASK_BOARD.md docs/SESSION_HANDOFF.md` | Both files match |
| F8 trigger | `rg 'path_domain' docs/TASK_BOARD.md` | No stale `path_domain.rs` as active blocker without re-derive note |
| Governance | `uv run --no-sync python -m pytest tests/unit/test_task_board_freshness.py tests/unit/test_backlog_tracker_truth.py tests/unit/test_public_docs_governance.py -q` | 97 passed |
| PR open | `gh pr view 1123 --json state,baseRefName` | OPEN, base `main` |
| No product code | `git diff origin/main...HEAD --name-only` | Only `docs/**` paths (includes `docs/plans/DOCS-RECONCILE.md` plan artifact) |

## Evidence (closeout 2026-08-30)

- PR #1123 / head `3b6145b1a72ffb5a72392e6acbed8bc787b412aa`
- CI run `33295639980` — **success** (docs-only; heavy lanes skipped by design)
- Prior run `33295196600` — success on `2fb11a5` (pre-plan commit)

## Next slice

**HYGIENE-FORMAT** — `ruff format --preview` on 15 markdown files (`docs:` PR).
