---
name: tensor-grep-index-fingerprint-freshness
description: >-
  Use when working on index/reuse staleness detection — the Rust `.tg_index` reuse path, a cached
  parse/symbol/semantic index that is served on a LATER query, `staleness_reason` /
  `compute_tree_fingerprint` / mtime-size identity, or a "reused index must not serve the wrong
  tree" guard (M17). Covers identity-anchor-first (canonical root, not a raw path string), the
  alias/swap RED shape that a naive wrong-root comparison cannot reach, the fingerprint ladder
  (mtime+size → ctime → content-checksum → whole-tree), and bounded sampling with NAMED boundaries
  so the honest cap is a reopen trigger, not a silent hole. Triggers: "index reuse", "staleness",
  "fingerprint", "wrong tree", "stored root", "canonical_root_of", "mtime size", "ctime",
  "sampling cap", "M17", "swap". Sibling of tensor-grep-cross-platform-path-confinement (the alias
  swap is a junction there) and tensor-grep-hermetic-hostile-tests (the fixture must BITE); this one
  is the identity/freshness discipline for CACHED INDEX REUSE.
---

# tensor-grep: index fingerprint + reuse freshness

An index is a memory of a PAST tree. Serving it on a LATER query is only correct if the past tree
is still the queried tree — and "still the queried tree" is a WEAKER claim than "nothing changed",
because the same path string can name a DIFFERENT tree (a swapped symlink/junction, a renamed tree,
a copied/tampered `.tg_index`). The M17 finding (index reuse) was the exact case: `staleness_reason`
checks only no-ignore mode + per-entry mtime/size + a new-file walk **over `self.root`** — it never
asks "is the stored root the queried root, canonically?".

---

## Part 1 — Identity-anchor-first: canonical root, never a raw path string

The M17 structural fix (merged on origin/main as v1.110.12; current tag v1.110.14) is the model:

- [ ] Persist a **canonical** root at build: `canonical_root_of()` recorded once (format v6) —
      lexical AND canonical, so later path-spelling differences (case, `..`, junctions in the
      prefix) cannot manufacture a mismatch that is not real.
- [ ] Store entries **canonical-relative** to that root with a single deref
      (`canonical_root.join(rel)`), so NOTHING is cwd-dependent — a later query from a different
      working directory must not change where entries resolve.
- [ ] At reuse/warm-load, compare **canonical QUERY root vs canonical STORED root** at EVERY load
      site — the `preloaded_index` arm handed off by `detect_warm_index_state` AND the fresh-reuse
      branch — and the check must run BEFORE `staleness_reason`/incremental update.
- [ ] On mismatch: REBUILD from the current tree (always safe in-tree — prefer rebuild over bare
      refuse) with a disclosed reason — rebuild must not be invisible latency.

Root bytes are already persisted (`index.rs`, `root` field, `:272-274` write / `:355-356,413`
read on origin/main); the defect was that the reuse path never COMPARED them to the query root.

---

## Part 2 — The alias/swap RED shape (the repro that actually bites)

CRITICAL constraint: `resolve_index_path` puts `.tg_index` INSIDE the tree, so a plain "build root
A, query root B" NEVER shares an index file — the naive RED is unreachable. The real wrong-tree
serve needs an **ALIAS**: same string, swapped tree:

- [ ] RED fixture = a root symlink/junction under a FIXED path, swapped to a different directory
      BETWEEN build and query (Windows: a junction — see
      tensor-grep-cross-platform-path-confinement Part 1); on a metadata-preserving swap this is
      the ONLY shape that still looks fresh.
- [ ] The fixture carries the fixture-BITES precheck (prove the swap actually applied — the junction
      must resolve to the NEW dir before the probe runs; see tensor-grep-hermetic-hostile-tests).
- [ ] A naive `stored_root == query_root` string compare PASSES on the alias (same string) — the
      canonical-root comparison is what separates "same text" from "same tree".
- [ ] Keep the per-file identity walk (`staleness_reason`) as the "still the queried tree" backstop
      — it is not replaced, it is supplemented.

---

## Part 3 — The fingerprint ladder: climb only as far as the threat demands

| Level | Signal | Evadable by | Use when |
|---|---|---|---|
| 1 | **mtime + size** (per entry) | a same-size/same-mtime edit (`touch -r`, an rsync that preserves mtime+size) | cheap per-entry freshness in `staleness_reason`'s loop — NEVER a tree-level claim |
| 2 | **ctime** (change/inode time) | clock-restored file systems, a swap that preserves ctime — but catches `touch -r` on most real systems | an intermediate tier when same-mtime edits are in the threat model |
| 3 | **content checksum** (per sampled file, FULL bytes) | almost nothing short of a genuine same-checksum collision | the sampled-file tier of `compute_tree_fingerprint` — the 4 KiB byte cap that let an edit past offset 4096 evade was a real finding |
| 4 | **whole-tree** (every file fully hashed) | nothing short of a full collision | the M17-FU1 follow-up for unsampled files (below) |

