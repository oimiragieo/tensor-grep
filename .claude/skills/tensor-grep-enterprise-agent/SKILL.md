---
name: tensor-grep-enterprise-agent
description: Use when designing or evaluating tensor-grep as an enterprise agentic code-intelligence tool — multi-repo workspaces, accuracy gates, audit/compliance loops, MCP/session daemons, and the gap between current tg capabilities and world-class enterprise readiness. Complements tensor-grep-workspace-dogfood (how to stress-test) and tensor-grep-code-audit (how to use tg for pre-edit audits).
---

# tensor-grep for enterprise agents

## What tg already is (shipped, dogfood-proven)

Use these as the default agent toolkit on a real multi-language workspace:

| Job | Command | Notes |
| --- | --- | --- |
| Orient | `tg orient PATH --ignore … --json` | Central files + entry points |
| Ranked text search | `tg search PATTERN PATH --rank --json` | Always scope PATH |
| File deps | `tg imports FILE` / `tg importers FILE ROOT` | Absolute paths; O(1) forward |
| Symbol source | `tg source PATH SYMBOL --json` | Prefer over blind Read loops |
| Pre-edit capsule | `tg agent PATH "task" --json` | Check `ambiguity.status` + confidence |
| Cached loop | `tg session open` → `session context-render` | Avoid re-index tax |
| Impact | `tg callers` / `tg blast-radius` + `--deadline` | Exit 2 + `partial` = incomplete |

## Hard stops for autonomous enterprise agents

Do **not** auto-edit when any of these fire:

1. `ambiguity.status == "tie_requires_confirmation"`
2. `result_incomplete` / `partial: true` / symbol-command exit `2`
3. `confidence.overall` low with language-mismatched `validation_commands`
4. `tg scan` / ast-grep unavailable on the agent host OS
5. Unscoped workspace search (will hang ~60s)

## Enterprise gaps (world_class_readiness = not_claimed)

From `tg dogfood` + 2026-07-10 workspace dogfood (v1.58.9):

| Gap | Why it blocks enterprise |
| --- | --- |
| Missing agent target-selection accuracy gate | No published top-k / MRR / false-primary metrics |
| GPU search experimental | Cannot claim acceleration |
| LSP not proof-backed | `lsp_proof` false even when pyright runs |
| Unscoped multi-root search hangs | Agents on monorepo parents burn timeouts |
| Workspace inventory deadline → 0 files | Misleading empty totals |
| Cross-OS ast-grep | Security rulesets fail under WSL with Windows npm shim |
| No multi-root workspace primitive | Agents must manually fan out per repo |
| Review-bundle / audit chain not in default agent loop | Compliance surface exists but unused |

## Recommended enterprise agent loop

```bash
tg doctor --json REPO                    # launcher + ast_grep + GPU honesty
tg orient REPO --ignore "node_modules/**" --ignore "**/core/skills/**" --json
tg search "intent" REPO --rank --json
tg agent REPO "task" --json              # abort if tie_requires_confirmation
tg imports ABS_FILE --json
tg callers REPO SYMBOL --deadline 15 --json   # abort if exit 2 without retry plan
SID=$(tg session open REPO --json | jq -r .session_id)
tg session context-render "$SID" REPO "task" --json
# after edit: scoped validation_commands only; never invent runners
```

## Sibling skills

- `tensor-grep` — day-to-day usage
- `tensor-grep-workspace-dogfood` — stress-test matrix
- `tensor-grep-code-audit` — pre-edit blast-radius discipline
- `dogfood-the-shipped-artifact` — post-PyPI real-binary proof
