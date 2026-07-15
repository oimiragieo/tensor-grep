---
name: tensor-grep-enterprise-agent
description: Use when designing or evaluating tensor-grep as an enterprise agentic code-intelligence tool — PATH narrowing for complete graphs, EvidenceReceipts, codemap, multi-repo workspaces, accuracy gates, and world-class readiness gaps.
---

# tensor-grep for enterprise agents

Verified against **tg 1.71.1** (2026-07-13 workspace dogfood on `/mnt/c/dev/projects`).

## Guidance (updated)

- **Prefer `REPO/src` (or package root) for `tg agent` latency.** Whole-repo agent works NATIVELY (~26s on tensor-grep, exit 0, valid capsule); the 75s WSL `/mnt/c` timeout is a 9p-latency artifact (reproduce natively before calling it a regression), NOT a v1.71.1 native regression.
- **For exhaustive callers, use `REPO/src`.** Root often returns `partial` with empty callers and exit 2.
- Workspace `tg orient .` works (~53s) but per-repo orient is faster (~24s).
- Multi-project workspace search is **refused in ~1s** unless scoped (`--glob` / `--type` / `--max-depth`) or `--allow-broad-generated-scan`.
- Built-in `tg scan --ruleset` works again on WSL; still check stderr for skipped-path warnings.

## Shipped toolkit

| Job | Command |
| --- | --- |
| Orient | `tg orient REPO --ignore … --json` |
| Ranked search | `tg search PATTERN REPO --rank --json` |
| Scoped workspace search | `tg search PATTERN . --glob "*.py" --max-depth N --json` |
| File deps | `tg imports` / `tg importers` (absolute paths) |
| Agent capsule | `tg agent REPO/src "task" --json` |
| Callers / blast | `tg callers REPO/src SYMBOL --deadline 15 --json` |
| Evidence | `tg evidence emit REPO --capsule … --json` |
| Session cache | `tg session open` → `context-render` |
| Ruleset scan | `tg scan --ruleset auth-safe --path REPO/src --json` |
| Browsable map | `tg codemap REPO --out /tmp/code-map` (slow; optional) |

## Hard stops

1. `ambiguity.status == tie_requires_confirmation`
2. `partial` / `result_incomplete` / exit `2` on graph cmds (incomplete ≠ absent)
3. Unscoped workspace search (now fail-fast refuse — still a hard stop unless scoped/opt-in)
4. Whole-repo / mega-tree `tg agent` timeouts — narrow PATH before raising budgets
5. Treat `tg scan` path-skip warnings as coverage loss

## Enterprise gaps (`world_class_readiness = not_claimed`)

| Gap | 1.71.1 status |
| --- | --- |
| Unscoped multi-root refuse | **Fixed** (fast exit 2 + remediation text) |
| Cross-OS / WSL `scan` runnable | **Mostly fixed** (PASS; residual path-skip warnings) |
| Whole-repo agent (native) | **OK** (~26s on tensor-grep, exit 0); WSL `/mnt/c` amplifies to 75s = 9p artifact, not a native regression |
| Large JS/TS agent trees | Open — `agent-studio` slow on WSL; needs NATIVE repro before claiming a real timeout |
| Complete callers at repo root | Still prefer `src/` |
| `codemap` agent-loop-safe | **Fixed** (#153 deadline; native ~41s whole-repo, bounded/partial) |
| Agent accuracy gate (top-k / MRR / false-primary) | Missing |
| GPU promotion / LSP proof | Experimental / not claimed -- GPU Phase-0 SHIPPED v1.75.0-v1.75.4 (native assets locally correctness-proven, gated off the public release behind `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE`), but no speed crossover is proven vs `rg`/`tg_cpu` and `public-gpu-proof.yml` remains unmet (`docs/gpu_crossover.md`) |
| Cold text search vs `rg` claims | `rg` remains baseline |

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
