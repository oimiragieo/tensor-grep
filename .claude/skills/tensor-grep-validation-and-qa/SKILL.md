---
name: tensor-grep-validation-and-qa
description: Use when deciding what counts as proof that a tensor-grep (tg) change works — before trusting a subagent's "tests pass", writing a new test, claiming a routing/docs/release fix is done, shipping a doc-drift/ranking/classification heuristic off green fixture tests, or running the pre-push gate. Covers TDD-first discipline, the CliRunner-vs-real-binary trap, the fixture-green-vs-real-corpus-dogfood trap for precision/heuristic features, the `capfd`-vs-`result.stdout` capture-surface trap on routing/delegation changes (needs `tests/integration/` run with the native `tg` binary rebuilt, not just `tests/unit/`), the certified/golden inventory (routing parity, docs governance, release-asset validation), agent-readiness/`tg dogfood`, benchmark-gated speed claims, acceptance thresholds, and which suite/marker/fixture to use for a new test, plus the `--preview` / `--no-sync` / `-x` gotchas.
---

# tensor-grep validation and QA

This is the **evidence-bar runbook**: what is allowed to count as proof that a change to `tensor-grep`
(the `tg` CLI) works, and how to add a test that actually enforces it. `tensor-grep` describes itself
as a "benchmark-governed, contract-heavy codebase" (`CONTRIBUTING.md:3`) — many behaviors are pinned by
tests that fail on drift, and speed claims are gated by measured numbers, not review opinion.

## Who this is for

Two readers, written to the **lower bound** of each:

- A **Sonnet-class AI** in a cheap autonomous session: copy-pasteable commands and hard gates so you
  cannot silently skip validation.
- A **mid-level human engineer**: the *why* behind each gate, so you extend it correctly to new cases.

## When to use this skill vs a sibling

