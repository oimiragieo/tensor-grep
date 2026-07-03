---
name: tensor-grep-change-control
description: Use when about to change, review, merge, or release ANY code in tensor-grep — adding a tg command or search flag, touching a backend/router/pipeline, editing CI/release/docs contracts, merging a PR, or claiming a fix or speedup is done. Encodes the non-negotiable gates (draft-PR-only autonomy, never-trust-a-self-report, no-speed-claim-without-numbers, experimental-until-proven, TDD-first, smallest-change, benchmark-hot-paths, the 4 registration sites, one-merge-per-tick / the push-race, dogfood-the-real-binary, contract-changes-need-validator-tests) and the historical incident behind each.
---

# tensor-grep change control

This is the **gate-and-discipline runbook** for changing `tensor-grep` (the `tg` CLI). It answers: *what must be true before a change is allowed to land, and why.* Every rule here was written in blood — each traces to a real incident that shipped a bug, blocked a release, or wasted CI cycles. Read it before you edit, merge, or claim "done."

`tensor-grep` is described in its own docs as a **benchmark-governed, contract-heavy codebase** (`CONTRIBUTING.md`, `AGENTS.md:15`). "Contract-heavy" means many behaviors are pinned by tests that fail if you drift; "benchmark-governed" means speed claims are gated by measured numbers, not review opinion. Do not optimize by guesswork.

## Who this is for

Two readers at once — write and act to the **lower bound** of each:

- A **Sonnet-class AI** in a cheap autonomous session: you need copy-pasteable commands and hard guardrails so you cannot silently skip a gate.
- A **mid-level human engineer** with zero repo context: you need the *why* and the domain theory so the rule makes sense and you apply it to new cases.

## When to use this skill vs a sibling

| Your task | Use |
|---|---|
| About to edit/merge/release; "is this allowed? what gate applies?" | **this skill** |
| Actually *using* `tg` to navigate a repo (search/defs/callers/orient) | `tensor-grep` (the usage skill) or `code-search-and-retrieval-reference` |
| A `tg` flag/env-var reference | `tensor-grep-config-and-flags` |
| A bug/test-failure to diagnose | `tensor-grep-debugging-playbook` (+ `superpowers:systematic-debugging`) |
| Deep detail on a past incident | `tensor-grep-failure-archaeology` |
| How the internals/contracts are wired | `tensor-grep-architecture-contract` |
| Build / toolchain / env setup | `tensor-grep-build-and-env` |
| Running a benchmark or proving a speed claim | `tensor-grep-benchmark-and-proof-toolkit` |
| Release mechanics / positioning depth | `tensor-grep-release-and-positioning` |
| Validation-suite / CI-gate detail | `tensor-grep-validation-and-qa` |

**No skill routes around change-control.** If a sibling seems to let you skip a gate here, the sibling is wrong — stop and reconcile.

---

## Part 1 — The four UNWRITTEN non-negotiables

These are not in a config file; they are CEO-confirmed law. Breaking one is a process failure even if the code is clean.

### 1. Autonomy is draft-PR-only

**Rule:** Never auto-merge, never admin-merge, never auto-restart a service unattended. Every self-acting behavior ships **default-OFF** and graduates only via: council-verify → dry-run (preview what it would do on real data) → a **conscious flag-flip** by a human. The endpoint of any autonomous fan-out is a **draft PR** a human reviews and clicks merge on.

**Why / incident:** The dogfood follow-up workflow ends every fan-out at a draft PR precisely because a post-build adversarial audit once caught a **HIGH CUDA-fork hazard that 203 passing green tests missed** (`AGENTS.md:212`, `AGENTS.md:271`). Green tests are not a merge signal for autonomous work. A model that merges its own PR removes the one gate that catches what the tests can't.

**Applies to:** any agent orchestration, self-upgrade helper, watcher, or "just merge it" impulse.

### 2. Never trust a self-report

**Rule:** A subagent's or model's "tests pass" / "N green" / "I fixed it" is a **hypothesis** until **external state** confirms it: an exit code, a real-binary dogfood, or a `file:line` that actually resolves. Re-run any validation a subagent claims to have passed.

