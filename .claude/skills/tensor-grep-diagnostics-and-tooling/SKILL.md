---
name: tensor-grep-diagnostics-and-tooling
description: Use when you need to MEASURE tensor-grep's health instead of eyeballing it -- interpreting a `tg doctor --json` field, reading a PASS/FAIL/SKIP from `tg dogfood` or `scripts/agent_readiness.py`, deciding which `benchmarks/*.py` script answers a given question, or explaining what a diagnostic field actually proves (and does not prove). Not for CLI syntax (tensor-grep-run-and-operate), fixing a found bug (tensor-grep-debugging-playbook), or constructing a claim-quality benchmark artifact (tensor-grep-benchmark-and-proof-toolkit).
---

# tensor-grep Diagnostics and Tooling

How to **measure, not eyeball**, whether a tensor-grep (`tg`) install or a repo checkout is
healthy. All facts below are verified against the repo at **v1.17.25 (2026-07-02)** by reading
the cited files — re-verify with the commands in "Provenance and maintenance" if you suspect drift.

## When to use this skill (and when to use a sibling instead)

Use this skill when you are about to:

- Read a `tg doctor --json` payload and need to know which of its ~40 fields are load-bearing.
- Run `tg dogfood` or `scripts/agent_readiness.py` before a push and need to interpret a
  `FAILED`/`SKIPPED` check name.
- Decide which `benchmarks/*.py` script answers "did my change make X faster/slower".
- Explain to someone (or yourself) what a green gate actually proves, so you don't overclaim.

Use a **sibling** instead when the question is really about:

| You need... | Use instead |
|---|---|
| Exact CLI syntax for `tg orient` / `search --rank` / `session` / `checkpoint` / `mcp` / etc. | `tensor-grep-run-and-operate` |
| Investigating a hang, wrong/silent-empty result, red CI, or a release that didn't publish | `tensor-grep-debugging-playbook` |
| Whether a "novel" fix idea was already tried and reverted | `tensor-grep-failure-archaeology` |
| Constructing a claim-quality benchmark artifact, the noise-floor rule, fair-baseline rule | `tensor-grep-benchmark-and-proof-toolkit` |
| The 4 command / 2 flag registration sites, the front door, the backend fail-closed contract | `tensor-grep-architecture-contract` |
| Every `TG_*`/`TENSOR_GREP_*` env var and its default/guard | `tensor-grep-config-and-flags` |
| Rebuilding the Rust extension/binary, toolchain setup, `maturin develop` | `tensor-grep-build-and-env` |
| PR/release process gates (one-merge-per-tick, draft-PR-only, etc.) | `tensor-grep-change-control` |

**This skill is the interpretation layer** — it explains what a signal *means*. It does not tell
you the CLI syntax to produce other output (`tensor-grep-run-and-operate`), how to fix what a
signal reveals (`tensor-grep-debugging-playbook`), or how to build a defensible speed claim from a
benchmark run (`tensor-grep-benchmark-and-proof-toolkit`).

## The four measurement tools, at a glance

| Tool | What it measures | Speed | Mutates repo? |
|---|---|---|---|
| `tg doctor --json --no-lsp` | One point-in-time snapshot: version parity, launcher routing, native-binary staleness, GPU/LSP/AST/session-daemon state | ~2-5s | No |
| `python scripts/agent_readiness.py --json` | 13 repo-local trust checks (+ up to ~9 more public-shell probes) — the governed pre-push gate | ~1-5 min | Only `--output <path>` if given |
| `tg dogfood --json` | Wraps `agent_readiness.py` as a subprocess; adds a one-page verdict + a static "what this does NOT prove" disclaimer | Same as above + small overhead | Only `--output <path>` (+ sibling `.agent-readiness.json`) |
| `python scripts/dogfood/dogfood_features.py` | Runs the **real published binary** (post-release, in Docker or any clean install) through every user-facing feature | Seconds | No |

None of these four tools overlap in scope — each answers a different question. Run `tg doctor`
first (cheapest), `agent_readiness.py`/`tg dogfood` before a push, and the post-release Docker
dogfood only after a version actually publishes.

