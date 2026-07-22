---
name: tensor-grep-enterprise-agent
description: Use when designing or evaluating tensor-grep as an enterprise agentic code-intelligence tool — tg prepare one-call edit readiness, PATH narrowing, agent --deadline, find/route-test/ledger, install-dense, EvidenceReceipts, review-bundle, GPU honesty, world-class readiness gaps.
---

# tensor-grep for enterprise agents

Verified against **tg 1.93.2** (last full WSL workspace+GPU dogfood 2026-07-21 at v1.91.0; individual
gaps below re-verified against the shipped v1.91.1-v1.93.2 line, not a re-run whole-workspace sweep).

## Guidance

- **Default edit gate:** `tg prepare REPO/src "task" --json` (replaces orient→agent→route-test→callers→evidence argv guessing). Use `--claim` when multi-agent coordination is needed; use `--out FILE` to persist the capsule for `tg evidence emit --capsule FILE` with no manual save.
- Prefer `REPO/src`. Whole-repo: `tg prepare|agent REPO --deadline N` → expect partial / ask_user.
- Do not trust bare cold-path default alone on WSL (`tg agent REPO` still empty TIMEOUT @75s).
- Dense find: run `tg install-dense` once per machine (never auto); then `tg find`. Every dense-absent hint across the CLI now leads with `tg install-dense`.
- Ledger remains advisory — see `tensor-grep-ledger`. Claim/release/list now canonicalize to the nearest `.git` ancestor (worktree-aware); the PATH-mismatch footgun from 1.92.1-era dogfood is fixed.
- Skip `tg codemap` on WSL. GPU inventory ≠ acceleration; the WSL bare-shim cross-domain misclassification that produced a bogus `path_not_found` is fixed (v1.93.0).

## Hard stops

1. `ask_user_before_editing.required`
2. Full-coverage claims on `partial` / exit `2`
3. Unscoped workspace search refuse
4. GPU promotion without `search_ready`
5. `review-bundle create` without `--manifest`
6. `route-test.agreement == false` (when not using prepare's floor)
7. Treating ledger overlaps as hard locks

## Enterprise gaps (`world_class_readiness = not_claimed`)

| Gap | v1.93.2 status |
| --- | --- |
| Whole-repo agent/prepare default deadline reliability | Open (explicit deadline partial OK; bare agent TIMEOUT) |
| CUDA-native GPU promotion | Open (adjudicated HOLD, #169 CEO-gated; kernel is brute-force byte-compare, not PFAC) |
| `codemap` on WSL | Open (TIMEOUT) |
| Mega-repo auto-narrow + accurate deadline primaries | Partial (`suggested_scope`/`workspace_root_detected` shipped, #684; deadline-primary accuracy still open) |
| Unscoped-search fast-refuse on the default flag-less path | **Shipped** (A9; generic 1500-file ceiling, ~1.7s, all 3 doors) |
| Dynamic-import / blast-radius decoy honesty | **Shipped** (A10/A15; `dynamic_unresolved` excluded from forward/reverse resolution and the blast-radius scoring prefilter) |
| One-call prepare CUJ | **Shipped** (prefer `src/`; `--out FILE` persists the capsule) |
| Packaged dense semantic | **Shipped via `install-dense`** (opt-in, once; every dense-absent hint now leads with it) |
| Ledger → CI / review-bundle bridge | Partial — `review-bundle --receipt`/`--against` CI gate chain shipped (#681); ledger itself stays advisory, not wired into a CI gate |
| Agent accuracy gate | **Shipped** (`tests/eval/test_agent_accuracy.py`, per-task-pinned, 16/16 golden tasks — the loop-4 measurement instrument that surfaced and fixed #250) |
| Beat-`rg` cold search + LSP proof | Open |

## Recommended loop

```bash
tg install-dense --json   # once per host
tg prepare REPO/src "task" --out /tmp/prep.json --json
# if ask_user / partial: narrow PATH or raise --deadline; do not edit yet
# optional: tg prepare REPO/src "task" --claim --json
# then edit from primary_target; run validation_commands; optionally:
tg evidence emit REPO --capsule /tmp/prep.json --query "task" --json --agent-id "$AGENT_ID"
```
