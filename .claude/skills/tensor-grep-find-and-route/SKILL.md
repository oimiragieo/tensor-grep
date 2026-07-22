---
name: tensor-grep-find-and-route
description: Use when vocabulary-mismatched queries need whole-repo hybrid search via tg find (BM25 + optional dense RRF, no regex pre-filter), or when verifying context-render vs edit-plan target agreement with tg route-test before trusting an edit plan. Distinct from tg search --rank/--semantic (those re-rank an existing regex match set).
---

# tensor-grep find + route-test

Verified against **tg 1.93.2** (2026-07-22; prior full dogfood 2026-07-21 WSL workspace sweep at v1.91.0).

## When to use

| Need | Command |
| --- | --- |
| Natural-language / mismatched vocabulary over a **whole repo** (no pattern pre-filter) | `tg find "query" PATH --json` |
| Confirm `context-render` and `edit-plan` agree on primary file/symbol/line | `tg route-test PATH "query" --json` |

Do **not** use `tg find` as a grep replacement (`--format rg` is intentionally absent). Prefer scoped `PATH` (`REPO/src`) first.

## `tg find`

```bash
tg find "session daemon timeout handling" REPO/src --deadline 20 --json
tg find "session daemon timeout handling" REPO --deadline 30 --json
```

- Bounded by default (`--max-repo-files`, `--deadline`, internal chunk cap).
- Truncation → `result_incomplete` + exit `2` (never silent partial-as-complete).
- **Bare `tg find "query"` with no PATH does NOT hit the search fast-refuse** — `find` defaults PATH to
  `.` (it is not bootstrap-intercepted; the `IMPLICIT_SEARCH_WALK_FILE_CEILING=1500` fast-refuse is a
  `tg search` front-door behavior, v1.92.3). `find` bounds itself via `--max-repo-files` (default 2000)
  plus `--deadline`/chunk caps, and marks truncation honestly (`result_incomplete` + exit `2`). Still:
  always scope `tg find` to a PATH — for ranking quality and so a big root doesn't truncate the corpus.
- Dense leg: prefer **`tg install-dense`** (one-shot pip + pinned potion-code-16M). Without it, find is
  BM25-only and reports `rank_fallback_reason` — supported, not silent. The fallback message is now the
  literal `retrieval_dense.py` string (A12(a), v1.93.0/#705): `` semantic ranking unavailable: model2vec
  not installed -- run `tg install-dense` (or pip install 'tensor-grep[semantic]') `` — every
  dense-absent hint across the CLI leads with `tg install-dense` the same way, not just this one.
- Dogfood (1.91.0): `find_src` ~8.4s PASS (BM25 before install-dense); `find_src_postdense` ~21s PASS (no fallback).

## `tg route-test`

```bash
tg route-test REPO/src "improve session daemon timeout" --json
```

- Emits `agreement` + per-field `agreement_details` (`file`/`symbol`/`line`).
- Dogfood (1.91.0): `agreement=true` on tensor-grep/src (~27s alone; can exceed 60s under WSL suite load — budget 90s). **This evidence predates the #693/#250 primary-target ranking fix (v1.91.2) that it is meant to validate — re-collect the `agreement=true` proof on v1.93.2 before citing it as current confirmation of post-fix routing agreement.**
- Use before trusting an edit-plan primary when routes might diverge.

## Related

- `tensor-grep`, `tensor-grep-enterprise-agent`, `tensor-grep-semantic-search-campaign` (build history for dense/RRF)
- `tg search --rank` / `--semantic` re-rank regex hits — different contract than `tg find`
