---
name: tensor-grep-ledger
description: Use when coordinating multiple agents on the same repo with tg ledger — advisory claim/release/list (Slice 1) and content-addressed finding record/find reuse (Slice 2). Never blocks edits; overlaps are informational. Distinct from tg evidence emit (produces receipts) and from docs/BACKLOG.md (product backlog, not this CLI).
---

# tensor-grep ledger (EXPERIMENTAL, ADVISORY-tier)

Verified against **tg 1.93.2** (2026-07-22; last full workspace sweep 2026-07-21 at v1.91.0 PASS;
Slice 1 PATH-canonicalization fixed v1.93.0/#706, re-verified via that PR's own closing dogfood).

`tg ledger` is **ADVISORY-tier**, not MANDATORY or OPTIONAL, in the vocabulary a multi-agent-coordination
ecosystem needs to stay legible (the same MANDATORY/OPTIONAL/ADVISORY split the `ruah` coordination
layer uses): a claim never blocks an edit, overlaps are reported for the caller to decide, and treating
one as a lock is a misuse of the contract, not a bug in it.

## When to use

| Need | Command |
| --- | --- |
| Tell sibling agents "I'm touching this symbol/file" | `tg ledger claim` |
| See live claims on a root | `tg ledger list` |
| Drop a claim | `tg ledger release` |
| Publish an artifact for reuse (evidence / blast-radius / context / map) | `tg ledger record` |
| Reuse a prior artifact instead of recomputing | `tg ledger find` |

**Advisory only** — claim never blocks an edit. Overlaps are reported for the caller to decide.

## Slice 1 — claims (PATH-canonicalization FIXED, v1.93.0/#706)

```bash
tg ledger claim REPO --symbol open_session --intent edit --agent-id "$AGENT_ID" --json
tg ledger list REPO --json
tg ledger release REPO --claim-id "$CLAIM_ID" --json
# or: tg ledger release REPO --symbol open_session --agent-id "$AGENT_ID" --json
```

- **Fixed behavior (v1.93.0, #706 — the root cause of the 1.92.1-era "PATH trap" was PHYSICAL: each
  command resolved the store directory from the literal PATH argument, so `claim core/hooks` and
  `list .` landed in two different on-disk stores).** Claims now canonicalize to the **nearest `.git`
  ancestor** (a worktree's `.git` FILE — not just a directory — is resolved correctly; a non-git dir
  falls back to the literal path), so `claim core/hooks` and `list .` (or `list $REPO_ROOT`, or any
  other subtree PATH under the same repo) hit the **same store**. `list [PATH]` additionally **rolls
  scope UP**: a claim recorded at `core/hooks` is visible from a `list` at the repo root, matched by a
  segment-wise `_scope_contains` check (root-relative POSIX segments), not a naive string-prefix
  compare — so `core/hooks-extra` can no longer false-match a claim scoped to `core/hooks`.
  `ClaimRecord` gained a stored `scope` field (root-relative POSIX path) that `list`/`release` match
  against. A `release` with `--claim-id`/`--symbol` that matches nothing now emits `unmatched_reason`
  + a bounded `live_claims_elsewhere` list (so the caller can see the claim IS there, just not at the
  path/symbol combination asked for) instead of a bare `released_count: 0`. A **bare-path** release
  with neither `--claim-id` nor `--symbol` now **fails closed** ("requires --claim-id or --symbol")
  rather than silently no-op'ing.
- **Migration note:** claim/list/release stores written before #706 (keyed by the pre-fix literal-path
  scheme) become invisible orphans under the new canonical-store resolution — their content is
  TTL-bounded (see below) and safe to hand-delete; they are not corrupt, just superseded.
- Exit `0` on success (including overlaps present, or release of unknown/expired claim).
- Exit `2` only on fail-closed (lock timeout, `--files` outside root, write failure, or the new
  bare-path-release-with-no-identifier case above).
