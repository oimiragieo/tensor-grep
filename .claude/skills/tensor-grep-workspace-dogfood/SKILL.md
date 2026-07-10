---
name: tensor-grep-workspace-dogfood
description: Use when stress-testing tensor-grep against a multi-project workspace (monorepo parent, many languages) like a real enterprise agent would — orientation, scoped search, symbol graphs, imports/importers, sessions, and readiness gates. Not the PyPI release dogfood harness (see global dogfood-the-shipped-artifact).
---

# tensor-grep workspace dogfood

Run this when validating `tg` on a **large, multi-language workspace root** (e.g. `C:\dev\projects` with dozens of sibling repos), not a single git checkout.

## Preconditions

```bash
tg --version
tg doctor --json ROOT   # read launcher_kind, GPU search_ready, ast_grep, lsp
```

`tg dogfood --root ROOT` is a **fast package-import gate** only when `ROOT` is not a tensor-grep source tree — it does **not** substitute for feature dogfood. For release proof, use global `dogfood-the-shipped-artifact` + `scripts/dogfood/`.

## Recommended sweep (copy-paste sequence)

Scope every search and graph query. Unscoped `tg search PATTERN` against workspace parents is refused or slow by design.

```bash
ROOT=/path/to/workspace

# 1. Cheap manifest (floor counts; --deadline may be exceeded on huge trees)
tg inventory "$ROOT" --deadline 30 --json

# 2. Orientation (use --ignore for vendor/skill trees that crowd centrality)
tg orient "$ROOT" --ignore "node_modules/**" --ignore "**/core/skills/**" --json

# 3. Per-repo deep tests (pick 2–3 representative repos: Python CLI, TS app, Rust)
tg map "$ROOT/tensor-grep" --json
tg search "session daemon" "$ROOT/tensor-grep" --rank --json
tg imports "$ROOT/tensor-grep/src/tensor_grep/cli/main.py" --json
tg importers "$ROOT/tensor-grep/src/tensor_grep/cli/main.py" "$ROOT/tensor-grep" --json

# 4. Symbol graph (always narrow PATH; treat exit 2 + partial as incomplete)
tg callers "$ROOT/tensor-grep" SYMBOL --deadline 15 --json
tg blast-radius "$ROOT/tensor-grep" SYMBOL --deadline 20 --json

# 5. Agent capsule + session cache
tg agent "$ROOT/tensor-grep" "representative task" --json
SID=$(tg session open "$ROOT/tensor-grep" --json | jq -r .session_id)
tg session context-render "$SID" "$ROOT/tensor-grep" "query" --json

# 6. Multi-language text search (scope path + type/glob)
tg search "def main" "$ROOT/some-python-repo" --type py --max-count 5
tg search "export" "$ROOT/some-js-repo/src" --glob "*.js" --max-count 5
```

## What to record per command

| Field | Why |
| --- | --- |
| exit code | Symbol commands use 0/1/2 agent contract (2 = incomplete) |
| wall time | Compare against `--deadline`; flag overruns |
| `result_incomplete` / `partial` | Hard stop for autonomous edits |
| `routing_backend` | Native vs sidecar vs rg passthrough |
| stderr tail | Refusal messages, timeout warnings, SyntaxWarnings |

## Latest sweep results (2026-07-10, tg 1.54.6, `/mnt/c/dev/projects`)

| Category | Pass | Notes |
| --- | --- | --- |
| Diagnostics (`doctor`, `devices`, `ast-info`, `rulesets`) | ✅ | doctor ~15s |
| Workspace inventory/orient | ✅ | inventory ~30s; orient ~33s |
| Scoped search (py/js/rust + `--rank`) | ✅ | sub-second to 10s |
| Symbol (`defs`, `source`) | ✅ | ~9s |
| File deps (`imports`) | ✅ | ~2s, 31 edges on `main.py` |
| Agent loop (`agent`, `context*`, `edit-plan`, `session`) | ✅ | agent hit `session_daemon.py`, confidence 0.75, 4 validation cmds |
| AST read (`tg run`) | ✅ | ~0.6s |
| `docs-coverage` | ✅ | ~2.5s |
| Graph scans (`callers`, `blast-radius`, `impact`) | ⚠️ | exit `2` + incomplete for generic symbol `main`; deadline improved |
| `tg map` at workspace scale | ⚠️ | 512-file cap; use per-repo |
| Unscoped search | ❌ | 60s timeout (exit 124) — scope paths |
| `tg scan` on WSL | ❌ | ast-grep Windows shim exit 127 |
| `classify` stdin | ❌ | file-path only |
| GPU search | ❌ | experimental (`search_ready: false`) |

Full TSV: `/tmp/tg-dogfood-v2/report.tsv`

## Known workspace pitfalls (2026-07-10 dogfood, tg 1.54.6)

1. **WSL `/mnt/c/` absolute paths** — native backend may return `path_not_found` for paths that `ls` shows; retry with `cd ROOT` + relative paths.
2. **`--deadline` on graph scans** — improved since 2026-07-09 (callers `--deadline 10` now ~13–22s vs ~6 min on v1.54.0) but still may exceed the requested budget; treat exit `2` + `partial`/`result_incomplete` as incomplete, not absent.
3. **`tg map` truncation** — workspace root and large single repos hit the 512-file cap (`possibly_truncated: true`); map one repo at a time for full symbol graphs.
4. **`tg importers FILE ROOT` path doubling** — relative `FILE` + relative `ROOT` from a parent cwd can resolve to `ROOT/ROOT/FILE`; use absolute paths or `cd ROOT` first.
5. **`tg scan`** — requires a working `ast-grep` binary on the **same OS** as `tg`; Windows npm shims break under WSL (exit 127).
6. **Unscoped search** — still hits 60s `TG_RG_TIMEOUT_SECONDS` on this workspace (exit 124); always scope to a repo/path.
7. **GPU** — `doctor` reports `search_ready: false`; text/AST paths are unaffected.
8. **`tg orient` without `--ignore`** — skill/vendor trees can rank as "central" on harness repos; use the same `--ignore` globs as `orient`/`agent`.

## Pass / fail rubric

**PASS (agent-ready for this workspace)** when:
- Scoped `search --rank`, `imports`, `defs`/`source`, `agent`, and `session context-render` complete with exit 0 on representative repos
- `doctor` shows consistent launcher version and no foreign `tg` shadowing
- Incomplete graph results are **detectable** (exit 2 + JSON flags), not silent

**FAIL / improve** when:
- Deadline flags are set but wall time is 10× the deadline
- Absolute paths fail while relative paths work
- Agent primary target lands in vendor/skill trees without `--ignore`
- `validation_commands` empty or misaligned with primary target language

## Sibling skills

- `tensor-grep-run-and-operate` — exact CLI syntax and exit-code contract
- `tensor-grep-diagnostics-and-tooling` — interpreting `doctor` / `result_incomplete`
- `tensor-grep-large-repo-scale-campaign` — campaign history for scale limits
- `dogfood-the-shipped-artifact` (global) — post-release PyPI binary proof
