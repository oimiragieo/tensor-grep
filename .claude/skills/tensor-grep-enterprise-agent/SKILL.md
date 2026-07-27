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
- **The WSL `tg agent REPO` "empty TIMEOUT @75s" claim is DEBUNKED -- do not re-chase it.**
  `docs/BACKLOG.md:603` records #578 correcting this as one of TWO false WSL-`/mnt/c` "regression"
  claims; the native repro is **~26s**. The scan is already interruptible and already keeps a usable
  partial (`repo_map.py:7400`: *"Break + keep what we have -- never raise, never zero the results"*;
  measured 7/45 files at a 0.05s deadline still yields the correct `primary_target`).
  What actually produces an apparently-empty result is the **post-deadline overshoot**, which is
  deliberate: per `docs/CONTRACTS.md:144`, `--deadline` is a *stop-starting-new-work* bound, not a
  wall-clock guarantee -- each builder rechecks the deadline at its own return point and would
  rather report a late-but-complete answer as `partial`. A 60s default plus an FS-latency-
  proportional tail lands near 75s, at which point the CALLER's own timeout kills the process. That
  is the harness returning nothing, not tg returning an empty payload -- a different bug with a
  different fix.
- At real workspace scale — native Windows, 300k+ files, a separate data point from the WSL caveat above: `tg orient`/`tg search`/`tg inventory --deadline` all bound gracefully (`orient` ~4.9s via scan_limit+centrality; `search` returns partial plus an honest "exceeded timeout" message, exit 124; `inventory --deadline` bounds per-project). Known low-priority edge: a single non-lazy `os.scandir` call in the shared `_iter_repo_files` can still blow `inventory --deadline` on a pathological workspace-union tree (rare; not worth a load-bearing fix).
- Dense find: run `tg install-dense` once per machine (never auto); then `tg find`. Every dense-absent hint across the CLI now leads with `tg install-dense`.
- Ledger remains advisory — see `tensor-grep-ledger`. Claim/release/list now canonicalize to the nearest `.git` ancestor (worktree-aware); the PATH-mismatch footgun from 1.92.1-era dogfood is fixed.
- **The `tg codemap` WSL timeout is likewise DEBUNKED** (`docs/BACKLOG.md:603`, #578: native repro
  **41s whole-repo, `partial=false`, complete**; and `:606` *"codemap '60-180s/no JSON' = WSL 9p
  (native 33s complete)"*). Its slow path is also a DIFFERENT mechanism from `tg agent`'s: codemap
  makes three git subprocess calls (`codemap.py:612`, `:1054`, `:1353`) where `tg agent` makes zero,
  and that exact cost was already root-caused and fixed in `e95abfa` (v1.82.1). Treating the two as
  "one root cause" under-serves codemap and over-serves agent. GPU inventory ≠ acceleration; the WSL bare-shim cross-domain misclassification that produced a bogus `path_not_found` is fixed (v1.93.0).

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
| Whole-repo agent/prepare default deadline reliability | **CLOSED 2026-07-27 -- do not re-open from this row.** Two successive framings here were both overtaken. The "bare agent TIMEOUT" was a debunked WSL-9p artifact (`BACKLOG.md:603`, native ~26s), and the follow-on "unbounded session refresh" framing is ALSO stale: task #304 bounded the staleness-triggered rebuild with the same warm-daemon budget (`session_daemon.py` carries the `Task #304: bound the staleness-triggered rebuild with the SAME budget` comment and passes `deadline_monotonic=monotonic() + WARM_DAEMON_DEFAULT_DEADLINE_SECONDS`). The old citations are deliberately DROPPED rather than re-stamped -- they described a code shape that no longer exists, and re-pointing them would preserve a dead paragraph. If you believe a deadline gap remains, re-derive it from scratch against `origin/main`; do not carry this row forward |
| CUDA-native GPU promotion | Open (adjudicated HOLD, #169 CEO-gated; kernel is brute-force byte-compare, not PFAC) |
| `codemap` on WSL | **Debunked, not open** (`BACKLOG.md:603`/`:606`; native 41s whole-repo complete, 33s in the earlier repro). Root cause was three git subprocess calls, fixed in `e95abfa` @ v1.82.1 |
| Mega-repo auto-narrow + accurate deadline primaries | Partial (`suggested_scope`/`workspace_root_detected` shipped, #684; deadline-primary accuracy still open) |
| Unscoped-search fast-refuse on the default flag-less path | **Shipped** (A9; generic 1500-file ceiling, ~1.7s, all 3 doors) |
| Dynamic-import / blast-radius decoy honesty | **Shipped** (A10/A15; `dynamic_unresolved` excluded from forward/reverse resolution and the blast-radius scoring prefilter) |
| One-call prepare CUJ | **Shipped** (prefer `src/`; `--out FILE` persists the capsule) |
| Packaged dense semantic | **Shipped via `install-dense`** (opt-in, once; every dense-absent hint now leads with it) |
| Ledger → CI / review-bundle bridge | Partial — `review-bundle --receipt`/`--against` CI gate chain shipped (#681); ledger itself stays advisory, not wired into a CI gate |
| Agent accuracy gate | **Shipped** (`tests/eval/test_agent_accuracy.py`, per-task-pinned, 16/16 golden tasks — the loop-4 measurement instrument that surfaced and fixed #250) |
| Symbol-graph language coverage | **REGISTRY 10/10; CALLER-GRAPH 5/10 -- quote the split, never the bare 10/10.** All ten register (`c, cpp, csharp, go, java, javascript, php, python, rust, typescript`; C/C++ via `lang_c.py`/`lang_cpp.py`). But this skill's own hard-stops key on `blast_radius_floor`/`callers_count`, and those need the AST caller path: PARSER-BACKED for `go, javascript, python, rust, typescript`; FOUNDATIONAL-TIER for `c, cpp, csharp, java, php` -- defs+imports are real there, while `tg refs`/`callers`/`blast-radius` fall through to the regex heuristic. **Go belongs in the parser-backed tier, and this line used to say 4/10 with go as "PARTIAL" -- corrected 2026-07-27.** `repo_map._symbol_navigation_descriptor`'s own docstring warns about exactly that mistake: *"this tier currently also includes go, which several PR-comment summaries lump in with the 'foundational-only' languages below -- this undercounts it"*, because `lang_go.go_references_and_calls` is a full tree-sitter extractor (package-alias resolution, node-type-based `ref_kind`), not a regex fallback. **Never hand-count this** -- the product derives it live, so ask the product: `python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"`, which today prints `parser-backed-refs-callers:go-javascript-python-rust-typescript+foundational-defs-imports-only:c-cpp-csharp-java-php`. (The prior "5 of 8 registry specs" denominator was also wrong -- the registry holds 10, and 5 have `references_and_calls is None`. Run it from a CURRENT checkout: a stale tree reports only 5 registered languages and 0 foundational, which looks like a clean answer and is not.) Accepted ceiling: `class MACRO Name` in C++ misparses -- do not re-chase |
| Beat-`rg` cold search | **Closed — honest negative** (#261). Startup is at parity (rg 6.2ms / tg 6.5ms), GPU is dead (3 proofs), `.tg_index` measured NET NEGATIVE (~10x slower), and tg's native walk *is* rg's walk (same `ignore` crate) — so widening it relocates cost, never removes it. The campaign's return was a defect family, not milliseconds. Do not re-measure. |
| LSP proof | Open |
| Trust: incompleteness disclosure across the CLI | Partial (#292). §0 of `docs/CONTRACTS.md` pins the completeness contract; a silent-loss census ratchet guards regressions; 7 disclosure defects fixed this cycle (codemap, checkpoint-undo data-loss, tg scan). **Do NOT cite the trust-benchmark "tie" as a tg result.** Adversarial review 2026-07-26 found the harness's `tg` row runs plain `tg search` (`trust_benchmark.py:79`), which `bootstrap.py:1497` forwards to real rg via `_run_rg_passthrough` whenever no native binary resolves (`bootstrap.py:1269-1277`) — the literal `rg: ` prefix exists nowhere in our source, while tg's own is `tg: {err}`. So that row is ripgrep measured twice. On the `--json` surface that actually matters tg is BEHIND: `rg --json` exits 2 on an unreadable path, `tg --json` exits 0 with a success envelope (#276). |
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
