---
name: tensor-grep-workspace-dogfood
description: Use when stress-testing tensor-grep against a multi-project workspace — orientation, scoped search, tg find, prepare, route-test, ledger, install-dense, symbol graphs, GPU, evidence, sessions, readiness gates. Not the PyPI release dogfood harness.
---

# tensor-grep workspace dogfood

## Preconditions

```bash
# Prefer an explicit published pin when comparing skills to product:
uvx --from tensor-grep==1.110.10 tg --version
# Bare `uvx --from tensor-grep tg` / a shadowed `C:\Users\...\bin\tg` can report a stale version.
tg doctor --json ROOT
tg devices
```

## Recommended sweep (v1.110.10)

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

## Latest sweep (2026-08-09, tg 1.110.10, Windows uvx)

| Category | Result | Notes |
| --- | --- | --- |
| Symbol ladder / blast / orient / map / route-test / evidence | ✅ | CUJ stable; callers ~7–8s on tg `src` |
| `tg agent` scoped + `--deadline` | ✅ | ~16s on tg `src` |
| **`tg prepare`** / `--out` / `--claim` | ✅ | ~6s saddle whole-repo; ~14s tg `src` + claim |
| ledger list rollup | ✅ | under repo root |
| `tg find` without dense | ✅ | BM25 + install-dense hint; MaxSim still unreachable |
| GPU | ⚠️ | experimental; default loops stay CPU (`tensor-grep-gpu`) |
| Multi-project parent unscoped | ✅ | exit 2; `incomplete_reason_class`/`error.code`=`workspace_root_refused` |
| Parent scoped `--glob`+`--max-depth` | ✅ | does **not** hard-refuse; exit 1 empty-complete is OK |
| Bare `search` text + `--json` | ✅ | `path_was_defaulted` + `scope_note` |
| Language coverage (live JSON) | ✅ | 10/10 parser-backed refs/callers; foundational empty |
| Cold doctor | ✅ | ~17s |
| `edit-ready` / `verify-edit` / `workspace` | ❌ | still absent — and unknown tokens fall through to `tg search` help (exit 0) |

Artifact: `C:\Users\Public\tg-dogfood-111010.json`.

## Prior sweep (2026-08-05, tg 1.108.2, gotcontext-saddle)

Kept for trend only. Parent refuse class was still recorded as `scan_limit` in that sweep —
**superseded** by `workspace_root_refused` on 1.110.x (#956).

## Trend

| Version | PASS | TIMEOUT | Notable |
| --- | ---: | ---: | --- |
| 1.101.22 | saddle ✅ | — | bare-`--json` PATH note (stderr) |
| 1.101.31 | saddle ✅ | — | bare-`--json` in-band `scope_note`; MaxSim de-advertised |
| 1.108.2 | saddle ✅ | — | CUJ stable; parent refuse class then=`scan_limit` |
| **1.110.10** | **saddle+tg ✅** | — | `workspace_root_refused`; 10/10 parser-backed; prepare~6–14s |

## Sibling skills

- `tensor-grep`, `tensor-grep-prepare`, `tensor-grep-ledger`, `tensor-grep-find-and-route`, `tensor-grep-gpu`, `tensor-grep-enterprise-agent`, `tensor-grep-multi-project-search`