**Why / incidents:**
- Subagents can assert success without executing (`AGENTS.md:210`). Worktree fan-out branches have **no `.venv`**, so an agent's "tests pass" is literally un-runnable in its own tree — you must re-run pytest/ruff/mypy in the real venv before integrating (`AGENTS.md:269`).
- **Mock-based FFI tests passed GREEN while the real PyO3 bridge was DEAD** — it dropped every forwarded flag and silently fell back to the Python engine. Prove a bridge/FFI change with a **live runtime call into the built extension**, then confirm the flag actually reached `rg` (`AGENTS.md:547`).

**Concrete gate:** For generated/detached code (install scripts, self-upgrade helpers), adversarial-review by **executing** it — `compile()` + `exec()` the generated string and assert behavior (e.g. the checksum gate fires *before* `os.replace`), not substrings (`AGENTS.md:210`).

### 3. No speed / improvement claim without measured numbers

**Rule:** Never claim a speedup, regression, or "improvement" without a measured line **vs the accepted baseline** (not memory). Reject a candidate that is slower — or only "faster" in a microprofile while slower end-to-end — **even if the code is clean**. If a candidate is correct but slower, **revert it and record the attempt** (in `docs/PAPER.md`) so no future agent retries the losing idea.

**Why / incidents & theory:** `rg` (ripgrep) is the **raw cold-text parity baseline**; `ast-grep` is the **structural-search baseline** (`AGENTS.md:133-134`). tg's moat is the agent-native intelligence layer, *not* faster grep — so an unmeasured "it's faster" claim is both unverified and off-strategy. Hard-won architectural truths already in the repo: more caching is **not** always faster; onefile Nuitka binaries are **not** the Windows speed path for plain passthrough; GPU is currently **slower** than CPU (`AGENTS.md:474-490`). Benchmark artifacts must carry `tg_launcher_mode` + `tg_launcher_command_kind` and **refuse stale in-tree binaries by default** — a timing taken through a `.cmd` shim or a stale `rust_core/target/*/tg.exe` is not a claim (`AGENTS.md:152`). Run the *right* benchmark for the area (see `tensor-grep-benchmark-and-proof-toolkit`).

### 4. Experimental-until-proven

**Rule:** GPU, LSP, semantic-search, and provider-backed classify (`cybert`) paths stay **default-OFF and labeled experimental** until correctness **and** speed **and** UX are all proven. Never market an unproven wedge.

**Why / incidents:**
- **GPU** is slower than CPU with no promotion-ready path; the P1 CUDA-PFAC kernel is **PAUSED** (roadmap: fund the CPU moat first — `AGENTS.md:226-234`). Any GPU-requested fallback must surface `gpu_evidence_status = unsupported`, `gpu_proof = false`, `native_gpu_unavailable` (`AGENTS.md:156`). The only *candidate* CUDA wedge is many fixed strings over a large corpus — never single-pattern cold grep.
- **LSP** availability is install evidence only, not proof of working navigation; a row counts as LSP proof only with `lsp_provider_response = true` from a completed request (`AGENTS.md:163`).
- **classify** is deterministic-local by default; provider mode requires `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` and provider failure must fall back **before** loading a tokenizer/model (`AGENTS.md:154`).

---

## Part 2 — The written Operating Rules

From `AGENTS.md` "Operating Rules" (`:168-176`) and `CONTRIBUTING.md`:

1. **Start with a failing test when behavior changes** (TDD-first). See `superpowers:test-driven-development`.
2. **Make the smallest defensible change.**
3. **Run local gates before pushing**, scoped to this desktop unless the user approves heavy validation. Prefer targeted tests locally; use PR/main CI for the full matrices.
4. **Benchmark every hot-path change.**
5. **Reject regressions even if the code is otherwise clean.**
6. **Do not change workflow, release, or docs contracts without updating the validator-backed tests.**
7. Do not `wsl --shutdown` / restart WSL/Docker / reboot the host for "memory cleanup" without explicit user approval — other agents share WSL.

