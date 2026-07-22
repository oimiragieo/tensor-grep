---
name: tensor-grep-workspace-dogfood
description: Use when stress-testing tensor-grep against a multi-project workspace — orientation, scoped search, tg find, prepare, route-test, ledger, install-dense, symbol graphs, GPU, evidence, sessions, readiness gates. Not the PyPI release dogfood harness.
---

# tensor-grep workspace dogfood

## Preconditions

```bash
tg --version
tg doctor --json ROOT
tg devices
```

## Recommended sweep (v1.93.2)

```bash
cd /path/to/workspace
tg calibrate
tg search PATTERN tensor-grep/src --type py --gpu-device-ids 0 --json
tg agent tensor-grep/src "task" --gpu-device-ids 0 --gpu-timeout-s 15 --json | jq .gpu_acceleration

tg inventory tensor-grep --json
tg orient tensor-grep --ignore "node_modules/**" --json
tg search TODO . --glob "*.py" --max-depth 3 --json
tg find "session daemon timeout" tensor-grep/src --deadline 20 --json
# Prefer prepare over the multi-step agent loop for edit readiness:
tg prepare tensor-grep/src "task" --json
tg prepare tensor-grep/src "task" --claim --json
tg prepare tensor-grep/src "task" --out /tmp/capsule.json --json   # persist for evidence emit --capsule
tg prepare tensor-grep "task" --deadline 20 --json   # expect partial on whole-repo
tg route-test tensor-grep/src "task" --json
tg agent tensor-grep "task" --deadline 20 --json     # still flaky without explicit deadline
tg evidence emit tensor-grep --capsule /tmp/capsule.json --query "task" --json --agent-id dogfood > /tmp/receipt.json
tg ledger claim|record|find|release …                # see tensor-grep-ledger
tg install-dense --json                              # once; then re-try tg find
tg agent agent-studio/.claude/lib/routing "task" --json
tg dogfood --root . --output /tmp/dogfood-ws.json
```

## Latest sweep (2026-07-22, tg 1.92.1, gotcontext-saddle) — historical; 4 rows fixed since, not re-run as one workspace sweep

| Category | Result | Notes |
| --- | --- | --- |
| Symbol ladder / imports / orient / map / route-test / evidence | ✅ | route agreement=true; trunc hard-stop exit 2 |
| `tg agent` scoped + root `--deadline 90` | ✅ | root ~50s rc 0 non-partial (improved vs tight deadline) |
| **`tg prepare`** | ✅ | ~8–9s; blast_radius_floor; `--claim` submits |
| ledger claim/list/record/find/release | ⚠️→**fixed v1.93.0 (A13, #706)** | was: **list PATH must match claim PATH**; now canonicalizes to the nearest `.git` ancestor, `list [PATH]` rolls scope UP — the footgun is closed for Slice 1 (claim/release/list); Slice 2 (record/find) stays literal-path-rooted |
| `tg find` without dense | ✅ | BM25 + `rank_fallback_reason` (message now leads with `tg install-dense`, A12(a)) |
| GPU | ⚠️→**partially fixed v1.93.0 (A11, #704)** | was: CPU front door; probe `failed_probe_path` on WSL — that specific bare-shim cross-domain misclassification is fixed (honest `unsupported`/`gpu-auto-fallback-cpu` now); GPU search itself is still CPU-fallback on a non-CUDA build, unchanged |
| Unscoped search | ⚠️→**fixed v1.92.3 (A9, #702)** | was: timeout-first / empty under short TG_* timeout, not a fast refuse; now a generic `IMPLICIT_SEARCH_WALK_FILE_CEILING=1500` fast-refuse fires in ~1.7s on any defaulted PATH across all 3 doors |
| Cold `doctor` session_daemon | ⚠️ | often `running: false` until warm traffic; now additively reports `autostart` (A12(b)) explaining why |

Prior workspace (2026-07-21, 1.91.0): **57 PASS / 8 INCOMPLETE / 2 TIMEOUT / 1 FAIL**
(`/tmp/tg-dogfood-v21/report.tsv`). Suite artifact this run: `/tmp/tg-dogfood-1921.json`. No fresh
whole-workspace re-run has been recorded past v1.92.1 as of v1.93.2 — the four fixed rows above are
individually verified against their shipping PRs' own gate-run/dogfood evidence (docs/BACKLOG.md), not
a repeat of this sweep.

## Trend

| Version | PASS | TIMEOUT | Notable |
| --- | ---: | ---: | --- |
| 1.81.18 | 46 | 2 | deadline symbol flaky |
| 1.83.0 | 52 | 2 | ledger ships |
| 1.91.0 | 57 | 2 | prepare + install-dense |
| 1.92.1 | saddle ✅ | — | prepare solid; ledger PATH-scope footgun documented |
| **1.93.2** | not re-swept | — | ledger PATH fix (A13), unscoped fast-refuse (A9), WSL GPU-probe fix (A11), dynamic-import honesty (A10/A15), install-dense/doctor-autostart/prepare-`--out` UX batch (A12) all shipped since 1.92.1 — see the row-by-row fixes above; a fresh whole-workspace PASS/TIMEOUT count is not yet recorded |

## Sibling skills

- `tensor-grep`, `tensor-grep-prepare`, `tensor-grep-ledger`, `tensor-grep-find-and-route`, `tensor-grep-gpu`, `tensor-grep-enterprise-agent`, `tensor-grep-multi-project-search`
