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

## Known workspace pitfalls (2026-07-09 dogfood)

1. **WSL `/mnt/c/` absolute paths** — native backend may return `path_not_found` for paths that `ls` shows; retry with `cd ROOT` + relative paths.
2. **`--deadline` overruns** — `callers`/`inventory` can exceed the deadline budget on ~800-file Python repos; do not treat deadline as SLA.
3. **`tg map` on workspace root** — hits `--max-repo-files` (default 512) and exits `2`; map a single repo instead.
4. **`tg scan`** — requires a working `ast-grep` binary on the **same OS** as `tg`; Windows npm shims break under WSL.
5. **`tg orient` without `--ignore`** — skill/vendor trees can rank as "central" on harness repos; use the same `--ignore` globs as `orient`/`agent`.
6. **GPU** — `doctor` reports `search_ready: false`; text/AST paths are unaffected.

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