Rule 6 is easy to underrate: if you touch `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `scripts/validate_release_assets.py`, docs contracts, or package-manager assets, the change is **incomplete** until the matching validator test is updated. Read `docs/CI_PIPELINE.md` first — it is the canonical pipeline contract (`AGENTS.md:445-453`).

---

## Part 3 — Registration completeness (the silent-misroute bug class)

**Jargon:** *registration* = an entry that must be added in multiple independent places for a feature to work; miss one and it fails **quietly** (no error, wrong route). This is a universal bug class, not a tg quirk — it also broke a downstream user's billing route.

### Adding a top-level `tg COMMAND` — 4 sites (miss one → silent misroute)

| # | Site | File | Verified anchor |
|---|---|---|---|
| 1 | `KNOWN_COMMANDS` (Python known-command registry) | `src/tensor_grep/cli/commands.py` | `commands.py:9` |
| 2 | `Commands::X` enum variant + dispatch arm (native front door) | `rust_core/src/main.rs` | `enum Commands` at `main.rs:838` |
| 3 | `PUBLIC_TOP_LEVEL_COMMANDS` (parity contract test) | `tests/e2e/test_routing_parity.py` | `:17` (asserted at `:502-503`) |
| 4 | `@app.command` function (Typer entry point) | `src/tensor_grep/cli/main.py` | 37 `@app.command` defs |

### Adding a search flag (`tg search --myflag`) — 2 front doors (miss one → `rg: unrecognized flag` crash for installed users)

| # | Front door | File | Verified anchor |
|---|---|---|---|
| 1 | `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (native allowlist) | `rust_core/src/main.rs` | `:160` |
| 2 | `bootstrap._TG_ONLY_SEARCH_FLAGS` (Python bootstrap allowlist) | `src/tensor_grep/cli/bootstrap.py` | `:23` (checked at `:304`) |

