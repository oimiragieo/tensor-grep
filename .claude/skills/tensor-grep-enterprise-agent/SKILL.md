---
name: tensor-grep-enterprise-agent
description: Use when designing or evaluating tensor-grep as an enterprise agentic code-intelligence tool — tg prepare one-call edit readiness, PATH narrowing, agent --deadline, find/route-test/ledger, install-dense, EvidenceReceipts, review-bundle, GPU honesty, world-class readiness gaps.
---

# tensor-grep for enterprise agents

Verified against **tg 1.98.25** (2026-07-26 refresh of the gap table only; the WSL workspace+GPU
rows still date from the 2026-07-21 v1.91.0 dogfood and are marked as such. Individual gaps are
re-verified by source inspection against the shipped line, not a re-run whole-workspace sweep — see
the native-scale dogfood bullet below for a fresh large-repo data point).

**Each row states when it was last checked; rows without a date are carried forward unverified.**
A row re-verified today and one inherited from a five-week-old dogfood are not the same evidence,
and a gap table that flattens the difference is exactly the drift that let "8/10 languages, C/C++
deferred" survive three releases past the campaign that shipped them. When you re-verify a row,
stamp it.

## Guidance

- **Default edit gate:** `tg prepare REPO/src "task" --json` (replaces orient→agent→route-test→callers→evidence argv guessing). Use `--claim` when multi-agent coordination is needed; use `--out FILE` to persist the capsule for `tg evidence emit --capsule FILE` with no manual save.
- Prefer `REPO/src`. Whole-repo: `tg prepare|agent REPO --deadline N` → expect partial / ask_user.
- Do not trust bare cold-path default alone on WSL (`tg agent REPO` still empty TIMEOUT @75s). *Last observed 2026-07-21 @ v1.91.0; not re-run since.*
- At real workspace scale — native Windows, 300k+ files, a separate data point from the WSL caveat above: `tg orient`/`tg search`/`tg inventory --deadline` all bound gracefully (`orient` ~4.9s via scan_limit+centrality; `search` returns partial plus an honest "exceeded timeout" message, exit 124; `inventory --deadline` bounds per-project). Known low-priority edge: a single non-lazy `os.scandir` call in the shared `_iter_repo_files` can still blow `inventory --deadline` on a pathological workspace-union tree (rare; not worth a load-bearing fix).
- Dense find: run `tg install-dense` once per machine (never auto); then `tg find`. Every dense-absent hint across the CLI now leads with `tg install-dense`.
- Ledger remains advisory — see `tensor-grep-ledger`. Claim/release/list now canonicalize to the nearest `.git` ancestor (worktree-aware); the PATH-mismatch footgun from 1.92.1-era dogfood is fixed.
- Skip `tg codemap` on WSL (*last observed 2026-07-21 @ v1.91.0; not re-run since*). GPU inventory ≠ acceleration; the WSL bare-shim cross-domain misclassification that produced a bogus `path_not_found` is fixed (v1.93.0).

## Hard stops

1. `ask_user_before_editing.required`
2. Full-coverage claims on `partial` / exit `2`
3. Unscoped workspace search refuse
4. GPU promotion without `search_ready`
5. `review-bundle create` without `--manifest`
6. `route-test.agreement == false` (when not using prepare's floor)
7. Treating ledger overlaps as hard locks

## Enterprise gaps (`world_class_readiness = not_claimed`)

| Gap | Status (version + date where re-verified) |
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
| Symbol-graph language coverage | **Shipped 10/10** (re-verified 2026-07-26 by reading the live `LANGUAGE_REGISTRY`: `c, cpp, csharp, go, java, javascript, php, python, rust, typescript`. C/C++ landed via `lang_c.py`/`lang_cpp.py`; accepted ceiling: `class MACRO Name` in C++ misparses — do not re-chase) |
| Beat-`rg` cold search | **Closed — honest negative** (#261). Startup is at parity (rg 6.2ms / tg 6.5ms), GPU is dead (3 proofs), `.tg_index` measured NET NEGATIVE (~10x slower), and tg's native walk *is* rg's walk (same `ignore` crate) — so widening it relocates cost, never removes it. The campaign's return was a defect family, not milliseconds. Do not re-measure. |
| LSP proof | Open |
| Trust: incompleteness disclosure across the CLI | Partial (#292). §0 of `docs/CONTRACTS.md` pins the completeness contract; a silent-loss census ratchet guards regressions; 7 disclosure defects fixed this cycle (codemap, checkpoint-undo data-loss, tg scan). Cross-platform benchmark says tg **ties** rg and GNU grep on unreadable-path disclosure (2/2/0 both OSes) — it does not lead. |
| Trust: `--json`/`--ndjson` incompleteness marker | **Open, and the load-bearing enterprise gap** (#276). The native JSON envelope reports success while walk errors go only to stderr, so an agent consuming `--json` cannot distinguish *truncated* from *absent*. Until this closes, a machine consumer has no in-band completeness signal on the fastest path. |

## Recommended loop

```bash
tg install-dense --json   # once per host
tg prepare REPO/src "task" --out /tmp/prep.json --json
# if ask_user / partial: narrow PATH or raise --deadline; do not edit yet
# optional: tg prepare REPO/src "task" --claim --json
# then edit from primary_target; run validation_commands; optionally:
tg evidence emit REPO --capsule /tmp/prep.json --query "task" --json --agent-id "$AGENT_ID"
```