## Tool 1: `tg doctor --json` — field-by-field interpretation

Source: `_build_doctor_payload` / `_render_doctor_payload` in `src/tensor_grep/cli/main.py`
(command registration at `main.py:9903`, payload builder at `main.py:2745`).

```powershell
tg doctor --json --no-lsp        # fast (~2-5s); always prefer this while iterating
tg doctor --json --with-lsp      # slower; also probes external LSP providers
tg doctor                        # same payload, human-readable field dump (no PASS/FAIL judgment)
```

`--with-lsp` is the **default** for `tg doctor` (`--with-lsp/--no-lsp`, default `True`) — pass
`--no-lsp` explicitly for a fast check, which is what `scripts/agent_readiness.py` and the
`AGENTS.md` release checklist both do (`tg doctor --json --no-lsp`).

`tg doctor`'s own human-readable renderer (`_render_doctor_payload`) is a **straight field dump** —
it does not compute PASS/FAIL/WARN. The bundled `doctor_traffic_light.py` script (below) fills that
gap.

### Load-bearing fields

| Field | Healthy value | What a bad value means |
|---|---|---|
| `version` | your expected tensor-grep version string | absent/mismatched → doctor itself couldn't resolve the installed package |
| `search_acceleration_backend` | `standalone-native-tg` \| `rust-core-extension` \| `python` | These are the only 3 values the current builder emits (verified live: `standalone-native-tg` on this box). `scripts/agent_readiness.py`'s validator also accepts a 4th legacy string `native-standalone` that nothing currently produces — don't be surprised if you never see it. |
| `native_tg_binary_kind` | `standalone-executable`, `in-tree-*`, etc. | tells you WHERE the resolved native binary came from |
| `path_tg_first_launcher_kind` | `native-exe` \| `managed-native` \| `cmd-shim` \| `powershell-shim` \| `python-entrypoint` \| `bash-shim` | `foreign` means the first `tg` on PATH is **not** tensor-grep (some unrelated tool named `tg`) |
| `path_tg_first_version_matches` | `true` (or `null` if unprobed) | `false` is a hard problem: the first `tg` your shell resolves is stale or foreign. Read `path_tg_foreign_warning` + `path_tg_foreign_remediation` alongside it. |
| `fresh_shell_path_tg_first_launcher_kind` / `*_version_matches` | same shape as above, but simulated for a **brand-new shell** (reads Windows registry `PATH` on Windows) | catches "your current shell is fixed but a fresh terminal still resolves the wrong `tg`" |
| `python_subprocess_path_tg_first_*` (Windows only) | same shape | Python's own `subprocess.run(["tg", ...])` can resolve **differently** than your interactive shell (e.g. Windows `CreateProcess` picks `.exe` ahead of a `.com` bridge) — this is what MCP servers and other Python tooling actually see |
| `rust_binary_version_status` | `matches` or `stale-skipped` | `missing` is often benign (no standalone native binary in play; check `search_acceleration_backend`). `stale` or `mismatch` is a real problem — see remediation below. |
| `rust_binary_remediation` | `null` when healthy | when non-null, it is a copy-pasteable fix string, e.g. rebuild-the-in-tree-binary guidance |
| `skipped_native_tg_binaries` | `[]`, or a list of correctly-ignored stale in-tree binaries | a non-empty list here is the **healthy** outcome when you have an old local dev build lying around — it means doctor correctly did NOT select it |
| `mcp_stdio_launcher_warning` | `null` | non-null on Windows usually means a PowerShell shim (`tg.ps1`) is ambiguous for MCP stdio clients; the message tells you to point the MCP client at the native `tg.exe` directly |
| `gpu.available` / `gpu.search_ready` / `gpu.tier.promotion_proof` | `available` reflects CUDA device presence; `search_ready` reflects whether a real search actually routed through `NativeGpuBackend` | **`gpu.available=true` does NOT mean GPU search works.** Always read `search_ready` and `tier.promotion_proof`, not just `available`. GPU is experimental-until-proven (see `docs/gpu_crossover.md`) — never a PASS/FAIL signal, always informational. |
| `lsp.enabled` / `lsp.providers[].health_status` | `health_status` in `{ready, available_unverified, unhealthy, missing}` | **provider availability is not navigation proof.** A provider counts as real LSP evidence only when a completed request set `lsp_provider_response = true` — `provenance = "lsp-*"` alone is not enough (`AGENTS.md` LSP rules). |
| `ast_grep.available` / `ast_grep.binary` | `true` / a resolved path | `false` degrades `tg run`'s semantic (`--selector`/`--strictness`) options; AST structural search itself still works via the native backend |
| `session_daemon.running` | informational | `true` means a warm localhost daemon is serving cached repo-map/session state for this root |

