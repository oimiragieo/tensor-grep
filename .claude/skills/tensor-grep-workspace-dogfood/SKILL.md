---
name: tensor-grep-workspace-dogfood
description: Use when stress-testing tensor-grep against a multi-project workspace (monorepo parent, many languages) — orientation, scoped search, symbol graphs, imports/importers, codemap, evidence receipts, sessions, and readiness gates. Not the PyPI release dogfood harness (see global dogfood-the-shipped-artifact).
---

# tensor-grep workspace dogfood

## Preconditions

```bash
tg --version
tg doctor --json ROOT
```

## Recommended sweep (v1.69.3)

```bash
cd /path/to/workspace
tg inventory tensor-grep --json
tg orient tensor-grep --ignore "node_modules/**" --json
tg search "session daemon" tensor-grep --rank --json
tg imports "$PWD/tensor-grep/src/tensor_grep/cli/main.py" --json
tg callers tensor-grep/src SYMBOL --deadline 15 --json   # prefer src for complete
tg agent tensor-grep/src "task" --json > /tmp/capsule.json  # ~4× faster than root
tg evidence emit tensor-grep --capsule /tmp/capsule.json --query "task" --json --agent-id dogfood
```

## Latest sweep (2026-07-13, tg 1.69.3, `/mnt/c/dev/projects`)

| Category | Result | Notes |
| --- | --- | --- |
| Diagnostics | ✅ | doctor ~14s |
| Multi-lang search | ✅ | py/js/ts/rust + `--rank` |
| Workspace orient | ✅ | **fixed** — ~56s (was TIMEOUT on 1.68.1) |
| `tg agent` root | ✅ | **fixed** — ~60s, correct primary |
| `tg agent` src | ✅ | ~13s, same quality |
| callers src | ✅ | **complete** 3 callers ~5s |
| callers root | ⚠️ | partial / 0 callers — narrow PATH |
| blast/impact/defs/source/context* | ✅ | 3–11s |
| evidence/session/checkpoint/classify | ✅ | |
| `tg codemap` | ❌ | TIMEOUT 90s |
| Unscoped search | ❌ | 60s timeout |
| `tg scan` WSL | ❌ | ast-grep shim 127 |

TSV: `/tmp/tg-dogfood-v7/report.tsv` — **37 PASS / 4 INCOMPLETE / 1 FAIL / 2 TIMEOUT** (best score in this series)

## Trend vs prior dogfoods

| Version | PASS | TIMEOUT | Notable |
| --- | ---: | ---: | --- |
| 1.63.2 | 27 | 10 | agent/graph hangs |
| 1.68.1 | 29 | 6 | agent root still hangs |
| **1.69.3** | **37** | **2** | agent root + workspace orient fixed |

## Pitfalls

1. Prefer `REPO/src` for complete callers and faster agent.
2. Unscoped search still burns 60s — always scope.
3. `codemap` not agent-loop ready.
4. WSL `scan` broken (Windows npm ast-grep).
5. Honor `tie_requires_confirmation` on harness repos.

## Sibling skills

- `tensor-grep`, `tensor-grep-enterprise-agent`, `tensor-grep-code-audit`, `dogfood-the-shipped-artifact`