- TTL default 900s (`TG_LEDGER_CLAIM_TTL_SECONDS`) — deliberately shorter than a long-running agent
  session and shorter than some comparable tools (e.g. a 1800s TTL in `chump`) but longer than others
  (e.g. `ContextNest`'s 120s) — the choice trades "a crashed agent's claim ages out reasonably fast"
  against "a normal edit-verify-commit loop doesn't need to re-claim mid-task."
- `--agent-id` is never inferred; falls back to `TG_LEDGER_AGENT_ID` then `TG_EVIDENCE_AGENT_ID`
  (else `anonymous` via `prepare --claim`, which also stamps `coordination.claim.agent_id_hint`).
- **Verify-before-relying on worktrees:** the canonical-store fix resolves a worktree's `.git` FILE to
  its real common store, so sibling worktrees of one logical repo SHOULD share a claims store — but
  dogfood-confirm this on your own setup (`tg ledger claim` in worktree A, `tg ledger list` in worktree
  B) before depending on cross-worktree coordination for something load-bearing; don't assume it from
  this doc alone.
- **External backing for "narrow the claim":** AgenticFlict-class research on AI-authored PRs measures
  merge-conflict rate rising sharply with edit footprint (~9.9% at a 2-line change vs ~30% at 25+ lines,
  ~27.67% overall for AI-authored PRs) — prefer `REPO/src` over whole-repo scope and a single-symbol
  claim over a broad one; a narrow claim is not just politeness, it measurably lowers the chance two
  agents' real edits collide even though the ledger itself never blocks either one.

Dogfood: claim/list/release round-trip PASS from any subtree PATH under the same repo (v1.93.0/#706
closing dogfood); record+find fresh PASS (Slice 2, unaffected by the Slice-1 fix — see below).

## Slice 2 — findings reuse (UNCHANGED — still literal-path-rooted, the OLD footgun survives here)

**This slice did NOT get the #706 canonicalization fix.** `record`/`find` still resolve their store
from the literal PATH argument exactly the way Slice 1 used to — a `record` at `core/hooks` and a
`find` at `.`/repo-root are NOT guaranteed to hit the same store. Do not assume Slice 2 inherited
Slice 1's fix; treat this as a known, still-open footgun until a matching PR closes it.

```bash
tg evidence emit REPO --capsule capsule.json --query "task" --json --agent-id "$AGENT_ID" > receipt.json
tg ledger record REPO --receipt receipt.json --artifact-kind evidence-receipt --symbol open_session --agent-id "$AGENT_ID" --json
tg ledger find REPO --symbol open_session --artifact-kind evidence-receipt --fresh-only --json
```

`--artifact-kind`: `evidence-receipt` | `blast-radius` | `context-pack` | `repo-map`.

**`find` exit contract (3-state):**

| Exit | Meaning |
| --- | --- |
| `0` | ≥1 fresh finding (revision matches — safe to reuse) |
| `1` | nothing matched **or** matches exist but none fresh → recompute |
| `2` | fail-closed (missing `--symbol`, corrupt index/blob) |

Dogfood: empty find exit 1 PASS; after record, find exit 0 PASS (~16s).

## Agent loop with ledger

```bash
tg ledger claim REPO/src --symbol SYM --agent-id "$AGENT_ID" --json
# if overlaps: coordinate or proceed knowingly (advisory)
tg agent REPO/src "task" --json > capsule.json
tg evidence emit REPO --capsule capsule.json --query "task" --json --agent-id "$AGENT_ID" > receipt.json
tg ledger record REPO --receipt receipt.json --symbol SYM --agent-id "$AGENT_ID" --json
tg ledger release REPO --symbol SYM --agent-id "$AGENT_ID" --json
```

## Related

- `tensor-grep`, `tensor-grep-enterprise-agent`, `tensor-grep-enterprise-review-bundle`
- Surface/schema may change in a minor release — re-check `tg ledger --help` on upgrades
