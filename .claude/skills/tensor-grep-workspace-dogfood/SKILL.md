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

## Recommended sweep (v1.101.31)

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

## Latest sweep (2026-08-02, tg 1.101.31, gotcontext-saddle)

| Category | Result | Notes |
| --- | --- | --- |
| Symbol ladder / blast / orient / map / route-test / evidence / dogfood | ✅ | `agreement_details`; evidence `checks.digest_valid` |
| `tg agent` scoped + root `--deadline 90` | ✅ | scoped ~8s; root ~55s rc 0 non-partial |
| lexical + trunc hard-stop | ✅ | trunc conf **0.72** + ask.required |
| **`tg prepare`** / `--out` / `--claim` | ✅ | ~8–13s; strong anonymous `agent_id_hint` |
| ledger Slice 1 + Slice 2 find | ✅ | list + find rollup under repo root |
| `tg find` without dense | ✅ | BM25 + install-dense hint; **MaxSim NOT advertised** (help) |
| GPU | ⚠️ | `unsupported` / cpu-fallback + not-proof stderr |
| Multi-project parent unscoped | ✅ | exit 2 + JSON `incomplete_reason` |
| Bare `search` text + `--json` | ✅ | PATH note on stderr; **`--json` also has `path_was_defaulted` + `scope_note`** (see condition below) |
| Cold doctor daemon | ✅ | autostart hint → warm running |

Artifact: `/tmp/tg-dogfood-110131.json`.

**Condition on `path_was_defaulted` / `scope_note` (added 2026-08-02, NOT re-verified).** These are
stamped only when `result.path_was_defaulted` is true -- `json_fmt.py`, `grep -n "path_was_defaulted"
src/tensor_grep/cli/formatters/json_fmt.py`, which is explicitly additive ("absent on an
explicitly-scoped search, so an existing consumer's payload is byte-identical"). Shipped since
v1.101.26; enumerated by EMITTER in `tests/unit/test_scope_note_covers_every_json_emitter.py`.

A 2026-08-02 re-probe on the installed v1.102.0 did NOT reproduce either field -- in a small root the
search completed (exit 0) with neither key, and in the repo root the unscoped-scan guard refused
(exit 2) so the normal emitter never ran. **That is an unreproduced row, not a refuted one:** neither
probe established which emitter it reached (the native front door has its own -- 20 hits in
`rust_core/src/main.rs`), so it cannot discriminate. Left standing and labelled rather than deleted
or trusted. Re-verify by asserting the emitter, not the exit code.


## Trend

| Version | PASS | TIMEOUT | Notable |
| --- | ---: | ---: | --- |
| 1.101.19 | saddle ✅ | — | bare-text PATH stderr note |
| 1.101.22 | saddle ✅ | — | bare-`--json` PATH note (stderr); stronger anon hint |
| **1.101.31** | **saddle ✅** | — | bare-`--json` **in-band** `scope_note`; MaxSim de-advertised |

## Sibling skills

- `tensor-grep`, `tensor-grep-prepare`, `tensor-grep-ledger`, `tensor-grep-find-and-route`, `tensor-grep-gpu`, `tensor-grep-enterprise-agent`, `tensor-grep-multi-project-search`