### Live example (this repo, this box, 2026-07-02)

```
version = 1.17.23
search_acceleration_backend = standalone-native-tg
path_tg_first_launcher_kind = native-exe
path_tg_first_version_matches = True
rust_binary_version_status = matches
mcp_stdio_launcher_warning = "MCP stdio launcher warning: PATH candidate 4 resolves
  C:\Users\oimir\bin\tg.ps1; ... Configure MCP clients for `tg mcp` to call the managed
  native tg.exe directly ..."
gpu.available = True, gpu.search_ready = False   (2 CUDA devices present, search NOT GPU-routed)
```

This is a **healthy** payload with one real WARN (the MCP stdio shim ambiguity) — a good
demonstration that "all green" is not the bar; you read every field's *meaning*, not just its
presence.

### Remediation you'll actually use

- **`rust_binary_version_status = stale`** (an in-tree dev build IS being selected and it's old):
  rebuild it. On this dev box: `C:/Users/oimir/.cargo/bin/cargo.exe build --manifest-path
  rust_core/Cargo.toml --release` (this exact command is the shipped `rust_binary_remediation`
  string in `main.py`'s `_doctor_rust_binary_remediation`, not just a local aside) — or set
  `TG_NATIVE_TG_BINARY` to pin a specific binary. Full toolchain setup: `tensor-grep-build-and-env`.
- **`rust_binary_version_status = stale-skipped`**: nothing to do — this is the healthy "doctor
  correctly ignored your stale local build" outcome, unless you specifically need that local build
  to take effect (then rebuild, or pin `TG_NATIVE_TG_BINARY`).
- **`path_tg_first_launcher_kind = foreign`**: another program named `tg` shadows tensor-grep on
  PATH. Try `tg repair-launcher` (`--allow-foreign-rename` if you own that foreign binary and want
  it backed up/replaced), or reorder PATH manually.
- **`mcp_stdio_launcher_warning` non-null**: point your MCP client config at the managed native
  `tg.exe` path directly (the warning message includes the exact resolved path), not `tg.ps1`.

## Tool 2: `python scripts/agent_readiness.py` — the governed pre-push gate

Source: `scripts/agent_readiness.py` (entire file; `build_check_plan` at line 666).

```powershell
python scripts/agent_readiness.py --json --output artifacts/agent_readiness.json
python scripts/agent_readiness.py --no-shell-probes --no-wsl-probe --json   # repo-local checks only
python scripts/agent_readiness.py --only-shell-probes                       # public shell probes only
```

This is the exact command `AGENTS.md` "Required Local Validation" tells you to run before push,
alongside `tg dogfood` (`AGENTS.md:316-323`).

### Two independent phases

1. **Public shell probes** (skip with `--no-shell-probes`): verify the *installed, on-PATH* `tg`
   resolves consistently across PowerShell, `cmd`, unprofiled `pwsh`, Git Bash, WSL (optional), and
   — Windows only — a Python `subprocess.run(["tg", ...])` call, plus `tg doctor --json --no-lsp`
   via `cmd`/`pwsh`, a quoted-multi-word-no-match-pattern regression guard
   (`public-windows-launcher-quoted-patterns`), and `public-search-advertised-flag-sweep` (every
   rg-style flag exercised by its sweep cases — 65 distinct flag tokens as of this writing,
   including all `--no-*` inverse-config overrides — must both appear in `tg search --help` and
   actually round-trip through the real binary without an "unexpected argument" error).
2. **Repo-local checks** (skip with `--only-shell-probes`; this is the **13-check fast gate**
   referenced elsewhere in project history): `repo-cli-build-warmup`, `repo-doctor`,
   `context-render-trust`, `rg-parity-edges`, `broad-generated-scan-guard`, `ast-info-json`,
   `ast-run-smoke`, `mcp-context-render-smoke`, `mcp-stdio-protocol-smoke`, `agent-capsule`,
   `agent-capsule-mixed-language`, `agent-capsule-hardcases`, `docs-claim-check` — exactly 13 when
   counted in `build_check_plan`.

### Check name → what it actually verifies

| Check name | What it verifies | A SKIP (not FAIL) is normal when... |
|---|---|---|
| `repo-cli-build-warmup` | `uv run tg --version` — syncs + warms the editable build; **must pass first**, everything after trusts it | never skips; if it fails, run `uv sync` (the validator message tells you this) |
| `repo-doctor` | `uv run --no-sync tg doctor --json --no-lsp` sanity on the just-built repo `tg` | — |
| `context-render-trust` | `tg context-render` on a fixed test file/query keeps the correct primary file + body context (an agent-capsule target-selection regression guard) | — |
| `rg-parity-edges` | `tests/e2e/test_rg_parity_edges.py` — deterministic rg edge cases (BOM, NUL bytes, etc.) | — |
| `broad-generated-scan-guard` | unbounded/broad generated-root scans require explicit bounds or opt-in (a DoS/hang guardrail) | — |
| `ast-info-json` | `tg ast-info --json` lists `python` among supported languages | — |
| `ast-run-smoke` | `tg run --pattern ... --lang js --json` AST smoke | AST dependencies/backend are unavailable in this environment (matched via `skip_error_patterns`) |
| `mcp-context-render-smoke` | MCP `context-render` tool preserves invoice body + target | — |
| `mcp-stdio-protocol-smoke` | full `tg mcp` stdio `initialize`/`tools/list`/`tools/call` roundtrip | — |
| `agent-capsule` | `tg agent` Actionable Context Capsule, CLI + MCP | — |
| `agent-capsule-mixed-language` | mixed-language invoice capsule + validation stay aligned | — |
| `agent-capsule-hardcases` | polyglot monorepo, generated-noise, Rust/Python/JS/TS hardcases | — |
| `docs-claim-check` | **no subprocess** — reads `AGENTS.md`/`README.md`/`SKILL.md`/`docs/*.md` directly and checks required fragments + version-staleness prose patterns + a banned-phrase list on GPU docs | — |

`docs-claim-check` (`validate_docs_claims` in `agent_readiness.py:537`) is the mechanism that
enforces the **no-oversell rule** described in `AGENTS.md`: it bans phrases like `"mathematically
guaranteeing"`, `"0ms interpreter lag"`, `"peak theoretical throughput"`, `"GPU-ready"` from
`docs/benchmarks.md`, `docs/gpu_crossover.md`, and `docs/PAPER.md`, and requires phrases like `"not
promotion-ready"` and `"no crossover"` to be present. If you are writing docs or a skill and this
check would fail on your prose, that prose is oversold — reword it.

### JSON report shape

```jsonc
{
  "artifact": "agent_readiness_report",
  "status": "complete",              // "running" while --output is being written incrementally
  "expected_version": "1.17.25",
  "root": "...",
  "summary": {"passed": N, "failed": N, "skipped": N},
  "results": [
    {"name": "...", "status": "passed|failed|skipped", "duration_s": 1.23,
     "command": [...], "returncode": 0, "message": "...",
     "stdout_tail": [...], "stderr_tail": [...]}   // last 20 lines, 4000 chars each, bounded
  ]
}
```

With `--output <path>`, the file is rewritten **after every check** (`status: "running"` +
`current_check`), so you can tail a still-running run instead of waiting for it to finish —
useful when the shell-probe phase is slow.

`agent_readiness.py` itself has **no built-in timeout** for the whole run; timeout wrapping is
`tg dogfood`'s job (see below).

## Tool 3: `tg dogfood` — verdict + JSON envelope around `agent_readiness.py`

Source: `src/tensor_grep/cli/dogfood.py` (`run_dogfood_readiness`), CLI command at
`main.py:9651`.

```powershell
tg dogfood --output artifacts/dogfood_readiness.json
tg dogfood --json --timeout-s 170 --no-wsl-probe
```

`tg dogfood` **spawns `python scripts/agent_readiness.py` as a subprocess** (same two phases as
Tool 2) and wraps it with:

```jsonc
{
  "artifact": "dogfood_readiness_report",
  "dogfood_version": 1,
  "command": [...],                    // the exact agent_readiness.py argv used
  "agent_readiness": { ... },          // the full nested report from Tool 2 (or a timeout stub)
  "verdict": {"status": "PASS"|"FAIL", "failed_checks": [...], "summary": "..."},
  "world_class_readiness": { ... },    // see below -- READ THIS CAREFULLY
  "write_policy": { "mode": "read_only_except_explicit_output_and_readiness_probe_output", ... },
  "release_docs_worktree": {"status": "clean"|"dirty"|"unknown", "dirty_paths": [...]}
}
```

**`world_class_readiness` is a STATIC disclaimer block, not a live signal.** `_build_world_class_readiness()`
(`dogfood.py:203`) takes **zero arguments** and returns the identical literal content on every
single run, regardless of repo state. Its `status` field is always `"not_claimed"`. Its purpose is
purely governance: it exists so a passing `tg dogfood` run can never be misread as "tg replaces
`rg`", "tg replaces `ast-grep`", "GPU is promotion-ready", or "LSP navigation is proven" — each of
those surfaces has an explicit `required_evidence` entry describing what WOULD need to be true. Do
not write code or docs that branch on `world_class_readiness.status` expecting it to reflect
anything about the current run.

`write_policy` documents `tg dogfood`'s own no-mutation contract: it writes only the explicit
`--output` path plus a sibling `<output-stem>.agent-readiness.json` child report (or, with no
`--output`, `artifacts/agent_readiness/dogfood-agent-readiness.json`). Tracked release docs are
**never** touched by `tg dogfood` — that's `python scripts/stamp_release_assets.py`'s job, a
separate release-workflow step. `release_docs_worktree` just reports `git status --porcelain` on
the 7 governance doc paths (`AGENTS.md`, `README.md`, `SKILL.md`, `docs/SESSION_HANDOFF.md`,
`docs/CONTINUATION_PLAN.md`, `docs/CONTRACTS.md`,
`tests/unit/test_public_docs_governance.py`) — informational, does not affect `verdict`.

`--timeout-s` (default `170.0`) bounds the whole nested `agent_readiness.py` subprocess; on
timeout, `tg dogfood` kills the process tree (via `psutil` if available, else `taskkill /T /F` on
Windows) and injects a synthetic `agent-readiness-timeout` failed check plus `"status":
"timed_out"` into the `agent_readiness` object — this is the ONLY place a timeout concept exists;
`agent_readiness.py` run standalone has none.

## Tool 4: post-release Docker dogfood (`scripts/dogfood/`)

Source: `scripts/dogfood/dogfood_features.py`, `scripts/dogfood/README.md`,
`scripts/dogfood/Dockerfile`.

```bash
# after a version actually publishes to PyPI:
docker build --build-arg TG_VERSION=1.17.25 -f scripts/dogfood/Dockerfile -t tg-dogfood scripts/dogfood
docker run --rm tg-dogfood
# or, without Docker, against any installed tg:
pip install "tensor-grep==1.17.25"
python scripts/dogfood/dogfood_features.py         # or TG_BIN=/path/to/tg python ...
```

This is the **only** one of the four tools that runs the real, installed, published `tg` binary
through its actual front door (`tensor_grep.cli.bootstrap:main_entry`). Tests using Typer's
`CliRunner` — and, for CLI-invoked checks, parts of `agent_readiness.py`/`tg dogfood` when run
inside a `uv run` editable checkout — still exercise real subprocesses, but the specific value of
this tool is validating the **published PyPI artifact**, in a clean environment, with zero repo
context. It generates a tiny fixture repo (a hub function imported by two modules, plus a `.rs`
file) and runs `--version`, `search` (plain + `--rank` regression guard for the v1.14.0 bug +
`--rank --json` + `--json`), `orient` (+ `--json` + empty-dir), `map`, `agent --json`. Exit 0 =
every feature works on the shipped artifact; exit 1 = a named regression with output.

**When you ship a new user-facing feature, add a `check(...)` line here** so the battery grows.
For the full "why CliRunner alone is not enough" rationale and workflow discipline, see the global
skill `dogfood-the-shipped-artifact` — this section only covers what the script measures.

## Benchmarks — which script answers which question

Deep methodology (claim-quality artifacts, the noise-floor/absolute-jitter rule, the fair-baseline
rule, launcher-attribution fields) lives in `tensor-grep-benchmark-and-proof-toolkit` — load that
skill before constructing or disputing a speed claim. This table is only the "which tool do I
reach for" lookup (source: `AGENTS.md` "Benchmark Rules", verified against each script's
`argparse` block):

| Change area | Script | Compare with |
|---|---|---|
| plain search routing, startup/launcher, text-search control plane | `benchmarks/run_benchmarks.py` | `benchmarks/check_regression.py --baseline auto` |
| StringZilla index, CPU regex prefilter, cache/decode/posting-list | `benchmarks/run_hot_query_benchmarks.py` | absolute-jitter tolerance for sub-10ms rows |
| AST single-query cold start (`tg run` vs native `sg`) | `benchmarks/run_ast_benchmarks.py` | — |
| `run`/`scan`/`test` AST workflow startup/batching | `benchmarks/run_ast_workflow_benchmarks.py` | — |
| `tg agent` capsule routing, edit-loop latency | `benchmarks/run_agent_workflow_benchmarks.py`, `benchmarks/run_agent_success_harness.py` | workflow evidence, **not** a cold exact-text speed claim |
| GPU/NLP backend | `benchmarks/run_gpu_benchmarks.py` | `rg -F -e ... -e ...` (the fair many-pattern baseline); treat cyBERT `SKIP` as expected infra state when Triton is unavailable, not a fake failure |

`benchmarks/run_benchmarks.py` refuses **claim-quality** output (not the run itself) when the timed
`tg` entrypoint is a stale in-tree native binary (`benchmark_binary_warnings` /
`benchmark_claim_blockers` in `run_benchmarks.py:194-225`) — it prints a blocker to stderr and
requires `--allow-claim-unsafe-launcher` to proceed anyway for exploratory-only timing. It also
tags every artifact with `tg_launcher_mode` and `tg_launcher_command_kind`
(`classify_tg_launcher_command`, e.g. `native_exe`, `uv`, `python_module`, `cmd_shim`,
`powershell_shim`) so a slow wrapper/interpreter overhead is never silently attributed to the
search engine itself. `benchmarks/check_regression.py --baseline auto` resolves
`benchmarks/baselines/run_benchmarks.<platform>.json` and fails (exit 2) on an environment mismatch
unless you pass `--allow-env-mismatch`.

A handful of older scripts (`scripts/parity_check.py`, `scripts/stress_test_gauntlet.py`,
`scripts/generate_parity_corpus.py`) exist but are **not** referenced by `AGENTS.md` or
`CONTRIBUTING.md` and are not part of the governed gate — treat them as legacy/exploratory, not a
source of truth.

## Common failure patterns

| Symptom | Likely cause | What to do |
|---|---|---|
| `tg doctor` shows `path_tg_first_launcher_kind = foreign` | a different tool named `tg` is first on PATH | `tg repair-launcher` (see remediation above) |
| `rust_binary_version_status = stale` (not `stale-skipped`) | a dev build in `rust_core/target/{debug,release}/` is stale **and selected** | rebuild via cargo (see remediation above); `tensor-grep-build-and-env` for full toolchain setup |
| `ast-run-smoke` shows `SKIPPED` | AST deps/backend unavailable in this environment | expected, not a regression — do not treat as FAIL |
| `docs-claim-check` FAILS right after a version bump | a doc still has stale `vX.Y.Z` prose, or is missing a required fragment | usually self-heals via `python scripts/stamp_release_assets.py`'s `version_variables` stamping during release; if hand-editing docs, match the exact prose patterns in `validate_docs_claims` |
| Unit/integration tests are all green, but the real published binary is broken | `CliRunner` bypasses `tensor_grep.cli.bootstrap:main_entry` (the real front door) | run Tool 4 (`scripts/dogfood/dogfood_features.py`) against the real binary; see `dogfood-the-shipped-artifact` |
| `repo-cli-build-warmup` fails or times out | the repo-local `uv`/`tg` editable entrypoint is stale/unsynchronized | `uv sync`, or `uv run --refresh-package tensor-grep tg --version` |
| `gpu.available = true` but you expected GPU search to actually run | `available` only reflects CUDA device presence | check `gpu.search_ready` and `gpu.tier.promotion_proof` instead — GPU is experimental-until-proven |

## The bundled traffic-light script

`scripts/doctor_traffic_light.py` (in this skill directory) fills the one real gap in the four
tools above: there is no fast, standalone way to get a PASS/WARN/FAIL judgment on a single
`tg doctor --json` snapshot without either eyeballing ~40 raw fields yourself or running the full
3-5 minute `agent_readiness.py`/`tg dogfood` gate. It is read-only (writes only to an explicit
`--output` you pass) and works against **any** installed `tg`, not just a repo checkout.

```powershell
python .claude/skills/tensor-grep-diagnostics-and-tooling/scripts/doctor_traffic_light.py
python .claude/skills/tensor-grep-diagnostics-and-tooling/scripts/doctor_traffic_light.py --json
python .claude/skills/tensor-grep-diagnostics-and-tooling/scripts/doctor_traffic_light.py --with-lsp
python .claude/skills/tensor-grep-diagnostics-and-tooling/scripts/doctor_traffic_light.py --root C:\some\repo --tg-bin tg
python .claude/skills/tensor-grep-diagnostics-and-tooling/scripts/doctor_traffic_light.py --from-file captured_doctor.json
```

It is a **supplement**, not a replacement: passing it only means one doctor snapshot looks sane —
it does not run any of the 13 governed `agent_readiness.py` checks, does not dogfood the real
published binary, and does not run a benchmark. Exit code 0 unless a check is FAIL (WARN/INFO never
fail the exit code, matching `tg doctor`'s own non-gating nature).

## Provenance and maintenance

Verified against v1.17.25 (2026-07-02) by reading the cited source directly. Re-verify if this
skill feels stale:

```powershell
# current version
grep -n '^version = ' pyproject.toml

# doctor payload fields (re-check the table above against the real builder)
grep -n 'search_acceleration_backend\|rust_binary_version_status\|launcher_kind' src/tensor_grep/cli/main.py

# agent_readiness check names / count (13 repo-local checks expected)
grep -n 'name="' scripts/agent_readiness.py

# tg dogfood verdict/world_class_readiness shape
grep -n 'world_class_readiness\|_build_verdict\|write_policy' src/tensor_grep/cli/dogfood.py

# benchmark script -> change-area mapping, still current
grep -n '^### ' AGENTS.md | sed -n '/Benchmark Rules/,/Performance Discipline/p'

# does the traffic-light script still run clean against the live binary?
python .claude/skills/tensor-grep-diagnostics-and-tooling/scripts/doctor_traffic_light.py --json
```

Open uncertainties (do not treat as settled without re-checking):

- `agent_readiness.py`'s doctor validator accepts a legacy `"native-standalone"` backend string
  that the current `_build_doctor_payload` never emits — unclear if this is intentional forward
  compatibility or simple drift; harmless either way (the traffic-light script also accepts it).
  Re-check `main.py`'s `search_acceleration_backend` ternary if a 4th backend kind is ever added.
  Also unconfirmed against `agent_readiness.py`: no schema link — the two lists (this skill's
  `KNOWN_BACKENDS` and the payload builder) are recorded from direct reads, not enforced by a
  shared constant, so a future rename can silently desync all three.
  DEFERRED CANDIDATE: introduce a shared source-of-truth for this enum before it grows a 5th value.
