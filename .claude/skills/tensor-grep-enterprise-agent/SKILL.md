---
name: tensor-grep-enterprise-agent
description: Use when designing or evaluating tensor-grep as an enterprise agentic code-intelligence tool — PATH narrowing for complete graphs, EvidenceReceipts, codemap, multi-repo workspaces, accuracy gates, and world-class readiness gaps.
---

# tensor-grep for enterprise agents

Verified against **tg 1.69.3** (2026-07-13 workspace dogfood).

## Guidance (updated)

- **Whole-repo `tg agent` works again** (~60s on tensor-grep). Prefer `REPO/src` (~13s) for latency.
- **For exhaustive callers, use `REPO/src`** — root often returns `partial` with empty callers.
- Workspace `tg orient .` works (~56s) but per-repo orient is faster.

## Shipped toolkit

| Job | Command |
| --- | --- |
| Orient | `tg orient REPO --ignore … --json` |
| Ranked search | `tg search PATTERN REPO --rank --json` |
| File deps | `tg imports` / `tg importers` (absolute paths) |
| Agent capsule | `tg agent REPO/src "task" --json` |
| Callers / blast | `tg callers REPO/src SYMBOL --deadline 15 --json` |
| Evidence | `tg evidence emit REPO --capsule … --json` |
| Session cache | `tg session open` → `context-render` |
| Browsable map | `tg codemap REPO --out /tmp/code-map` (slow; optional) |

## Hard stops

1. `ambiguity.status == tie_requires_confirmation`
2. `partial` / `result_incomplete` / exit `2` on graph cmds
3. Unscoped workspace search
4. `tg scan` unavailable on host OS

## Enterprise gaps (`world_class_readiness = not_claimed`)

| Gap | 1.69.3 status |
| --- | --- |
| Whole-repo agent hang | **Fixed** (slow but OK) |
| Complete callers at repo root | Still prefer `src/` |
| Unscoped multi-root refuse | Open |
| Cross-OS ast-grep | Open |
| `codemap` production-ready | Open (timeouts) |
| Agent accuracy gate | Missing |
| GPU / LSP proof | Experimental |

## Recommended loop

```bash
tg doctor --json REPO
tg orient REPO --ignore "node_modules/**" --json
tg search "intent" REPO --rank --json
tg agent REPO/src "task" --json > /tmp/capsule.json
tg evidence emit REPO --capsule /tmp/capsule.json --query "task" --json --agent-id "$AGENT_ID"
tg callers REPO/src SYMBOL --deadline 15 --json
SID=$(tg session open REPO --json | jq -r .session_id)
tg session context-render "$SID" REPO "task" --json
```
