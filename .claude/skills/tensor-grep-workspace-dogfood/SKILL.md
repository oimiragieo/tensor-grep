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

## Recommended sweep (v1.101.9)

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

## Latest sweep (2026-07-28, tg 1.101.9, gotcontext-saddle)

| Category | Result | Notes |
| --- | --- | --- |
| Symbol ladder / blast / orient / map / route-test / evidence / dogfood | ✅ | `agreement_details` true; evidence `checks.digest_valid` |
| `tg agent` scoped + root `--deadline 90` | ✅ | scoped ~12s; root ~61s rc 0 non-partial (faster vs 1.101.7) |
| lexical + trunc hard-stop | ✅ | lexical OK; trunc now honest **conf 0.72** + ask.required (was 0.9) |
| **`tg prepare`** / `--out` / `--claim` | ✅ | ~6–8s (faster); `agent_id_hint` |
| ledger Slice 1 rollup + Slice 2 | ✅ | list `.` sees subtree claim; find fresh |
| `tg find` without dense | ✅ | BM25; install-dense hint; help mentions optional MaxSim |
| GPU | ⚠️ | `unsupported` / cpu-fallback + explicit “not GPU proof” stderr |
| Multi-project parent unscoped | ✅ | exit 2 refuse |
| Bare `search --json` (no PATH) | ⚠️ | still ~1s exit 1 empty, no refuse stderr |
| Cold doctor daemon | ✅ | autostart hint → warm running |

Artifact: `/tmp/tg-dogfood-11019.json`. Prior same-day 1.101.7: `/tmp/tg-dogfood-11017.json`.

## Trend

| Version | PASS | TIMEOUT | Notable |
| --- | ---: | ---: | --- |
| 1.91.0 | 57 | 2 | prepare + install-dense |
| 1.92.1 | saddle ✅ | — | prepare solid; ledger PATH footgun |
| 1.93.x–1.95.0 | fixes ship | — | ledger rollup, prepare `--out`, GPU probe honesty |
| 1.101.7 | saddle ✅ | — | agreement_details; bare-json PATH footgun |
| **1.101.9** | **saddle ✅** | — | faster prepare/agent; trunc conf honesty; MaxSim in find help |

## Sibling skills

- `tensor-grep`, `tensor-grep-prepare`, `tensor-grep-ledger`, `tensor-grep-find-and-route`, `tensor-grep-gpu`, `tensor-grep-enterprise-agent`, `tensor-grep-multi-project-search`
