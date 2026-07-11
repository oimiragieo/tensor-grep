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

## Latest sweep results (2026-07-10 evening, tg 1.58.9, `/mnt/c/dev/projects`)

| Category | Pass | Notes |
| --- | --- | --- |
| Diagnostics (`doctor`, `devices`, `ast-info`, `rulesets`) | ✅ | doctor ~18s; launcher native-exe; rust version matches |
| Per-repo inventory | ✅ | `tg inventory tensor-grep` → 864 files |
| Workspace inventory + `--deadline` | ❌ | returns `files=0` with `truncation_cause=deadline` |
| Orient (workspace + saddle) | ✅ | 28s / 2.5s |
| Scoped search (py/js/ts/rust + `--rank`) | ✅ | 0.5–19s across tensor-grep, gotcontext-*, omega-main |
| Symbol (`defs`, `source`) | ✅ | ~13s for `open_session` |
| File deps (`imports` / `importers` abs paths) | ✅ | 29 imports / 1 importer; path-doubling avoided with abs paths |
| Agent loop (`agent`, `context*`, `edit-plan`, `session`) | ✅ | agent → `session_daemon.py`, conf 0.75, 5 validation cmds |
| AST read (`tg run`) | ✅ | ~0.7s |
| `docs-coverage` | ✅ | ~2s |
| Graph scans (`callers`, `blast-radius`, `impact`) | ⚠️ | exit `2` + `partial`; callers found 3 sites in ~17s |
| `tg map` | ⚠️ | 512-file cap → exit 2 incomplete |
| `tg refs` | ❌ | timed out at 45s on `open_session` |
| Unscoped search | ❌ | 60s timeout (exit 124) despite top-level `node_modules` |
| `tg scan` on WSL | ❌ | ast-grep Windows shim exit 127 |
| `classify --json` | ❌ | no `--json` flag — use default format or `--format json` |
| `checkpoint create` whole repo | ❌ | fails on `benchmarks/external_repos/chalk`; scope to `src/` |
| GPU search | ❌ | experimental (`search_ready: false`) |
| Harness-repo agent (`gotcontext-saddle`) | ⚠️ | wrong primary + `tie_requires_confirmation` + 0 validation cmds |

Full TSV: `/tmp/tg-dogfood-v3/report.tsv` (28 PASS / 5 INCOMPLETE / 3 FAIL / 2 TIMEOUT)

## Known workspace pitfalls (2026-07-10, tg 1.58.9)

1. **WSL `/mnt/c/` absolute paths** — native backend may return `path_not_found` for paths that `ls` shows; retry with `cd ROOT` + relative paths.
2. **`--deadline` on graph scans** — usually honored within ~1–2× budget now; still exit `2` + `partial` when truncated.
3. **Workspace-root inventory + short deadline → zero files** — do not trust `totals.files=0` as empty; inventory per repo instead.
4. **`tg map` truncation** — 512-file default cap; map one repo at a time.
5. **`tg importers FILE ROOT`** — use absolute paths (relative pairs can double-resolve).
6. **`tg scan`** — Windows npm ast-grep shim breaks under WSL (exit 127); doctor `available: true` is not proof it runs.
7. **Unscoped search** — still 60s timeout on this workspace; always scope to a repo/path.
8. **`tg classify`** — `FILE_PATH` required; default output is JSON; **no `--json` flag**.
9. **`tg checkpoint create` on huge trees** — can fail on awkward paths under `external_repos`; scope to `src/`.
10. **GPU** — `search_ready: false`; text/AST unaffected.
11. **`tg orient` / `tg agent` without `--ignore`** — skill/vendor trees can rank as central or primary on harness repos; use `--ignore` and honor `ambiguity.status=tie_requires_confirmation`.

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
