---
name: tensor-grep-workspace-dogfood
description: Use when stress-testing tensor-grep against a multi-project workspace — orientation, scoped search, tg find, prepare, route-test, ledger, install-dense, symbol graphs, GPU, evidence, sessions, readiness gates. Not the PyPI release dogfood harness.
---

# tensor-grep workspace dogfood

## Preconditions

```bash
# Prefer an explicit published pin when comparing skills to product:
uvx --from tensor-grep==1.110.14 tg --version
# Bare `uvx --from tensor-grep tg` / a shadowed `C:\Users\...\bin\tg` can report a stale version.
tg doctor --json ROOT
tg devices
```

## Recommended sweep (v1.110.14)

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

## Latest sweep (2026-08-11, tg 1.110.13, Windows uvx)

| Category | Result | Notes |
| --- | --- | --- |
| Core CUJ | ✅ | 21/21 PASS; prepare saddle ~5s; tg `src` --out/--claim ~22s |
| Parent refuse | ✅ | exit 2; `workspace_root_refused` |
| **A90** reserved+flag refuse | ✅ | `edit-ready/--json` → exit 2 `unknown_command`; typo `searhc --help` suggests search |
| A90 bare reserved token | ✅ by design | `tg edit-ready` (no flag) still searches that string |
| M16 scan / M17 `--index` | ✅ | retained from 1.110.12 |
| Language coverage | ✅ | 10/10 parser-backed |
| Phase-2 `edit-ready` product | ❌ | correctly **refused** now; not implemented |

Artifact: `C:\Users\Public\tg-dogfood-111013.json`.

**Version annotation (2026-08-12):** this section was previously stamped "tg 1.110.14", but the
cited artifact does NOT cover 1.110.14 — it is decisively a 1.110.13 sweep: the artifact records
`"version": "1.110.13"` with `"when": "2026-08-11T12:26:56Z"` (BEFORE v1.110.14 was tagged,
2026-08-11 17:32 UTC), and its sweep script `tg-dogfood-111013.js` pins `VER = '1.110.13'`. No
1.110.14 sweep artifact exists in `C:\Users\Public` (only 111010/111012/111013). The header above
was corrected to the version the artifact actually covers; the table rows below are unchanged
because they match the artifact (`counts.PASS = 21`).

## Prior sweep (2026-08-05, tg 1.108.2, gotcontext-saddle)

Kept for trend only. Parent refuse class was still recorded as `scan_limit` in that sweep —
**superseded** by `workspace_root_refused` on 1.110.x (#956).

## Trend

| Version | PASS | TIMEOUT | Notable |
| --- | ---: | ---: | --- |
| 1.101.22 | saddle ✅ | — | bare-`--json` PATH note (stderr) |
| 1.101.31 | saddle ✅ | — | bare-`--json` in-band `scope_note`; MaxSim de-advertised |
| 1.108.2 | saddle ✅ | — | CUJ stable; parent refuse class then=`scan_limit` |
| **1.110.10** | saddle+tg ✅ | — | `workspace_root_refused`; 10/10 parser-backed; prepare~6–14s |
| **1.110.12** | saddle+tg ✅ | — | +M16 scan severity/message; +M17 index root isolation surface; CUJ 21/21 |
| **1.110.13** | **saddle+tg ✅** | — | +A90 unknown-command fail-closed; CUJ 21/21 |

Table ends at 1.110.13 ON PURPOSE: the latest receipt artifact (`tg-dogfood-111013.json`) is a
1.110.13 sweep (see the version annotation above), and no 1.110.14 artifact exists. Do NOT add a
1.110.14 row until a genuine 1.110.14 sweep artifact exists — a trend row without an artifact is a
fabricated receipt.

## Sibling skills

- `tensor-grep`, `tensor-grep-prepare`, `tensor-grep-ledger`, `tensor-grep-find-and-route`, `tensor-grep-gpu`, `tensor-grep-enterprise-agent`, `tensor-grep-multi-project-search`
