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

## Recommended sweep (v1.71.1)

```bash
cd /path/to/workspace
tg inventory tensor-grep --json
tg orient tensor-grep --ignore "node_modules/**" --json
tg search "session daemon" tensor-grep --rank --json
# workspace-root search is refused unless scoped:
tg search TODO . --glob "*.py" --max-depth 3 --json
tg imports "$PWD/tensor-grep/src/tensor_grep/cli/main.py" --json
tg callers tensor-grep/src SYMBOL --deadline 15 --json   # prefer src for complete
tg agent tensor-grep/src "task" --json > /tmp/capsule.json  # do NOT default to repo root
tg evidence emit tensor-grep --capsule /tmp/capsule.json --query "task" --json --agent-id dogfood
tg scan --ruleset auth-safe --path tensor-grep/src --json
tg dogfood --root . --output /tmp/dogfood-ws.json
```

## Latest sweep (2026-07-13, tg 1.71.1, `/mnt/c/dev/projects`)

| Category | Result | Notes |
| --- | --- | --- |
| Diagnostics | ✅ | doctor ~17.5s; 2 GPUs detected |
| Multi-lang search | ✅ | py/js/ts/rust + `--rank`; omega-main inventory/search OK |
| Workspace orient | ✅ | ~53s |
| `tg agent` src | ✅ | ~24s, primary + validation_commands |
| `tg agent` root | WSL-slow | ~26s NATIVE (OK, exit 0); 75s on WSL `/mnt/c` = 9p artifact, not a regression |
| `tg agent` agent-studio | ❌ | TIMEOUT 60s |
| callers src | ✅ | complete 3 callers ~6s |
| callers root | ⚠️ | partial / 0 callers — narrow PATH |
| blast/impact/defs/source/context*/evidence/session | ✅ | |
| Unscoped workspace search | ✅** | **fixed** — refuse in ~1.1s (exit 2), was 60s hang |
| `tg scan` WSL | ✅** | **fixed** — exit 0 (~1.4s); may warn on WSL path shim |
| `tg codemap` | ❌ | TIMEOUT 90s |
| inventory/map/importers deadlines | ⚠️ | incomplete floors |

TSV: `/tmp/tg-dogfood-v8/report.tsv` — **37 PASS / 5 INCOMPLETE / 3 TIMEOUT** (no FAIL)

## Trend vs prior dogfoods

| Version | PASS | TIMEOUT | Notable |
| --- | ---: | ---: | --- |
| 1.63.2 | 27 | 10 | agent/graph hangs |
| 1.68.1 | 29 | 6 | agent root still hangs |
| 1.69.3 | 37 | 2 | agent root + workspace orient fixed; unscoped+scan still bad |
| **1.71.1** | **37** | **3** | unscoped refuse + scan fixed; the 3 "timeouts" are WSL `/mnt/c` 9p artifacts (native agent ~26s) |

## Pitfalls

1. Prefer `REPO/src` for complete callers and reliable agent capsules (root agent flaky again).
2. Unscoped multi-project search now fails fast — scope or opt in explicitly; do not treat exit 2 as “zero matches”.
3. `codemap` not agent-loop ready.
4. `tg scan` may PASS while still skipping paths under WSL — read stderr warnings.
5. Honor `tie_requires_confirmation` / `partial` / `result_incomplete` hard stops.
6. Harness/noisy repos can pick weak primaries — corroborate with `callers` + search.

## Sibling skills

- `tensor-grep`, `tensor-grep-enterprise-agent`, `tensor-grep-code-audit`, `dogfood-the-shipped-artifact`
