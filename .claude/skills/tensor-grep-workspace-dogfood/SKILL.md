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

## Recommended sweep (v1.101.7)

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

## Latest sweep (2026-07-28, tg 1.101.7, gotcontext-saddle)

| Category | Result | Notes |
| --- | --- | --- |
| Symbol ladder / blast / orient / map / route-test / evidence / dogfood | ✅ | route `agreement: true` via **`agreement_details`**; trunc hard-stop exit 2 |
| `tg agent` scoped + root `--deadline 90` | ✅ | scoped ~12s; root ~76s rc 0 non-partial |
| lexical camelCase → snake | ✅ | `readBlockEnabled` → `read_block_enabled` |
| **`tg prepare`** / `--out` / `--claim` | ✅ | ~13s; `--out` persists; `agent_id_hint` when anonymous |
| ledger Slice 1 rollup + Slice 2 record/find | ✅ | `list .` sees `claim core/hooks`; find fresh exit 0 |
| `tg find` without dense | ✅ | BM25; fallback leads with `` `tg install-dense` `` |
| GPU | ⚠️ | honest `unsupported` / `gpu-auto-fallback-cpu`; calibrate exit 2 (no CUDA) |
| Multi-project parent unscoped | ✅ | exit 2 refuse + remediation |
| Single-repo bare `search --json` (no PATH) | ⚠️ | ~2s exit 1 empty, **no** refuse stderr — always pass PATH |
| Cold doctor daemon | ✅ | `autostart: on-first-use…`; warm → `running: true` |

Artifact: `/tmp/tg-dogfood-11017.json`.

## Trend

| Version | PASS | TIMEOUT | Notable |
| --- | ---: | ---: | --- |
| 1.81.18 | 46 | 2 | deadline symbol flaky |
| 1.83.0 | 52 | 2 | ledger ships |
| 1.91.0 | 57 | 2 | prepare + install-dense |
| 1.92.1 | saddle ✅ | — | prepare solid; ledger PATH footgun documented |
| 1.93.x–1.95.0 | fixes ship | — | ledger rollup, install-dense hint, prepare `--out`, GPU probe honesty |
| **1.101.7** | **saddle ✅** | — | live reconfirm; route-test `agreement_details`; bare-json PATH footgun remains |

## Sibling skills

- `tensor-grep`, `tensor-grep-prepare`, `tensor-grep-ledger`, `tensor-grep-find-and-route`, `tensor-grep-gpu`, `tensor-grep-enterprise-agent`, `tensor-grep-multi-project-search`
