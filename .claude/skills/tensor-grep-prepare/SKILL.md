---
name: tensor-grep-prepare
description: Use when an agent needs one-call edit readiness before changing code — tg prepare returns primary target, confidence, callers/blast-radius floor, validation commands, and claim/evidence coordination hooks. Prefer over the multi-step orient→search→agent→route-test→callers→evidence→ledger loop for routine edits. Pair with tg install-dense once for semantic find quality.
---

# tensor-grep prepare (one-call edit readiness)

Verified against **tg 1.95.0** (2026-07-24; re-verified the `prepare` CLI contract against
`src/tensor_grep/cli/main.py` — `--claim`/`--deadline` (default 60s)/`--no-deadline`/`--out`
(symlink + dangling-symlink + directory-destination refusal), the `agent_id_hint` claim-hook, and
the #706 ledger PATH-canonicalization fix all still hold; `--out` shipped v1.93.0/#705; prior full
dogfood passes at 1.92.1 gotcontext-saddle and 1.91.0 workspace sweep, both still representative of
the core CUJ).

## When to use

| Need | Command |
| --- | --- |
| Single call before editing | `tg prepare REPO/src "task" --json` |
| Also submit advisory ledger claim | `tg prepare REPO/src "task" --claim --json` |
| Persist the capsule for evidence emit | `tg prepare REPO/src "task" --out capsule.json --json` |
| Whole-repo / large root | `tg prepare REPO "task" --deadline 20 --json` (expect partial) |

Prefer **`REPO/src`**. Default deadline is 60s (like agent cold path); pass `--no-deadline` only when intentional.

## What you get

- `primary_target` + `confidence` + `ask_user_before_editing`
- `validation_commands`
- `blast_radius_floor` (callers_count, top_callers, trust summary)
- `coordination.claim` — args ready; `submitted=true` only with `--claim`; an anonymous claim (no
  `TG_LEDGER_AGENT_ID` set) additionally stamps `coordination.claim.agent_id_hint` ("set
  TG_LEDGER_AGENT_ID for a stable identity") so the caller knows why `agent_id` reads `anonymous`
- `coordination.evidence` — argv/note to emit a receipt without guessing flags

Dogfood:

| Case | Result |
| --- | --- |
| `prepare core/hooks matches_trigger` (1.92.1) | PASS ~8s — overall 0.9, callers_count=1, claim.submitted=false |
| `prepare … --claim` (1.92.1) | PASS ~9s — claim.submitted=true (`agent_id` anonymous unless env set) |
| `prepare … --out capsule.json` (1.93.0) | PASS — file written byte-identical to stdout JSON; symlink/dangling-symlink/dir destination refused |
| `prepare tensor-grep/src` (1.91.0) | PASS ~27s — callers_count=2, claim.submitted=false |
| `prepare tensor-grep --deadline 20` (1.91.0) | INCOMPLETE — partial/deadline (same class as whole-repo agent) |

**`--out FILE` (v1.93.0, A12(d)) replaces the old manual-redirect workflow.** Chain straight into
evidence emit, no separate save step:

```bash
tg prepare REPO/src "task" --out capsule.json --json
tg evidence emit REPO --capsule capsule.json --query "task" --json --agent-id "$AGENT_ID" --out receipt.json
```

Without `--out`, prepare still does **not** write the capsule file on its own — save stdout instead
(`tg prepare ... --json > capsule.json`), or use `coordination.evidence.argv`.

**Ledger cross-ref:** `--claim`'s behavior is affected by the #706 PATH-canonicalization fix (see
`tensor-grep-ledger`) — a claim submitted here is now visible from `tg ledger list` at any subtree PATH
under the same repo, not just the exact PATH `prepare` was run against.

## Hard stops

1. `ask_user_before_editing.required`
2. `partial` / `result_incomplete` / exit `2` — do not claim full coverage
3. Empty `blast_radius_floor` with `source=no_primary_symbol` — narrow PATH or raise budget

## Dense semantic companion

```bash
tg install-dense --json   # once per machine; never auto-runs
tg find "intent" REPO/src --deadline 20 --json   # dense leg after install
```

Dogfood: `install-dense` PASS ~4s (uv-tool + potion-code-16M); post-install `tg find` no longer reports BM25-only `rank_fallback_reason`.

## Related

- `tensor-grep`, `tensor-grep-ledger`, `tensor-grep-find-and-route`, `tensor-grep-enterprise-agent`