**Why / incident:** The `tg search --rank` flag missed one of the two front doors. CliRunner tests were green — because CliRunner bypasses the bootstrap front door (Part 5) — so the crash shipped and only surfaced for users of the published binary (`AGENTS.md:187-192`). The **CI registration-completeness gate is BLOCKING since v1.17.1 (#282)** and its extractor is comment-aware (`#`-commented entries are not counted as registered) (`AGENTS.md:196`).

**Audit procedure before claiming a registration change is done:**
- `tg callers <registration-function>` lists every *callable* registration in ~1s — **but the call graph cannot see set/list/decorator/dispatch-table registrations** (e.g. `_TG_ONLY_SEARCH_FLAGS` is a *set*, `@router.post` a decorator). `--rank` lived in a set, so `callers` would never have found it.
- So **grep / `tg scan`** the set/decorator/table sites too. Confirm your new entry appears in **all** sites (`AGENTS.md:194`).

---

## Part 4 — Backend fail-closed contract (the silent-wrong-answer bug class)

**Jargon:** a *ComputeBackend* is a search engine implementation (CPU regex, Rust, GPU, ast-grep, …) behind a common interface (`src/tensor_grep/backends/base.py`).

**Rule (`backends/base.py:7`, `AGENTS.md:214-224`):** Every backend **MUST raise `BackendExecutionError` on a real failure** — never return a clean empty / `0-match` result, and never silently swap to an engine that cannot preserve the requested semantics. The search loop catches `BackendExecutionError` to fall back **visibly**; a swallowed failure reaches a coding agent as a trustworthy "no matches" — the one failure a context tool cannot afford.

- **Fail closed** for any flag the fallback cannot preserve — e.g. `--pcre2` through a non-PCRE2 engine must **raise, not swap**.
- If a degraded fallback is *legitimate* (e.g. heuristic classify when the model is down), make it **visible**: set `fallback_reason` (and a distinct `routing_reason`) on the result so JSON/CLI consumers can tell degraded from real. **Never label heuristic output as model output.**
- Validate an untrusted response shape (e.g. a model's class count vs a fixed label list) before indexing, so a mismatch degrades instead of raising an `IndexError` a broad `except` then swallows.

**Why / incidents (this contract is violated repeatedly):** the Rust/PCRE2 bridge ran `--pcre2` through the Python-regex engine (wrong results); the ast-grep OOM mask read a killed subprocess as a clean 0-match; a tree-sitter invalid-query silently returned 0 matches; CyBERT labeled keyword-heuristic hits as real model output. The recurring smell is a **bare `except Exception:` that returns empty or falls to a different engine** (`AGENTS.md:218`). The same rule extends to any router/pipeline that could silently override explicit user intent — e.g. an explicit `--gpu` request quietly routed to CPU must raise `ConfigurationError` or emit a diagnostic (`AGENTS.md:224`; fix shipped in `src/tensor_grep/core/pipeline.py`). A `SafeBackendMixin` + fault-injection conformance CI gate is the planned structural fix so this stops recurring file-by-file.

**Domain note (ripgrep):** tg's default regex path matches invalid UTF-8; **PCRE2 requires valid UTF-8 and transcodes** — which is *why* swapping `--pcre2` to a non-PCRE2 engine changes results, not just performance. `rg` exit code `1` with empty output is a legitimate "no match" (`AGENTS.md:155`); any other non-zero exit code (`2`+) is a **real ripgrep failure** — `ripgrep_backend.py` raises `RuntimeError` on `returncode > 1` at three call sites (`:88`, `:164`, `:199`) and this must not be swallowed as non-fatal.

---

## Part 5 — Dogfood the REAL binary, not CliRunner

**The entry point is `tensor_grep.cli.bootstrap:main_entry`.** It intercepts plain-text searches and forwards them to ripgrep **before the Typer app sees argv**. `CliRunner` invokes the Typer app directly and **bypasses this front door entirely** — so bootstrap-routing bugs are **invisible** to CliRunner unit tests (`AGENTS.md:198-202`).

After adding/changing a flag or command, dogfood the **installed published binary** with the harness at `scripts/dogfood/` (`Dockerfile` + `dogfood_features.py`, both verified present):

```bash
# Against a tg already on PATH (installed wheel):
python scripts/dogfood/dogfood_features.py
# Clean-room via Docker (install the PUBLISHED version, run the real binary):
docker build --build-arg TG_VERSION=<version> -f scripts/dogfood/Dockerfile -t tg-dogfood scripts/dogfood \
  && docker run --rm tg-dogfood
```

**Why / incident:** the `--rank` plain-text crash shipped precisely because CliRunner green-lit it while the real bootstrap route was broken (`CONTRIBUTING.md:73`). See the global skill `dogfood-the-shipped-artifact`.

---

## Part 6 — Verify AI-drafted plans against the real code

Before implementing any AI/subagent-drafted plan, **cite `file:line` for every factual seam claim** (edit locations, registration sites, routing). A claim with no citation is a **hypothesis, not a fact** (`AGENTS.md:204-212`).

**Why / incident:** AI plans reliably identify plausible-but-wrong edit locations (dead code paths, renamed symbols, already-fixed lines). A citation-enforced read-only review caught **5 blockers in two unverified plans in a single session**. After building, run a **post-build adversarial audit** (a distinct stage from planning) until **zero must-fix findings** remain — that zero-finding state is the convergence gate before promoting to a draft PR. See the global skill `verify-plan-against-code`.

**Post-merge gotcha:** apply follow-up fixes **by SYMBOL, not line number** — a squash-merge shifts every line below the change, so "fix `main.py:8468`" is stale the moment anything above it lands. Re-anchor on the function/const name via `tg defs` or grep (`AGENTS.md:548`).

---

## Part 7 — Push discipline & the push-race (one-merge-per-tick)

**The real publish is the `Semantic Release` JOB inside `.github/workflows/ci.yml`**, gated `github.ref == 'refs/heads/main' && github.event_name == 'push'`. `release.yml` is `workflow_dispatch`-only, so a manually-pushed `v*` tag **cannot** bypass semantic-release (`AGENTS.md:500-502`).

That job **compiles native assets before publishing → it runs ~6 minutes**, and that entire window is a race window:

> If **any** other merge lands on `main` during that window — **including a no-release `docs:`/`chore:` PR** — it advances `main`, and the in-flight release's final `git push origin main` (the `chore(release)` version-bump commit) is **rejected non-fast-forward** (`! [rejected] main -> main`), so **that version never publishes**.

**Why / incident:** `v1.17.23` (a security batch, #318) failed to publish because the GPU-pause `docs:` PR (#319) was merged while #318's release job was still compiling assets (`AGENTS.md:504`). The CI concurrency group serializes *runs*, not the *human act of clicking merge* — it is necessary but **insufficient**.

**Discipline = one-merge-per-tick:** merge ONE → wait for its `chore(release): vX [skip ci]` commit on `main` **and** the new version on PyPI → then merge the next. "Safe to interleave" means *after the prior release fully published*, not after its PR CI is green (`AGENTS.md:498`).

**Recovery — do NOT panic-rerun:** the failure self-heals. The next push-to-`main` re-runs `Semantic Release`; because the version is **derived from git tags** (not the failed run's state), it recomputes the correct next version and covers the orphaned `fix:`/`feat:` commit. The fix's *code* was already on `main` — only the publish step was behind. Diagnose by decoding the structured job result first: `gh run view <id> --json jobs` → find `Semantic Release` → `--log-failed`. A `! [rejected] main -> main` line is the push-race signature (`AGENTS.md:506-508`).

Other push rules: don't push from a dirty worktree if `origin/main` moved with unrelated local changes; a branch push / open PR starts **PR CI only** — it is not a release (`AGENTS.md:492-496`).

### Current wall-time is much bigger than "~6 minutes" — size watchers accordingly (re-verified 2026-07-03, v1.19.x receipts)

The **"~6 minutes" figure above (and at `AGENTS.md:507`) is stale** — it describes only the `Semantic Release` job's own runtime (still accurate: ~4-5 min in isolation), not the real race window. The real danger window is **squash-merge lands → `chore(release)` commit successfully pushed to `main`**, because `Semantic Release` cannot even *start* until every job in its `needs:` list finishes (`.github/workflows/ci.yml:862`), and that list now includes a 4-OS `native-build-smoke` matrix plus `benchmark-regression`. Measured against four consecutive real releases (`gh run view <run-id> --json jobs`, PR merge → `chore(release)` commit timestamp → `gh run` job `completedAt`):

| Release | PR / commit | push → `chore(release)` on `main` | push → `publish-pypi` | push → `release-tag-smoke` (final gate) |
|---|---|---|---|---|
| v1.19.0 | #343 `ab717a1` | 25m29s | 43m08s | 47m09s |
| v1.19.1 | #344 `80de0b4` | 22m38s | 40m07s | 44m18s |
| v1.19.2 | #345 `bb5dc59` | 43m39s | 1h01m24s | 1h05m48s |
| v1.19.3 | #346 `6b7b518` | 39m55s | 59m16s | 1h03m06s |

So: **~23-44 min before the version-bump commit is even on `main`**, and **~40-66 min before PyPI/the final release-tag-smoke gate confirms full publish**. Treat "~40 minutes" as the practical minimum wait before checking "did the prior release finish yet", not an upper bound — the slower runs (v1.19.2, v1.19.3) topped an hour.

**Long pole:** `native-build-smoke (macos-15-intel)` (`ci.yml:468-477`) is **consistently the slowest of its own 4-OS matrix** — every run measured: 15m09s, 9m14s, 15m43s, 11m43s (avg ~13 min) vs ~5 min for `ubuntu-latest`/`macos-latest` and ~10 min for `windows-latest`. It was the exact job whose completion unblocked `Semantic Release`'s start in 2 of the 4 runs (down to single-digit seconds: v1.19.0 completed 12:33:45, `Semantic Release` started 12:33:48; v1.19.2 completed 14:37:54, `Semantic Release` started 14:37:57). In the other 2 runs, `benchmark-regression (ubuntu-latest)` finished a couple minutes later and was the actual pole instead — the two jobs alternate as the true bottleneck, so don't tune a watcher to only one of them. After `Semantic Release` publishes, `build-release-native-assets (macos-15-intel, cpu)` (`:1078-1081`) repeats the same slow-OS pattern (9-12 min, once the whole pipeline's single longest job) before `publish-pypi` and `release-tag-smoke` can run.

**Gate a sequential merge-watcher on ABSOLUTE conditions, never "has the tag/commit changed since I started watching":**

- **Correct:** poll `gh pr view <N> --json state -q .state` until it reads `MERGED`, **and independently** poll `gh run list --workflow=ci.yml --branch main --limit 1 --json status,conclusion` until the latest run reports `status == "completed"` **and** `conclusion == "success"` — require the completed state on **2 consecutive polls** (a multi-job run can transiently look "done" while a late job like `release-tag-smoke` is still finishing) before treating the prior release as fully published and starting the next merge.
- **Wrong / deadlocks:** "wait until the release tag / `chore(release)` commit differs from what it was when my watcher launched." If the prior release **already finished publishing between the last time you looked and the moment the watcher actually started** — normal in a fast merge sequence like v1.19.0→v1.19.3 above, all landed inside roughly an hour — the tag is *already* at the target value the instant the watcher begins polling, so a changed-since-launch condition never fires and the watcher hangs forever on an event that already happened. Compare current absolute state against the registry/PR API, never against a snapshot taken at launch time.
- **This v1.19.x sequence is itself the reaffirmation receipt for one-merge-per-tick:** four releases merged one at a time, each waited out to a confirmed publish before the next merge started, and **zero** `! [rejected] main -> main` push-races occurred — contrast the `v1.17.23`/#319 incident above, which is exactly what happens when that wait is skipped. Two PR-CI runs in this window did report `conclusion: "failure"` (`28657702879` — a capfd-vs-stdout test-capture regression, the round-4 ledger's `--rank`-routing item; `28648738456` — a one-off `macos-15-intel` native-asset build failure): both were **ordinary red PR-branch CI**, fixed and re-pushed before merge, not push-races. Triage tell: a red run on a **PR branch** is a normal fix-and-repush gate; a red **`main`**-branch `Semantic Release` push step with `! [rejected]` in its log is the push-race signature and self-heals on the next push (see above — don't panic-rerun).

---

## Part 8 — PR title drives release intent

CI infers the semantic-release bump from the **PR title** (which becomes the squash-merge commit subject). Use conventional titles (`CONTRIBUTING.md:46-51`, `AGENTS.md:526-539`):

| Title prefix | Effect |
|---|---|
| `feat: ...` | minor release |
| `fix: ...` / `perf: ...` / `refactor: ...` | patch release |
| `feat!: ...` / `fix!: ...` | major release |
| `docs:` / `test:` / `chore:` / `ci:` / `build:` | **no release** |

> **`refactor:` cuts a PATCH release** — a frequent surprise. The ground truth is `scripts/validate_pr_title_semver.py` (`_RELEASE_INTENTS`), NOT the prose table in `AGENTS.md` (which omits `refactor:`). If you title a cleanup PR `refactor:` expecting no release, you will ship a version. Re-verify: `grep -A12 _RELEASE_INTENTS scripts/validate_pr_title_semver.py`.

- Use **Squash and merge** for release-bearing PRs so the validated title becomes the `main` subject.
- **Do not manually create release tags** while semantic-release is active.
- A release-bearing fix is **not complete** after only a branch push / open PR / green PR checks. The final report must name: PR, merge commit, main CI run, CodeQL run, released tag, PyPI publish status, and any public installer dogfood result (`AGENTS.md:522`).

---

## Part 9 — Required local validation (run before push)

From `CONTRIBUTING.md:9-14` and `AGENTS.md:297-304`:

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

Fast agent-critical gate (3–5 min) — complements, does not replace, the full gate:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

**The ruff `--preview` trap (this costs a cycle every time it's missed):** CI runs `ruff format --check --preview .`. Running `ruff format` **without** `--preview` is an **active revert** — it rewrites preview-style lines back on disk, so the next CI `ruff format --check --preview` fails on lines you never meant to touch. Always pass `--preview` to `ruff format`; **never** pass it to `ruff check` (preview lint rules like RUF056 produce false failures that don't match CI) (`CONTRIBUTING.md:22`, `AGENTS.md:304`).

**Windows CRLF false-alarm:** `.gitattributes` pins `*.py`/`*.rs` to `eol=lf`. A bare local `ruff format --check` can false-alarm over LF blobs; run `ruff format --preview <files>` (which normalizes) before commit. Audit real endings with `git ls-files --eol` (`git show` smudges output) (`CONTRIBUTING.md:24`, `AGENTS.md:552`).

**Decode the structured CI failure FIRST:** when a CI run fails, open the failing check's **structured JSON output** before reading tracebacks. Theorizing from tracebacks wasted **4 CI cycles** in the June-2026 README-rewrite incident (a README rewrite broke ~14 governance tests + a release-blocker gate); the structured output names the exact gate, file, and line (`CONTRIBUTING.md:26`, `AGENTS.md`).

**Commit-message trap:** `git commit -m "..."` with backticks/`$`/`!` runs shell command substitution and mangles the message. Use `git commit -F <file>` or a single-quoted `<<'EOF'` heredoc (`AGENTS.md:545`).

**Build/toolchain notes:** on this dev box `cargo`/`rustc` are off `PATH` — use `C:/Users/oimir/.cargo/bin/cargo.exe` (or prepend `~/.cargo/bin`). A "hanging" Rust build is almost always slow **LTO that completes** (`maturin develop` ~15s; `--release` is minutes) — do not kill it. For build/env depth see `tensor-grep-build-and-env`.

---

## Part 10 — Pre-merge checklist (run top to bottom)

- [ ] Behavior change → a **failing test written first** (TDD).
- [ ] Change is the **smallest defensible** one.
- [ ] New command → all **4 registration sites** present (Part 3); new search flag → **both front doors** present.
- [ ] Any registration in a **set/decorator/table** confirmed by grep/`tg scan`, not just `tg callers`.
- [ ] Backend/router/pipeline touched → **fail-closed** verified; no bare `except` swallow; degraded fallback carries `fallback_reason`.
- [ ] Flag/command touched → **dogfooded on the real binary** (`scripts/dogfood/`), not CliRunner alone.
- [ ] FFI/PyO3 change → proven with a **live call into the built extension**, not mocks.
- [ ] Hot-path change → **benchmarked vs the accepted baseline**; artifact carries launcher mode/kind; no stale in-tree binary.
- [ ] Contract/CI/docs change → **validator-backed test updated**.
- [ ] Local gate green: `ruff check` + `ruff format --check --preview` + `mypy src/tensor_grep` + `pytest -q`.
- [ ] Subagent claims **re-run in the real venv** — none trusted as-reported.
- [ ] PR title matches intended release bump; **squash-merge** for release-bearing.
- [ ] Merging: prior release **fully published** (its `chore(release)` on `main` + PyPI shows it) before this merge — **one-merge-per-tick**.
- [ ] Autonomous work stops at a **draft PR** — no auto/admin-merge.

---

## Provenance and maintenance

Volatile facts are dated **2026-07-02, release `v1.17.25`**, with a round-4 refresh dated **2026-07-03, release `v1.19.3`** (Part 7 wall-time section + this table's tag/wall-time rows). Re-verify anything below before relying on it:

| Claim | Re-verify command |
|---|---|
| Current release tag | `grep release_docs_current_tag AGENTS.md` (currently `v1.19.3`, `AGENTS.md:19`, as of 2026-07-03) |
| 4 command registration sites | `grep -n KNOWN_COMMANDS src/tensor_grep/cli/commands.py`; `grep -n "enum Commands" rust_core/src/main.rs`; `grep -n PUBLIC_TOP_LEVEL_COMMANDS tests/e2e/test_routing_parity.py`; `grep -cn "@app.command" src/tensor_grep/cli/main.py` |
| 2 search-flag front doors | `grep -n SEARCH_PYTHON_PASSTHROUGH_FLAGS rust_core/src/main.rs`; `grep -n _TG_ONLY_SEARCH_FLAGS src/tensor_grep/cli/bootstrap.py` |
| Fail-closed error type | `grep -n "class BackendExecutionError" src/tensor_grep/backends/base.py` |
| Entry point | `grep -rn "bootstrap:main_entry\|main_entry" pyproject.toml src/tensor_grep/cli/bootstrap.py` |
| Local-validation gate commands | `CONTRIBUTING.md` "Local Validation"; `AGENTS.md` "Required Local Validation" |
| PR-title → release-bump schema | `AGENTS.md` "PR Title And Release Intent"; `CONTRIBUTING.md` "Pull Request and Release Intent" |
| Push-race mechanism + latest receipt | `AGENTS.md` "Release publish is not instant — the push-race" |
| Release wall-time / long-pole job (dated 2026-07-03, v1.19.x) | `gh run list --workflow=ci.yml --branch main --limit 5 --json databaseId,createdAt,updatedAt`, then `gh run view <id> --json jobs -q '.jobs[] | {name, startedAt, completedAt, conclusion}'` — check whether `native-build-smoke (macos-15-intel)` / `build-release-native-assets (macos-15-intel, cpu)` / `benchmark-regression (ubuntu-latest)` are still the slowest `needs:` jobs; re-time push→`chore(release)`→`publish-pypi`→`release-tag-smoke` if the CI matrix has changed since |
| `TG_RG_TIMEOUT_SECONDS` default | `grep -n TG_RG_TIMEOUT_SECONDS src/tensor_grep/cli/subprocess_policy.py` (currently `60.0`, `subprocess_policy.py:44`; the `600` in `AGENTS.md:165` describes the pre-#288 hang) |
| Security round-3 sweep files | `AGENTS.md` "Security Hardening Patterns"; files `src/tensor_grep/cli/{checkpoint_store,session_daemon,session_store,mcp_server}.py` |
| Open round-4 argv item | `AGENTS.md` (native-argv `--` sentinel); `rust_core/src/rg_passthrough.rs` |
| Dogfood harness present | `ls scripts/dogfood/` (`Dockerfile`, `dogfood_features.py`, `README.md`) |

If any command above no longer matches, update this skill in the same change — a wrong runbook is worse than none.