| Your task | Use |
|---|---|
| "Is this proof good enough to claim done?" / adding or picking a test | **this skill** |
| The non-negotiable gates (draft-PR-only, registration sites, fail-closed contract, push-race) | `tensor-grep-change-control` |
| Picking/reading a `benchmarks/*.py` script, the noise-floor rule for sub-10ms rows | `tensor-grep-benchmark-and-proof-toolkit` |
| Interpreting a `tg doctor --json` / `tg dogfood` field — what it does and does NOT prove | `tensor-grep-diagnostics-and-tooling` |
| A live bug/red-CI to triage | `tensor-grep-debugging-playbook` |
| "Has this already been tried and lost?" | `tensor-grep-failure-archaeology` |
| Internals/why the front door is shaped this way | `tensor-grep-architecture-contract` |
| Env var / flag reference | `tensor-grep-config-and-flags` |
| Day-to-day CLI invocation syntax | `tensor-grep-run-and-operate` |
| Writing docs of record (AGENTS.md, README, docs/*.md) | `tensor-grep-docs-and-writing` |
| Release mechanics / positioning | `tensor-grep-release-and-positioning` |
| Using `tg` to navigate a codebase | `tensor-grep` (usage skill) / `code-search-and-retrieval-reference` |

**No skill routes around change-control.** This skill tells you what evidence a gate needs; it does not
relax any gate in `tensor-grep-change-control`.

---

## Part 1 — What counts as evidence here (in order of trust)

Ranked by how hard each is to fake, cheapest-to-check first:

1. **A failing test written before the fix** (TDD-first). `CONTRIBUTING.md` "Performance Discipline":
   *"Start with a failing test when behavior changes."* Repeated in `AGENTS.md` Operating Rules #1
   (`AGENTS.md:170`). If you cannot point to the test that failed before your diff, the fix is
   unverified — see `superpowers:test-driven-development`.
2. **A contract test**, not just a behavior test. This repo names them `test_*_contract*.py` /
   `test_*_contracts.py` (e.g. `tests/e2e/test_backend_contracts.py`,
   `tests/e2e/test_io_contracts.py`, `tests/unit/test_main_cli_contracts.py`,
   `tests/unit/test_rg_contract.py`). A contract test asserts an invariant that must hold for *every*
   implementation of a protocol (every `ComputeBackend` must expose `.matches` /
   `.total_matches` / `.is_empty` — `tests/e2e/test_backend_contracts.py:8-12`), not one code path's
   happy case.
3. **Dogfood on the real binary, not `CliRunner`.** `tests/unit/` uses Typer's `CliRunner` 400+ times
   (`grep -rc CliRunner tests/unit/*.py`) — `CliRunner` calls the Typer `app` object directly and
   **skips `tensor_grep.cli.bootstrap:main_entry` entirely**, so a routing bug in the bootstrap front
   door (the layer that intercepts plain-text searches and forwards them to `rg` *before* Typer ever
   sees `argv`) is invisible to it. This is not hypothetical: the `tg search --rank` flag shipped
   broken to real users while every `CliRunner` test stayed green, because the flag was missing from
   one of the two search-flag front doors (`CONTRIBUTING.md:73`, `AGENTS.md:198-202`). After any
   command/flag/routing change, run the real binary: `python scripts/dogfood/dogfood_features.py`
   (installed `tg` on PATH) or the clean-room Docker path in `scripts/dogfood/README.md`. See
   `dogfood-the-shipped-artifact` (global skill) and `tensor-grep-change-control` Part 5.
4. **Fixture-green is not sufficient for a precision/heuristic feature — dogfood the real corpus, not
   just the fixtures you wrote alongside it.** A test suite authored together with a detection
   heuristic tends to only contain the cases the author already thought of; the failure mode that
   actually matters (flooding false positives) never shows up until the heuristic meets a real, larger
   corpus. Receipt (2026-07-03): the `tg diff-docs` MVP (round-4 design-council build, commit
   `90b7042` "wip: tg diff-docs foundation (DEFERRED — precision inadequate, see task)" on
   `wip/diff-docs-precision`, **not merged to `main`**) shipped with 17 green tests in
   `tests/unit/test_diff_docs.py` (`grep -c "def test_"` on that commit) — every fixture passed — but a
   dogfood run against this repo's real `docs/` vs `src/` corpus produced on the order of 20,000
   findings, the large majority flagging language/stdlib types (`String`, `Option`, `Vec`) as
   "unresolved symbols" because nothing in the design gated on a *positive* in-repo reference signal.
   `diff_docs.py`'s own module docstring names the mechanism up front: naive code-doc drift detection
   is independently measured at 0.62 precision / 98% flag-rate (DocPrism, arXiv 2511.00215) — this is
   the expected failure mode of the whole naive-heuristic class, not a one-off implementation bug. The
   correct call was to **defer, not ship**: 20k false positives trains the agent to ignore the tool,
   which is worse than not shipping it. Before shipping any precision/heuristic feature (doc-drift,
   ranking, classification, dedup), run it on this repo's own real corpus and eyeball the finding count
   and the top hits — a green fixture suite alone cannot catch a flooding failure mode.
5. **A routing/delegation change is only proven by `tests/integration/`, run with the native `tg`
   binary built — not `tests/unit/` alone.**
   `tests/integration/test_bm25_search_flag.py::test_search_rank_reorders_by_bm25` read `capfd` (the
   OS-fd-level stream) because, before commit `5e6f780` (#342), `--rank` silently delegated to the
   native subprocess, which is what actually wrote to that fd. Fixing the delegation gate to refuse
   `--rank` (so the BM25 rerank runs in-process) moved the JSON emission to a different capture channel
   — `typer.echo` -> `CliRunner`'s captured `result.stdout`, not the fd — and broke `main`'s release
   the same day. Commit `ab717a1`'s own message: *"#342 ... merged but its release failed:
   `test_search_rank_reorders_by_bm25` read fd-level `capfd`, which only captured output when `--rank`
   *wrongly* delegated to the native subprocess. ... Only surfaces on main/release CI, which builds the
   native binary; PR CI skips it."* (fixed by reading `result.stdout` instead of
   `capfd.readouterr().out`). Two takeaways: (a) `capfd` in a `CliRunner` test is an implicit assertion
   that a **real subprocess** wrote to the OS stdout fd — any change to whether a code path delegates
   natively or runs in-process can silently break that assertion in either direction, and a green
   `tests/unit/` run will not catch it because the native binary isn't built there either; (b) before
   trusting a routing/delegation change, rebuild the native binary locally
   (`cargo build --manifest-path rust_core/Cargo.toml --bin tg`, or add `--release` — see
   `tensor-grep-build-and-env`) and run `uv run pytest tests/integration -q` — PR review alone builds
   neither, so a `--rank`/`--sort-files`-class bug is invisible until it reaches `main`.
6. **A benchmark line vs the accepted baseline**, for any hot-path/speed claim. Never trust a
   microprofile or memory of "it felt faster." Full decision table and noise-floor rules live in
   `tensor-grep-benchmark-and-proof-toolkit` — do not duplicate that table here; use it.
7. **The agent-readiness gate** (`scripts/agent_readiness.py`, wrapped by `tg dogfood`) — a CI-blocking
   fast dogfood of agent-critical surfaces (Part 4 below).
8. **Live extension call for FFI/PyO3 changes**, never a mock alone. A mock-based bridge test passed
   green while the real PyO3 extension silently dropped every forwarded flag and fell back to the
   Python engine — the mock could not see it. Prove an FFI change by calling the *built* extension at
   runtime and checking the flag actually reached `rg`.
9. **A subagent's "tests pass" is a hypothesis, not evidence**, until re-run against external state —
   an exit code you observed, a `file:line` that resolves, or a real dogfood run. This applies doubly
   to worktree-fanout branches: a worktree has no `.venv`, so a subagent's claim is *literally
   un-runnable in its own tree* until re-run in the real environment.

---

## Part 2 — Required local validation (run before push)

From `CONTRIBUTING.md:5-14` and `AGENTS.md:293-338`:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

For release/workflow/package-manager changes, also:

```powershell
uv run python scripts/validate_release_assets.py
```

Gotchas that each cost a real CI cycle when missed:

- **`ruff format` needs `--preview`; `ruff check` must NOT get it.** CI runs
  `ruff format --check --preview .` but `ruff check .` with no `--preview`. Running
  `ruff format` **without** `--preview` locally is an *active revert* — it rewrites preview-style
  lines back to non-preview style on disk, so the next CI `ruff format --check --preview` fails on
  lines you never touched. Passing `--preview` to `ruff check` produces false failures instead
  (preview lint rules like RUF056 don't match the CI lint gate). (`CONTRIBUTING.md:22`)
- **Windows CRLF false-alarms a bare `ruff format --check`.** `.gitattributes` pins `*.py`/`*.rs` to
  `eol=lf`; run `ruff format --preview <files>` (which normalizes) before trusting a local check.
  Audit real on-disk endings with `git ls-files --eol` — `git show`/`git cat-file -p` smudge output
  and can report false CR. (`CONTRIBUTING.md:24`)
- **`mypy` runs in `strict = true` mode** targeting `python_version = "3.11"` syntax even though the
  repo's CI-tested floor is 3.11-3.12 (`pyproject.toml:109-116`) — new functions need full type
  annotations (`disallow_untyped_defs = true`); do not rely on inference alone.
- **`uv run` alone re-syncs the environment to default deps and silently drops optional extras**
  (e.g. `[dev]`'s tree-sitter). If a prior step installed extras deliberately, use `uv run --no-sync`
  to keep them — this is exactly what CI's `agent-readiness` job does before running the readiness
  gate (`.github/workflows/ci.yml:133-134`). Forgetting `--no-sync` after an extras install is how a
  "clean" local run diverges from what CI actually validated.
- **`pytest` addopts include `-x`** (stop at first failure) — `pyproject.toml:49`. Useful for fast
  local iteration, but it means one early failure hides every later one in the same run. For a
  full-suite pass with no early exit, override on the command line:
  `uv run pytest -q --maxfail=0` (the last `--maxfail` value wins over the `-x` baked into `addopts`;
  verified empirically 2026-07-02).
- **The full suite is slow on Windows.** `uv run pytest -q` can exceed 70-90s when the full
  JS/TS/e2e surface is hot; budget at least 120s for narrow suites and much more for the full run
  under automation (`AGENTS.md:306`). Run a narrow suite first for a focused change, e.g.:
  ```powershell
  uv run pytest tests/unit/test_cli_bootstrap.py -q
  uv run pytest tests/unit/test_cpu_backend.py -q
  uv run pytest tests/unit/test_release_assets_validation.py -q
  ```
- **Decode the structured CI failure before theorizing.** When a CI check goes red, open its
  structured JSON output (`gh run view <id> --json jobs`, then `--log-failed` on the named job)
  before reading prose tracebacks — it names the exact gate/file/line. A June-2026 README rewrite
  cost 4 wasted CI round-trips because the team theorized from tracebacks instead of decoding the
  failing check first (`CONTRIBUTING.md:26`).
- **A local full-suite `pytest` pass without the native binary built does not prove a
  routing/delegation change.** `resolve_native_tg_binary()`
  (`src/tensor_grep/cli/runtime_paths.py:230`) looks for
  `rust_core/target/{release,debug}/tg(.exe)` first; if neither exists, every `native`-launcher test
  in `tests/e2e/test_routing_parity.py` and the fd-vs-in-process split in
  `tests/integration/test_bm25_search_flag.py` silently **skip** (`pytest.skip(...)`) instead of
  failing — a skip reads as a green summary line, not as "unverified." Rebuild before trusting the
  run: `cargo build --manifest-path rust_core/Cargo.toml --bin tg` (add `--release` to match CI's
  `native-build-smoke` profile). Receipt and full mechanism: Part 1 point 5 (#342/#343).

---

## Part 3 — The certified/golden inventory

Three test surfaces are explicitly named in `CONTRIBUTING.md` "Important surfaces" (`:75-79`) as the
ones that must stay in sync with any workflow/docs/release-asset change. A fourth (routing parity) is
the load-bearing contract behind the "Adding a Command or Flag" rule. Treat all four as CI-blocking
certified truth, not advisory tests.

### 1. Routing parity — Python launchers + native golden output

- `tests/e2e/test_routing_parity.py` runs the **same argv** through three launchers —
  `python -m tensor_grep`, the compiled native `tg` binary, and `bootstrap.py` — and asserts matching
  exit code / stdout / stderr (`run_command`, `LAUNCHERS = ["python-m", "native", "bootstrap"]`,
  `test_routing_parity.py:126-143,342-427`). It also pins `PUBLIC_TOP_LEVEL_COMMANDS`
  (`test_routing_parity.py:17-57`) against both Python's and native's visible `--help` command lists
  (`test_top_level_help_visible_commands_match_public_contract`, `:493-503`) and pins
  `PUBLIC_SEARCH_HELP_FLAGS` (from `src/tensor_grep/cli/rg_contract.py:388`) against both
  `search --help` outputs (`:464-476`).
- `rust_core/tests/test_search_golden.rs` is a **Windows-only** (`#![cfg(windows)]`) Rust integration
  test that runs the built native `tg` binary against fixture data in `tests/golden/fixture_data/` and
  diffs the output against committed golden files (`tests/golden/*.txt`, e.g.
  `simple_string_match.txt`, `case_insensitive_match.txt`, `regex_match.txt`).
- CI wires this as the **`search-golden-parity` (windows-latest)** job, which runs
  `cargo test --test test_search_golden` (`.github/workflows/ci.yml:441-466`), and separately the
  cross-platform `test-python` matrix job runs the full `tests/` tree including
  `tests/e2e/test_routing_parity.py` (`uv run pytest tests -v --tb=short`,
  `.github/workflows/ci.yml:389-390`). Both are required by the `Semantic Release` job
  (`needs: [..., search-golden-parity, ...]`, `.github/workflows/ci.yml:862`) — a routing-parity
  regression blocks the release, not just the PR.
- This is the concrete enforcement mechanism behind the "4 registration sites for a command / 2 front
  doors for a search flag" rule in `tensor-grep-change-control` Part 3 — when you add a site, add it
  here too, or the CI registration-completeness gate (blocking since v1.17.1, #282) fails the run.

### 2. Docs governance — content-pinned assertions on docs of record

Several `tests/unit/test_*_docs_governance.py` / `test_*_docs.py` files assert that specific strings
still appear in specific docs, so a docs edit that silently drops a load-bearing claim fails CI instead
of drifting unnoticed:

- `tests/unit/test_public_docs_governance.py` — pins README pointers to canonical docs
  (`docs/benchmarks.md`, `docs/tool_comparison.md`, `docs/gpu_crossover.md`, `docs/routing_policy.md`,
  `docs/harness_api.md`, `docs/harness_cookbook.md`), capability phrases (`"tg calibrate"`, `"tg mcp"`,
  `"native CPU engine"`, `"benchmark-governed"`), and per-release verified-commit/tag markers
  (`test_public_docs_governance.py:1-56`).
- `tests/unit/test_enterprise_docs_governance.py` — pins README links to `docs/CI_PIPELINE.md`,
  `docs/SUPPORT_MATRIX.md`, `docs/CONTRACTS.md`, `docs/HOTFIX_PROCEDURE.md`, `docs/EXPERIMENTAL.md`,
  a `## Future Work` heading, the CI-tested-vs-best-effort Python version matrix, and that
  `docs/CONTRACTS.md` explicitly excludes experimental surfaces (`tg worker`, `TG_RESIDENT_AST`) from
  stability guarantees.
- Sibling governance files worth knowing exist: `test_benchmark_docs.py`, `test_benchmark_governance.py`,
  `test_harness_api_docs.py`, `test_issue_intake_governance.py`, `test_routing_policy_docs.py`,
  `test_stamp_release_assets.py`.
- Full authoring rules (which doc owns which contract, the two governance layers) live in
  `tensor-grep-docs-and-writing` — use that skill when *editing* a governed doc; use this skill to know
  the check exists and is CI-blocking.

### 3. Release-asset validation

- `scripts/validate_release_assets.py` — a standalone validator (`validate_all()` at
  `scripts/validate_release_assets.py:3474`, CLI entry `main()` at `:3626`) that checks
  release/package-manager asset consistency: README canonical-doc links and release markers, `uv.lock`
  editable version parity with `pyproject.toml`/`rust_core/Cargo.toml`/`npm/package.json`, and more.
  Run it directly: `uv run python scripts/validate_release_assets.py` — exit 0 and
  `"Release/package assets validation passed."` on success, exit 1 with one `ERROR:` line per failure
  otherwise.
- `tests/unit/test_release_assets_validation.py` (≈4950 lines — one of the largest test files in the
  repo as of 2026-07-02, behind `test_cli_modes.py` and `test_benchmark_scripts.py`) exercises
  `validate_release_assets.py` module functions directly via
  `importlib.util` rather than shelling out, including
  `test_should_validate_release_and_package_assets_consistency` which just calls `validate_all()` and
  asserts `errors == []` against the *real* repo state — i.e. it fails the instant any of the other
  release-asset invariants regress.
- Related validators worth knowing exist for release *proof* (not just static asset shape):
  `scripts/verify_github_release_assets.py`(→`test_verify_github_release_assets.py`),
  `scripts/validate_pypi_artifacts.py`, `scripts/validate_release_binary_artifacts.py`,
  `scripts/validate_release_version_parity.py`, `scripts/validate_pr_title_semver.py`,
  `scripts/stamp_release_assets.py`.
- CI enforces this via the `release-readiness` job (a strict docs build plus workflow/package-manager
  validator checks, `docs/CI_PIPELINE.md:16`) — also a `needs:` dependency of `Semantic Release`.
  Deep release-mechanics coverage (push-race, PR-title→bump schema) lives in
  `tensor-grep-release-and-positioning`; this skill only anchors it as a certified test surface.

### Golden/snapshot output tests (a fourth, smaller certified surface)

- `tests/e2e/test_output_golden_contract.py` — 20 `GOLDEN_CASES` (default/`--cpu`/`-o`/`-c`/`-r`/`-n`
  /binary/`--json`/`--ndjson` combinations) run through both `python-m` and `native` launchers and
  compared for output parity (`test_output_golden_contract.py:28-60`).
- `tests/e2e/test_output_snapshots.py` uses the `pytest-snapshot` plugin's `snapshot.assert_match`
  fixture (`pyproject.toml:356`, dev dependency) to pin exact JSON-formatter output, with file-path
  normalization to `<FILE>` so the snapshot stays host-independent
  (`test_output_snapshots.py:5-46`). Marker: `pytest.mark.snapshot` (registered in
  `pyproject.toml:43`).

---

## Part 4 — Agent-readiness / `tg dogfood`

`scripts/agent_readiness.py` is a fast (3-5 minute) CI-blocking dogfood gate for agent-critical
surfaces — separate from, and complementary to, the full local-validation gate (`AGENTS.md:323`).
`tg dogfood` (`src/tensor_grep/cli/main.py:9651`, `dogfood()`) wraps the same check plan with a
one-page verdict and an optional `--timeout-s` (default `170.0`) around the nested readiness process.

Run it directly:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

Useful flags on `scripts/agent_readiness.py` (`main()`, `:1123-1201`): `--json` (machine-readable
report to stdout), `--no-shell-probes` (skip public shell version probes — used by CI's Linux
`agent-readiness` job), `--only-shell-probes` (Windows-only shell probes, mutually exclusive with
`--no-shell-probes` — used by CI's `windows-agent-readiness` job), `--no-wsl-probe`.

**Acceptance semantics:** the script's exit code is `1 if report["summary"]["failed"] else 0`
(`:1201`) — any failed check fails the whole gate; there is no partial-credit threshold. CI wires two
blocking jobs off it — `agent-readiness` (Ubuntu, `--no-shell-probes --no-wsl-probe`,
`.github/workflows/ci.yml:104-140`) and `windows-agent-readiness` (Windows,
`--only-shell-probes`, `:142-172`) — and both are `needs:` of `Semantic Release`
(`:862`), so a readiness regression blocks the release the same as a routing-parity regression.

Checks currently in the plan (`build_check_plan`, names verified at
`scripts/agent_readiness.py:683-968`): `public-version-{powershell,cmd,pwsh-noprofile,git-bash,wsl,
python-subprocess}`, `public-doctor-{cmd,pwsh-noprofile}`, `public-windows-launcher-quoted-patterns`,
`public-search-advertised-flag-sweep`, `repo-cli-build-warmup`, `repo-doctor`,
`context-render-trust` (the `context_consistency` agent-trust check — `AGENTS.md:135,161`),
`rg-parity-edges`, `broad-generated-scan-guard`, `ast-info-json`, `ast-run-smoke`,
`mcp-context-render-smoke`, `mcp-stdio-protocol-smoke`, `agent-capsule`,
`agent-capsule-mixed-language`, `agent-capsule-hardcases`, `docs-claim-check`. This list drifts with
each release — re-verify with the grep in Provenance below rather than trusting this snapshot.

For what a `tg doctor --json` field actually proves (vs merely install evidence), see
`tensor-grep-diagnostics-and-tooling` — this skill only covers the readiness gate as a **pass/fail
CI evidence surface**, not field-by-field diagnostic interpretation.

---

## Part 5 — Benchmark-gated speed claims (summary; depth lives in the sibling)

Never claim a speedup without a measured line vs the accepted baseline (`AGENTS.md:341`,
`CONTRIBUTING.md:37-42`). The **which-script decision table**, the fair-baseline rule, and the
launcher-attribution/stale-binary-refusal rules live in `tensor-grep-benchmark-and-proof-toolkit` —
load that skill before running or reviewing a benchmark. This skill records only the acceptance
**thresholds**, which are QA-gate facts, not benchmark methodology:

| Gate | Default threshold | Where |
|---|---|---|
| `benchmarks/check_regression.py` CLI | `--max-regression-pct` default **5.0%** slowdown fails | `check_regression.py:64,66` (CLI arg) |
| `perf_guard.check_regressions()` (library default, used when no CLI override) | `max_regression_pct` **10.0%** | `src/tensor_grep/perf_guard.py:48-53` |
| Noise-floor filter | rows with `baseline_time_s < min_baseline_time_s` (CLI default **0.1s**, library default 0.2s) are skipped entirely — avoids false regressions from scheduler jitter on tiny durations | `check_regression.py:70,72`, `perf_guard.py:52,76-77` |
| Sub-10ms hot-query rows | use an **absolute** jitter tolerance in addition to the ratio check (a 5% ratio on a 2ms row is noise) | `AGENTS.md:370` |
| CI blocking gate | `benchmark-regression` job runs a same-runner base-vs-head comparison on every PR and every push to `main`, and is a blocking gate before `Semantic Release`, not advisory | `docs/CI_PIPELINE.md:23,42-43` |

If a candidate is correct but slower: **revert it and record the attempt** in `docs/PAPER.md` so no
future agent (human or model) retries the losing idea — see `tensor-grep-research-methodology`.

---

## Part 6 — How to add a test

### Step 1 — pick the directory (what each one means here)

| Directory | What lives there | Run cost |
|---|---|---|
| `tests/unit/` (155 files as of 2026-07-02) | Fast, isolated; heavy `CliRunner` usage (400+ call sites) — good for flag-parsing/formatter/validator logic, **not sufficient alone for routing changes** (Part 1 point 3) | seconds each |
| `tests/e2e/` (15 files) | Cross-launcher parity (`python-m`/`native`/`bootstrap`), golden/snapshot output, backend/IO contracts, rg characterization, hypothesis property tests, throughput floors | seconds-minutes; some spawn real subprocesses |
| `tests/integration/` (9 files) | Needs real external state — GPU/cuDF, MCP stdio protocol, cross-backend runs, the harness-adoption smoke, `tg orient`/pipeline end-to-end | slow, sometimes GPU-gated |
| `tests/golden/` | Committed golden-output fixtures consumed by `rust_core/tests/test_search_golden.rs`, not itself a pytest dir | n/a |
| `tests/fixtures/`, `tests/schemas/`, `tests/helpers/` | Shared fixture data (`ast_smoke`, `retrieval`), `tg_output.schema.json`, `rg_parity.py` helper (ripgrep binary resolution + `RGContractRow`) | n/a |

`pyproject.toml:34-45` registers `testpaths = ["tests"]` and these markers (apply with
`@pytest.mark.<name>` or a module-level `pytestmark = pytest.mark.<name>`, `--strict-markers` is on so
an unregistered marker is a collection error):

`gpu`, `slow`, `integration`, `acceptance`, `property` (hypothesis-based, see
`tests/e2e/test_reader_props.py`), `characterization` (rg-output parity, see
`tests/e2e/test_ripgrep_parity.py`), `snapshot` (`pytest-snapshot` fixture, see
`tests/e2e/test_output_snapshots.py`), `performance` (see `tests/e2e/test_throughput.py`, which also
stacks `slow` and defines an OS-aware throughput floor that returns `None`/skip on Windows).

### Step 2 — pick the shape

- **Registration/contract change** (new command, new flag, new backend): write the failing test in
  `tests/e2e/test_routing_parity.py` (add the command to `PUBLIC_TOP_LEVEL_COMMANDS` or the flag to
  the relevant sweep) **and** confirm `tests/unit/test_cli_bootstrap.py`'s
  `test_bootstrap_commands_match_source_of_truth` / `test_typer_app_commands_match_source_of_truth` /
  `test_rust_core_uses_source_of_truth` still hold — these three are the existing enforcement of the
  4-site registration rule (`tensor-grep-change-control` Part 3). Do not invent a parallel check; add
  to these first. If the change affects whether a `SearchConfig` field is forwarded to native `argv`,
  refused, or gate-handled, also extend
  `tests/unit/test_native_delegation_field_coverage.py`'s `TestFieldCoverageRatchet` class (AST-derives
  the forwarded set — do not hand-maintain a second list) **and** run `uv run pytest tests/integration -q`
  with the native binary built (Part 1 point 5) — a `tests/unit`-only pass cannot exercise the
  fd-vs-in-process split that a delegation-routing change moves.
- **Precision/heuristic change** (doc-drift, ranking, classification, dedup, or any "flag when X looks
  wrong" feature): a green fixture suite alone is not sufficient evidence (Part 1 point 4). Add fixture
  tests as usual, but before claiming done, run the feature against this repo's own real corpus
  (`docs/` + `src/` for doc-drift, the full repo for ranking/classification) and record the finding
  count and a sample of the top hits — if the count floods (thousands of findings on a repo this size)
  or the top hits are dominated by one noisy category, that is a **defer** signal, not a "tune the
  threshold later" signal.
- **Backend behavior change**: extend `tests/e2e/test_backend_contracts.py`'s `_check_contract` shape
  or add a new `test_*_contract.py` — assert the fail-closed invariant (raises
  `BackendExecutionError`, never returns a clean empty result) per `src/tensor_grep/backends/base.py:7`.
- **Output-format change**: add a case to `tests/e2e/test_output_golden_contract.py`'s
  `GOLDEN_CASES`/`EXACT_OUTPUT_CASES`, or a new `tests/e2e/test_output_snapshots.py` snapshot (normalize
  absolute paths to a placeholder before `snapshot.assert_match` — see the existing path-scrubbing
  logic for why naive string replace breaks on Windows JSON escaping).
- **rg-compatibility claim**: add a case to `tests/e2e/test_ripgrep_parity.py` /
  `tests/e2e/test_rg_parity_edges.py` / `tests/e2e/test_rg_parity_matrix.py` — these call the real
  installed `tg` and `rg` binaries via subprocess (`rg_path`/`sample_log_file` fixtures,
  `tests/conftest.py:38,51`) and diff sorted output lines; this is a **dogfood-shaped** test, not a
  `CliRunner` test, precisely because rg-parity claims must survive the real front door.
- **Docs claim**: add or extend an assertion in the matching `test_*_docs_governance.py` /
  `test_*_docs.py` file (Part 3.2) — do not just edit the doc; the assertion is the enforcement.
  Route through `tensor-grep-docs-and-writing` for which doc owns which contract.
- **Release/workflow/package-manager change**: add or extend a case in
  `tests/unit/test_release_assets_validation.py` calling the relevant `validate_release_assets.py`
  function directly (via `importlib.util`, see the existing pattern at
  `test_release_assets_validation.py:14-25`) — do not only shell out to the script.

### Step 3 — verify the new test actually enforces something

Run it once against the **pre-fix** code and confirm it fails (TDD-first, Part 1 point 1). A test that
was never observed to fail cannot be trusted to catch a regression.

---

## Part 7 — Pre-claim checklist

- [ ] Behavior change has a test that was **observed failing** before the fix.
- [ ] If it touches routing/commands/flags: the real binary was **dogfooded**, not just `CliRunner`.
- [ ] If it touches native delegation/routing: `tests/integration/` was run **with the native `tg`
      binary rebuilt**, not just `tests/unit/` (Part 1 point 5) — a skip is not a pass.
- [ ] If it is a precision/heuristic feature (doc-drift, ranking, classification, dedup): it was run
      against this repo's **real corpus**, not just its fixture suite, and the finding count/top hits
      were eyeballed before claiming done (Part 1 point 4).
- [ ] If it touches a backend/router: the **fail-closed** contract holds (raises, doesn't return empty).
- [ ] If it touches a hot path: a **benchmark line vs the accepted baseline** exists, run through the
      right script (`tensor-grep-benchmark-and-proof-toolkit`), and did not silently trip the CLI's
      5% regression gate.
- [ ] If it touches docs/release/CI contracts: the matching **governance/validator test** was updated,
      not just the doc.
- [ ] `ruff check .` + `ruff format --check --preview .` + `mypy src/tensor_grep` + `pytest -q` (or the
      narrower targeted suite) are green **in the real venv**, not a subagent's self-report.
- [ ] `scripts/agent_readiness.py` / `tg dogfood` run clean if the change touches an agent-critical
      surface (routing, capsule, MCP, docs-claim strings).
- [ ] For release/workflow/package-manager changes: `uv run python scripts/validate_release_assets.py`
      exits 0.

---

## Provenance and maintenance

Volatile facts are dated **2026-07-02, release `v1.17.25`**. Re-verify before relying on them:

| Claim | Re-verify command |
|---|---|
| Total collected tests | `uv run pytest tests --collect-only -q` (tail line; **3184 tests collected** on 2026-07-02) |
| Test file counts (155 unit / 15 e2e / 9 integration) | `Get-ChildItem tests/unit,tests/e2e,tests/integration -Filter test_*.py -Recurse \| Measure-Object` (PowerShell) or `find tests/unit tests/e2e tests/integration -name 'test_*.py' \| wc -l` |
| `CliRunner` usage count in unit tests | `grep -rc CliRunner tests/unit/*.py \| awk -F: '{s+=$2} END{print s}'` |
| pytest markers registered | `grep -n "markers = \[" -A 10 pyproject.toml` |
| `-x` in pytest addopts (and the `--maxfail=0` override) | `grep -n "addopts" -A5 pyproject.toml`; empirically confirm with a scratch `pytest.ini` + two dummy tests |
| Routing-parity contract file/lines | `grep -n "PUBLIC_TOP_LEVEL_COMMANDS\|def test_top_level_help_visible" tests/e2e/test_routing_parity.py` |
| `search-golden-parity` CI job | `grep -n "search-golden-parity" -A25 .github/workflows/ci.yml` |
| Agent-readiness check names | `grep -n 'name="' scripts/agent_readiness.py` |
| Agent-readiness CI jobs | `grep -n "agent-readiness:\|windows-agent-readiness:" -A40 .github/workflows/ci.yml` |
| `validate_release_assets.py` entry points | `grep -n "^def validate_all\|^def main" scripts/validate_release_assets.py` |
| Release-asset validator test size | `wc -l tests/unit/test_release_assets_validation.py` |
| Benchmark regression thresholds | `grep -n "max-regression-pct\|min-baseline-time-s" -A3 benchmarks/check_regression.py`; `grep -n "max_regression_pct\|min_baseline_time_s" src/tensor_grep/perf_guard.py` |
| mypy strict-mode config | `grep -n "\[tool.mypy\]" -A6 pyproject.toml` |
| `--no-sync` rationale | `grep -n "no-sync" -B2 -A2 .github/workflows/ci.yml` |
| Current release tag | `grep -n "^version" pyproject.toml` |
| `tg diff-docs` still deferred/unmerged (2026-07-03) | `git log --oneline --all -- src/tensor_grep/cli/diff_docs.py` (should show only the `wip/diff-docs-precision` commit `90b7042`, nothing on `main`) |
| Native-binary discovery order for parity/integration tests (2026-07-03) | `grep -n "_in_tree_native_tg_candidates\|def resolve_native_tg_binary" -A5 src/tensor_grep/cli/runtime_paths.py` |
| Native-delegation field-coverage ratchet test still present (2026-07-03) | `grep -n "class Test" tests/unit/test_native_delegation_field_coverage.py` |
| `--rank`/`capfd` capture-surface receipt (2026-07-03) | `git show ab717a1 -s --format=%B` (contains both the `#342` refuse-delegation fix and the `#342 follow-up` capture fix in one squashed message) |

If any command above no longer matches, update this skill in the same change — a wrong runbook is
worse than none.
