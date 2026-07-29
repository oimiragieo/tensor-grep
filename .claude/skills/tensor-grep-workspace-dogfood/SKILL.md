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

## Recommended sweep (v1.101.17)

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

## Latest sweep (2026-07-28, tg 1.101.17, gotcontext-saddle)

| Category | Result | Notes |
| --- | --- | --- |
| Symbol ladder / blast / orient / map / route-test / evidence / dogfood | ✅ | `agreement_details`; evidence `checks.digest_valid` |
| `tg agent` scoped + root `--deadline 90` | ✅ | scoped ~7s; root ~**47s** (faster vs 1.101.9 ~61s) |
| lexical + trunc hard-stop | ✅ | trunc conf **0.72** + ask.required |
| **`tg prepare`** / `--out` / `--claim` | ✅ | ~7–8s; env `TG_LEDGER_AGENT_ID` attributed |
| ledger Slice 1 + Slice 2 find | ✅ | list rollup; **`find .` sees `record core/hooks`** (skill was stale) |
| `tg find` without dense | ✅ | BM25 + install-dense hint |
| GPU | ⚠️ | `unsupported` / cpu-fallback + not-proof stderr |
| Multi-project parent unscoped | ✅ | exit 2 + JSON `incomplete_reason` |
| Bare `search` / `search --json` (no PATH) | ⚠️ | ~2s exit 1 empty, **no** refuse stderr (text also empty now) |
| Cold doctor daemon | ✅ | autostart hint → warm running |

Artifact: `/tmp/tg-dogfood-110117.json`.

## Trend

| Version | PASS | TIMEOUT | Notable |
| --- | ---: | ---: | --- |
| 1.101.7 | saddle ✅ | — | agreement_details; bare-json PATH footgun |
| 1.101.9 | saddle ✅ | — | faster prepare/agent; trunc conf honesty |
| **1.101.17** | **saddle ✅** | — | Slice 2 find repo-visible; agent root ~47s; bare text also silent-empty |

## Sibling skills

- `tensor-grep`, `tensor-grep-prepare`, `tensor-grep-ledger`, `tensor-grep-find-and-route`, `tensor-grep-gpu`, `tensor-grep-enterprise-agent`, `tensor-grep-multi-project-search`