Receipts that shaped the ladder: the M17 gate round-2 finding that the initial 4 KiB-per-file byte
cap let a same-size/same-mtime edit past byte 4096 in a SAMPLED file evade detection, and that
inode identity would not catch it either — so sampled files are hashed in FULL (bounded by the
32-file cap, not by bytes). See `compute_tree_fingerprint` (`index.rs`, grep the symbol).

---

## Part 4 — Bounded sampling with NAMED boundaries (the reopen-trigger discipline, A49)

`compute_tree_fingerprint` samples the **top-32** top-level DIRECT children of the canonical root,
fully hashed, index-machinery namespace excluded (a leftover `.tg_index.<token>.tmp` / lock must
not consume a sample slot — `is_tg_index_owned_entry`). The honest remaining boundary is NAMED,
not hidden:

- [ ] The cap (`TREE_FINGERPRINT_TOP_LEVEL_CAP = 32`) is documented AT the constant with the
      reason ("deliberately bounded in FILE COUNT; each sampled file is FULLY content-hashed").
- [ ] The files NOT sampled (33rd+ top-level files, and every NON-top-level file) are covered only
      by the per-file mtime/size loop — that gap is **M17-FU1**, with owner + reopen trigger,
      recorded canonically (see docs/plans/2026-08-08-backlog-completion-plan.md M17 section and
      BACKLOG's M-follow-up rows).
- [ ] A sampling cap is an honest tradeoff, but it must be a *named* tradeoff with a reopening
      trigger (A49), never a silent assumption — "a same-path metadata-preserving swap landing in
      an unsampled file that mtime/size cannot detect" is that trigger.

The M17 REOPEN TRIGGER pattern applies to every fingerprint surface in the repo, including the
Python parse/semantic caches:

- `semantic_index.py` stale check = SHA-256 fingerprint over sorted paths + mtimes; on mismatch it
  warns to stderr and falls back in-memory (NOT wired to a `tg index` command yet).
- `repo_map.py` `_mtime_aware_cache` (grep the symbol) is the Python cache-freshness wrapper with a
  `_MTIME_CACHE_CLEAR_REGISTRY` that sweeps every decorated cache — a per-root keyed re-export cache
  is deliberately NOT one of them, and the boundary is documented at the symbol.

---

## External anchors (Exa research, 2026-08-09)

| Anchor | Their point | This skill's mapping |
|---|---|---|
| **rsync/backup-tool mtime-size semantics** (rsync(1) manpage; rsync issue/fix #627 for ctime handling in some tools) | mtime+size is cheap but is the classic evadable signature — restoring mtime+size is a standard attack on freshness heuristics | Part 3's ladder: why you climb to ctime/checksum when same-mtime edits are in the threat model. |
| **"Do not trust mtime alone for cache invalidation"** (general build-tool / ccache / ninja literature; the same reason `touch -r` defeats incremental builds) | File-system metadata timestamps are attacker-influenceable (or accidentally restorable) | Level 1's "NEVER a tree-level claim" + the 4 KiB-cap receipt. |
| **Content-addressed / digest-bounded verification (general)** | A bounded digest is a decision about the threat's cost, not a correctness shortcut | Part 4's named sampling cap + reopen trigger discipline. |

**Repo receipts to cite by symbol, not line:** `index.rs` `root` / `staleness_reason` /
`compute_tree_fingerprint` / `TREE_FINGERPRINT_TOP_LEVEL_CAP` / `is_tg_index_owned_entry`;
`main.rs` `resolve_index_path` / the reuse branch / `detect_warm_index_state`;
`src/tensor_grep/core/semantic_index.py` (the SHA-256 mtime-path fingerprint + fallback);
`repo_map.py` `_mtime_aware_cache` / `_MTIME_CACHE_CLEAR_REGISTRY`; AGENTS.md A44/A49 and the
verification-oracle family (a mismatch that is not real is a Form-10 branch-unit false green).
Grep the symbol, never a hardcoded line number; and record every deferred fingerprint boundary as a
named M-FU row with owner + reopen trigger.

---

## Quick reference

```
[1] identity   canonical query root vs canonical STORED root, at BOTH load sites, BEFORE staleness
[2] REPRO       wrong-tree serve needs an ALIAS/SWAP (junction), never the naive wrong-path RED
[3] ladder      mtime+size -> ctime -> full-content checksum -> whole-tree; climb to the threat
[4] named cap   32-file sample is bounded AND the unsampled gap is M17-FU1 with an owner+trigger
[5] mismatch    rebuild with a disclosed reason, never invisible latency, never bare refuse
```

The endpoint: an index that never serves a tree it was not built for, whose freshness proof is as
strong as the threat requires, and whose sampling cap is an honest, named, reopenable boundary.
