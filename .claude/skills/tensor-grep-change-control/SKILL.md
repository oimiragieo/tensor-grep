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

**Why / incident:** The dogfood follow-up workflow ends every fan-out at a draft PR precisely because a post-build adversarial audit once caught a **HIGH CUDA-fork hazard that 203 passing green tests missed** (`AGENTS.md:436`, `AGENTS.md:571`). Green tests are not a merge signal for autonomous work. A model that merges its own PR removes the one gate that catches what the tests can't.

**Applies to:** any agent orchestration, self-upgrade helper, watcher, or "just merge it" impulse.

### 2. Never trust a self-report

**Rule:** A subagent's or model's "tests pass" / "N green" / "I fixed it" is a **hypothesis** until **external state** confirms it: an exit code, a real-binary dogfood, or a `file:line` that actually resolves. Re-run any validation a subagent claims to have passed.

**Why / incidents:**
- Subagents can assert success without executing (`AGENTS.md:434`). Worktree fan-out branches have **no `.venv`**, so an agent's "tests pass" is literally un-runnable in its own tree — you must re-run pytest/ruff/mypy in the real venv before integrating (`AGENTS.md:569`).
- **Mock-based FFI tests passed GREEN while the real PyO3 bridge was DEAD** — it dropped every forwarded flag and silently fell back to the Python engine. Prove a bridge/FFI change with a **live runtime call into the built extension**, then confirm the flag actually reached `rg` (`AGENTS.md:901`).

**Concrete gate:** For generated/detached code (install scripts, self-upgrade helpers), adversarial-review by **executing** it — `compile()` + `exec()` the generated string and assert behavior (e.g. the checksum gate fires *before* `os.replace`), not substrings (`AGENTS.md:434`).

### 3. No speed / improvement claim without measured numbers

**Rule:** Never claim a speedup, regression, or "improvement" without a measured line **vs the accepted baseline** (not memory). Reject a candidate that is slower — or only "faster" in a microprofile while slower end-to-end — **even if the code is clean**. If a candidate is correct but slower, **revert it and record the attempt** (in `docs/PAPER.md`) so no future agent retries the losing idea.

**Why / incidents & theory:** `rg` (ripgrep) is the **raw cold-text parity baseline**; `ast-grep` is the **structural-search baseline** (`AGENTS.md:344-345`). tg's moat is the agent-native intelligence layer, *not* faster grep — so an unmeasured "it's faster" claim is both unverified and off-strategy. Hard-won architectural truths already in the repo: more caching is **not** always faster; onefile Nuitka binaries are **not** the Windows speed path for plain passthrough; GPU is currently **slower** than CPU (`AGENTS.md:796-826`). Benchmark artifacts must carry `tg_launcher_mode` + `tg_launcher_command_kind` and **refuse stale in-tree binaries by default** — a timing taken through a `.cmd` shim or a stale `rust_core/target/*/tg.exe` is not a claim (`AGENTS.md:364`). Run the *right* benchmark for the area (see `tensor-grep-benchmark-and-proof-toolkit`).

### 4. Experimental-until-proven

**Rule:** GPU, LSP, semantic-search, and provider-backed classify (`cybert`) paths stay **default-OFF and labeled experimental** until correctness **and** speed **and** UX are all proven. Never market an unproven wedge.

**Why / incidents:**
- **GPU** Phase-0 SHIPPED (v1.75.0-v1.75.4, PRs #593-#597): NVIDIA native assets are built and locally correctness-proven (RTX 4070 `sm_89` / RTX 5070 `sm_120` -- `docs/gpu_crossover.md`), but gated OFF the public release by the CI Actions var `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; GPU asset publishing needs the non-default `native-frontdoor-gpu`) -- Phase 1 is now a reversible flag-flip, not a multi-week rebuild. That flip publishes assets only: no speed crossover is proven vs `rg`/`tg_cpu`, GPU auto-recommendation stays `false`, and the reviewer-gated `public-gpu-proof.yml` speed-crossover gate remains unmet (`docs/CONTRACTS.md:80-82`). Any GPU-requested fallback must surface `gpu_evidence_status = unsupported`, `gpu_proof = false`, `native_gpu_unavailable` (`AGENTS.md:368`). The only *candidate* CUDA wedge is many fixed strings over a large corpus — never single-pattern cold grep.
- **LSP** availability is install evidence only, not proof of working navigation; a row counts as LSP proof only with `lsp_provider_response = true` from a completed request (`AGENTS.md:375`).
- **classify** is deterministic-local by default; provider mode requires `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` and provider failure must fall back **before** loading a tokenizer/model (`AGENTS.md:366`).

### 5. Mandatory adversarial security gate before merge

**Rule:** Every PR touching a security-sensitive surface — `apply_policy`, `mcp_server`, native-argv
construction (`cpu_backend`/`rg_passthrough`), `index_lock`, auth, money, a schema/data migration, or
**native asset / installer / doctor-probe construction** — gets a dedicated **adversarial** review
before merge, in addition to (never instead of) green tests:
"try to actually BREAK this, cite `file:line` for every claim, default to FIX-FIRST when uncertain."
This is a distinct pass from ordinary code review — a reviewer optimizing for "does this look right"
misses what a reviewer optimizing for "how would I exploit this" catches.

**Why / incident (2026-07-08 ultracode session):** this exact gate caught a **real symlink-follow RCE
bypass** on a security PR — `.resolve()` followed the symlink *before* the path-containment check ran,
so a crafted symlink escaped the intended root — and separately a **lock-release TOCTOU** on an
index-lock hardening PR. Both PRs had fully green test suites; neither bug was a test-coverage gap, it
was a missing adversarial pass. Ordinary review (Codex) proved unreliable/WSL-flaky for this role in
practice — run the security-adversarial pass on **Opus or Sonnet-5, never Fable** (Fable 5 ships a
semantic+cumulative cyber-safety classifier that auto-falls-back to Opus mid-turn on vuln-hunting
content, which just adds friction rather than blocking anything — see the global memory
`feedback-fable5-cyber-classifier-audit-on-opus`). **Precedent for the native-asset/installer/
doctor-probe addition:** the v1.75.2/v1.75.3 GPU Phase-0 installer-downgrade PR (#596, P0-5 -- loud
nvidia-to-cpu installer downgrade) was held in draft with an explicit "Opus gate pending before merge"
per its council-reviewed plan before shipping; construction of installer/asset-selection logic and
`doctor` probe payloads is exactly the class of code where a silent wrong-flavor install or a
misleading probe status is a security-relevant integrity failure, not just a UX nit.

**Verdict is binary, not a rubric score:** `SHIP` or `FIX-FIRST(file:line + repro + fix)`. A rubber-stamp
"looks fine" is not a passing verdict — the reviewer must state what they tried to break and why it held.

**Applies to:** any PR in the security-sensitive surface list above; extend the list as new
security-relevant subsystems appear (this is a floor, not an exhaustive enumeration).

### 6. Pin-first ranking gate (C-pin)

**Rule:** Before touching ANY scorer/graph/ranking code (a symbol scorer, a centrality/PageRank pass,
a blast-radius/import-graph traversal, a BM25/RRF weighting), write a test that **pins the CURRENT
ranked output GREEN on base** first. After the change, the ONLY acceptable diff against that pin is
the one the change intended — any OTHER legitimate-entry reorder is a STOP-finding, not a nit to wave
through.

**Why / incident (#709, v1.93.2):** the blast-radius reverse scoring prefilter was changed to exclude
`dynamic_unresolved` literals (a correctness fix, A10/A15). `test_blast_radius_legitimate_dependent_ranking_pin`
locked the pre-change ranked output first, so the fix's actual diff — removing exactly the decoy edges,
with zero reordering of legitimate dependents — was provable, not asserted. Ranking code is the class of
change where "the fix looks right" and "the fix didn't silently reorder something else" are different
claims; only a pin catches the second one.

**Applies to:** any PR touching `repo_map.py`'s scorers, the reverse-import/blast-radius graph, PageRank/
centrality, or any BM25/RRF/dense-fusion weighting.

---

## Part 2 — The written Operating Rules

From `AGENTS.md` "Operating Rules" (`:383-389`) and `CONTRIBUTING.md`:

1. **Start with a failing test when behavior changes** (TDD-first). See `superpowers:test-driven-development`.
2. **Make the smallest defensible change.**
3. **Run local gates before pushing**, scoped to this desktop unless the user approves heavy validation. Prefer targeted tests locally; use PR/main CI for the full matrices.
4. **Benchmark every hot-path change.**
5. **Reject regressions even if the code is otherwise clean.**
6. **Do not change workflow, release, or docs contracts without updating the validator-backed tests.**
7. Do not `wsl --shutdown` / restart WSL/Docker / reboot the host for "memory cleanup" without explicit user approval — other agents share WSL.

Rule 6 is easy to underrate: if you touch `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `scripts/validate_release_assets.py`, docs contracts, or package-manager assets, the change is **incomplete** until the matching validator test is updated. Read `docs/CI_PIPELINE.md` first — it is the canonical pipeline contract (`AGENTS.md:789`).

---

## Part 3 — Registration completeness (the silent-misroute bug class)

**Jargon:** *registration* = an entry that must be added in multiple independent places for a feature to work; miss one and it fails **quietly** (no error, wrong route). This is a universal bug class, not a tg quirk — it also broke a downstream user's billing route.

### Adding a top-level `tg COMMAND` — 4 sites (miss one → silent misroute)

| # | Site | File | Verified anchor |
|---|---|---|---|
| 1 | `KNOWN_COMMANDS` (Python known-command registry) | `src/tensor_grep/cli/commands.py` | `commands.py:9` |
| 2 | `Commands::X` enum variant + dispatch arm (native front door) | `rust_core/src/main.rs` | `enum Commands` at `main.rs:889` |
| 3 | `PUBLIC_TOP_LEVEL_COMMANDS` (parity contract test) | `tests/e2e/test_routing_parity.py` | `:18` (asserted at `:563-564`) |
| 4 | `@app.command` function (Typer entry point) | `src/tensor_grep/cli/main.py` | `grep -c "@app.command" src/tensor_grep/cli/main.py` (re-run before citing a count — it drifts every release; do not trust a stamped number) |

### Adding a search flag (`tg search --myflag`) — 2 front doors (miss one → `rg: unrecognized flag` crash for installed users)

| # | Front door | File | Verified anchor |
|---|---|---|---|
| 1 | `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (native allowlist) | `rust_core/src/main.rs` | `:183` |
| 2 | `bootstrap._TG_ONLY_SEARCH_FLAGS` (Python bootstrap allowlist) | `src/tensor_grep/cli/bootstrap.py` | `:50` (checked at `:355`) |

**Why / incident:** The `tg search --rank` flag missed one of the two front doors. CliRunner tests were green — because CliRunner bypasses the bootstrap front door (Part 5) — so the crash shipped and only surfaced for users of the published binary (`AGENTS.md:405-410`). The **CI registration-completeness gate is BLOCKING since v1.17.1 (#282)** and its extractor is comment-aware (`#`-commented entries are not counted as registered) (`AGENTS.md:414`).

**Audit procedure before claiming a registration change is done:**
- `tg callers <registration-function>` lists every *callable* registration in ~1s — **but the call graph cannot see set/list/decorator/dispatch-table registrations** (e.g. `_TG_ONLY_SEARCH_FLAGS` is a *set*, `@router.post` a decorator). `--rank` lived in a set, so `callers` would never have found it.
- So **grep / `tg scan`** the set/decorator/table sites too. Confirm your new entry appears in **all** sites (`AGENTS.md:412`).

### Registering a new symbol-graph language — 5 seams (miss one → a silent half-integration)

**Jargon:** the *symbol-graph tier* is the deep per-language layer behind `tg defs`/`tg source`/
`tg imports`/`tg callers`/`tg agent` — distinct from plain text search (any language, via `rg`
passthrough). As of this pass 8 languages are registered: python, javascript, typescript, rust, go,
java, php, csharp (`lang_registry.LANGUAGE_REGISTRY`, pinned by
`tests/unit/test_lang_registry.py:84-94`'s `test_language_registry_has_exactly_the_stage2_languages`);
C/C++ are not yet registered.

The registry entry point is `lang_registry.register_language(lang_registry.LanguageSpec(...))`
(`src/tensor_grep/cli/lang_registry.py:118`), called once per language inside
`src/tensor_grep/cli/repo_map.py` (currently 8 calls — `grep -n "lang_registry.register_language(" src/tensor_grep/cli/repo_map.py`, re-run before citing a count, it will grow). A language's extraction
callables can live either inline in `repo_map.py` (python/rust/java) or in a dedicated `lang_<x>.py`
module mirroring `lang_go.py` (go/php/csharp — a separate module avoids an import cycle back into
`repo_map.py`); both are contract-consistent.

Registering the `LanguageSpec` is necessary but not sufficient — 5 more call sites either dispatch on
the registry or hardcode a language list directly, and missing one is a **silent half-integration** (the
language works for some commands and quietly does nothing for others):

| # | Seam | Feeds | File | Verified anchor |
|---|---|---|---|---|
| 1 | `_imports_and_symbols_for_path` | `tg imports` (import list + symbols) | `repo_map.py` | `:6244`; per-language branches at `:6272-6287` |
| 2 | `_imports_with_lines_for_path` | `tg imports`' line-numbered spans | `repo_map.py` | `:6440` — currently dispatches only python/javascript/typescript/rust/java; go/php/csharp fall through to `[]` here today (matches seam 5's exclusion below) |
| 3 | `build_symbol_source_from_map` | `tg source` | `repo_map.py` | `:15799`; per-language branches at `:15837-15844` |
| 4 | `_target_language_for_path` | **MOST-FORGOTTEN.** Feeds the `tg agent` capsule's query-language-vs-target-language confidence gate (`agent_capsule.py`) | `repo_map.py` | `:7367` — the function's own comments say "MOST-FORGOTTEN seam" at each of the 4 newest branches (`:7380`, `:7387`, `:7391`, `:7395`); skip it and the capsule can silently report "no target language" for a real target instead of downgrading confidence honestly |
| 5 | `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` | `tg imports <file>`'s file-dependency-resolution "supported" gate | `repo_map.py` | `:16617` — currently `{python, javascript, typescript, rust, java}`; go/php/csharp are deliberately excluded (their `import_update_target` is still `None`, a tracked follow-up), so those files get an honest `result_incomplete=True` instead of a silently-empty resolved-imports list |

**Fail closed for a missing grammar.** Every language added since the registry existed
(go/java/php/csharp) sets `provenance_when_missing="grammar-missing"` in its `register_language(...)`
call (e.g. `repo_map.py:6090` for go) — never `"regex-heuristic"` — so a file whose tree-sitter grammar
package isn't installed surfaces as an honest `resolution_gaps` entry via
`_language_coverage_gaps_for_universe` (`repo_map.py:7966`, the fail-closed branch at `:8003`) instead
of a silent empty result. This is Part 4's Backend Fail-Closed Contract, applied inside the language
registry (see Part 4's own worked example below).

**Audit procedure:** grep all 5 seams plus the registration call
(`grep -n "lang_registry.register_language\|_imports_and_symbols_for_path\|_imports_with_lines_for_path\|_target_language_for_path\|_SUPPORTED_FILE_DEPENDENCY_LANGUAGES" src/tensor_grep/cli/repo_map.py`),
then widen `tests/unit/test_lang_registry.py:84-94` (`test_language_registry_has_exactly_the_stage2_languages`) to include the new language — this is a **pin test for registry membership** (same
principle as Part 1 Rule 6's ranking pin, applied to a set instead of a ranked list): it fails loud the
moment a rebase silently drops a language (see Part 7's sequential-drain corollary below).

---

## Part 4 — Backend fail-closed contract (the silent-wrong-answer bug class)

**Jargon:** a *ComputeBackend* is a search engine implementation (CPU regex, Rust, GPU, ast-grep, …) behind a common interface (`src/tensor_grep/backends/base.py`).

**Rule (`backends/base.py:7`, `AGENTS.md:438-448`):** Every backend **MUST raise `BackendExecutionError` on a real failure** — never return a clean empty / `0-match` result, and never silently swap to an engine that cannot preserve the requested semantics. The search loop catches `BackendExecutionError` to fall back **visibly**; a swallowed failure reaches a coding agent as a trustworthy "no matches" — the one failure a context tool cannot afford.

- **Fail closed** for any flag the fallback cannot preserve — e.g. `--pcre2` through a non-PCRE2 engine must **raise, not swap**.
- If a degraded fallback is *legitimate* (e.g. heuristic classify when the model is down), make it **visible**: set `fallback_reason` (and a distinct `routing_reason`) on the result so JSON/CLI consumers can tell degraded from real. **Never label heuristic output as model output.**
- Validate an untrusted response shape (e.g. a model's class count vs a fixed label list) before indexing, so a mismatch degrades instead of raising an `IndexError` a broad `except` then swallows.

**Why / incidents (this contract is violated repeatedly):** the Rust/PCRE2 bridge ran `--pcre2` through the Python-regex engine (wrong results); the ast-grep OOM mask read a killed subprocess as a clean 0-match; a tree-sitter invalid-query silently returned 0 matches; CyBERT labeled keyword-heuristic hits as real model output. The recurring smell is a **bare `except Exception:` that returns empty or falls to a different engine** (`AGENTS.md:442`). The same rule extends to any router/pipeline that could silently override explicit user intent — e.g. an explicit `--gpu` request quietly routed to CPU must raise `ConfigurationError` or emit a diagnostic (`AGENTS.md:448`; fix shipped in `src/tensor_grep/core/pipeline.py`). A `SafeBackendMixin` + fault-injection conformance CI gate is the planned structural fix so this stops recurring file-by-file.

**Concrete example outside `backends/` (the same contract, a different subsystem):** the multi-language
symbol registry (Part 3) applies this identically. `LanguageSpec.provenance_when_missing` must be
`"grammar-missing"` (never `"regex-heuristic"`) for any language with no text-heuristic fallback —
go/java/php/csharp all set it this way in their `register_language(...)` call (e.g. `repo_map.py:6090`)
— so `_language_coverage_gaps_for_universe` (`repo_map.py:7966`) can tell "grammar not installed, fail
closed" apart from "language has a regex fallback, degrade quietly" at its branch on line `:8003`. Get
this backwards (label a no-fallback language `"regex-heuristic"`) and a grammar-missing file would read
as a clean, silent "zero symbols found" instead of an honest gap — precisely the failure class this Part
exists to prevent, just reached through a registry field instead of a bare `except`.

**Domain note (ripgrep):** tg's default regex path matches invalid UTF-8; **PCRE2 requires valid UTF-8 and transcodes** — which is *why* swapping `--pcre2` to a non-PCRE2 engine changes results, not just performance. `rg` exit code `1` with empty output is a legitimate "no match" (`AGENTS.md:367`); exit code `2` with matches already parsed is treated as **partial** (kept + `result_incomplete=True`, the "surface degraded, don't discard" posture this Part argues for, not a swallow); any other case (`2`+ with nothing parsed, or `>2`) is a **real ripgrep failure** — `ripgrep_backend.py` raises `BackendExecutionError` (not a bare `RuntimeError`) on `returncode > 1 and not partial` at three call sites (`:126`, `:297`, `:413`) and this must not be swallowed as non-fatal.

---

## Part 5 — Dogfood the REAL binary, not CliRunner

**The entry point is `tensor_grep.cli.bootstrap:main_entry`.** It intercepts plain-text searches and forwards them to ripgrep **before the Typer app sees argv**. `CliRunner` invokes the Typer app directly and **bypasses this front door entirely** — so bootstrap-routing bugs are **invisible** to CliRunner unit tests (`AGENTS.md:422-427`).

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

Before implementing any AI/subagent-drafted plan, **cite `file:line` for every factual seam claim** (edit locations, registration sites, routing). A claim with no citation is a **hypothesis, not a fact** (`AGENTS.md:428-436`).

**Why / incident:** AI plans reliably identify plausible-but-wrong edit locations (dead code paths, renamed symbols, already-fixed lines). A citation-enforced read-only review caught **5 blockers in two unverified plans in a single session**. After building, run a **post-build adversarial audit** (a distinct stage from planning) until **zero must-fix findings** remain — that zero-finding state is the convergence gate before promoting to a draft PR. See the global skill `verify-plan-against-code`.

**Post-merge gotcha:** apply follow-up fixes **by SYMBOL, not line number** — a squash-merge shifts every line below the change, so "fix `main.py:8468`" is stale the moment anything above it lands. Re-anchor on the function/const name via `tg defs` or grep (`AGENTS.md:902`).

---

## Part 7 — Push discipline & the push-race (one-merge-per-tick)

**The real publish is the `Semantic Release` JOB inside `.github/workflows/ci.yml`**, gated `github.ref == 'refs/heads/main' && github.event_name == 'push'`. `release.yml` is `workflow_dispatch`-only, so a manually-pushed `v*` tag **cannot** bypass semantic-release (`AGENTS.md:838`).

That job **compiles native assets before publishing → it runs ~6 minutes**, and that entire window is a race window:

> If **any** other merge lands on `main` during that window — **including a no-release `docs:`/`chore:` PR** — it advances `main`, and the in-flight release's final `git push origin main` (the `chore(release)` version-bump commit) is **rejected non-fast-forward** (`! [rejected] main -> main`), so **that version never publishes**.

**Why / incident:** `v1.17.23` (a security batch, #318) failed to publish because the GPU-pause `docs:` PR (#319) was merged while #318's release job was still compiling assets (`AGENTS.md:840`). The CI concurrency group serializes *runs*, not the *human act of clicking merge* — it is necessary but **insufficient**.

**Discipline = one-merge-per-tick:** merge ONE → wait for its `chore(release): vX [skip ci]` commit on `main` **and** the new version on PyPI → then merge the next. "Safe to interleave" means *after the prior release fully published*, not after its PR CI is green (`AGENTS.md:834`).

**Recovery — do NOT panic-rerun:** the failure self-heals. The next push-to-`main` re-runs `Semantic Release`; because the version is **derived from git tags** (not the failed run's state), it recomputes the correct next version and covers the orphaned `fix:`/`feat:` commit. The fix's *code* was already on `main` — only the publish step was behind. Diagnose by decoding the structured job result first: `gh run view <id> --json jobs` → find `Semantic Release` → `--log-failed`. A `! [rejected] main -> main` line is the push-race signature (`AGENTS.md:844`).

**A second, DIFFERENT release-failure shape does NOT self-heal (C-release-flake) — do not apply the "just push again" recovery to it blind.** A flaky `needs:`-list job (e.g. a timing-sensitive lock-concurrency test, a transient dependency-install flake) can make `Semantic Release` report `skipped` rather than `failure` — no tag, no `chore(release)` commit, PyPI unchanged. This is **not** the push-race shape (no `! [rejected]` line) and it will **not** resolve itself on the next ordinary push, because nothing about the flaky job's cause changes between runs. Recovery here is `gh run rerun --failed` on the SAME run (re-executes only the failed job, not the whole pipeline) — receipts: v1.76.9/#612-613 (a timing-flaky heartbeat test widened + rerun), v1.92.2/#701 (the index-lock concurrency test rewritten to a scheduler-independent Event-handshake contract after 2 releases of flaking). **Tell the two shapes apart by reading the job conclusion, not by symptom-guessing:** `! [rejected] main -> main` in the `Semantic Release` job's own log = push-race, self-heals; a `skipped` conclusion with no rejection line = a `needs:`-job flake, needs `gh run rerun --failed`. Cross-link: `tensor-grep-debugging-playbook` §2.

Other push rules: don't push from a dirty worktree if `origin/main` moved with unrelated local changes; a branch push / open PR starts **PR CI only** — it is not a release (`AGENTS.md:830-832`).

### Rapid-window batch-merge — several already-green releasing PRs in one window (C-batch)

**Individually-green, releasing PRs may merge ~15-20s apart in one gate-open window and still produce
ONE combined, fully-published release** — this is not a violation of one-merge-per-tick, it is the same
discipline applied to a batch instead of a single PR. The tell that distinguishes a safe batch-merge from
a push-race collision: **only the LAST run in the window needs to go fully green.** Intermediate runs
that report `cancelled` (the CI concurrency group superseding an in-flight run with a newer push) or even
`failure` on their own push step are benign IF the final run in the sequence completes the full pipeline
and publishes — the cumulative state is validated by whichever run actually finishes on top.

**Receipt (v1.93.0, #703→#706):** four independently-green PRs merged in a tight window; run
`29890576036` shows a rejected-only intermediate push (superseded, not a real failure); run
`29890612228` completed and published — the combined result was ONE release, `v1.93.0`, covering all
four PRs' commits, with zero actual push-race damage. Earlier precedent: v1.91.0 (a similar rapid
4-in-a-row window).

**How this differs from the accidental push-race (do not confuse the two):** the v1.17.23/#318 incident
(Part 1 above) was an UNPLANNED collision — a `docs:` PR merged mid-flight killed a security batch's
publish, and that version never came out at all. The v1.93.0/#703-706 sequence was a DELIBERATE,
monitored batch where every intermediate `cancelled`/rejected state was expected and the operator
confirmed the final run's full green before declaring the batch shipped. **The discipline is: know
which one you're doing** — an accidental collision is a bug to prevent (one-merge-per-tick); a
monitored rapid batch is a valid pattern IF you watch the final run to completion, not just each
individual PR's own CI.

### Build-vs-merge decoupling -- the push-race gates MERGE, not BUILD

**One-merge-per-tick governs when a PR may *merge*, not when work on it may *start*.** A PR sequenced "after vX publishes" purely for a **code-collision** reason (it touches the same file as the in-flight release, or it wants vX's already-merged code as its base) may **branch and build off the just-merged `main` in parallel with the in-flight release** -- draft it, implement it, run PR-branch CI, get it fully review-ready -- while the release job is still compiling native assets. Only the final **merge** into `main` stays push-race-gated: wait for the prior `chore(release)` commit + PyPI to confirm publish before clicking merge, not before starting work. Across a multi-PR campaign this saves ~40 min/PR of pure idle waiting (see the wall-time table below for how long a full publish actually takes). Named patterns for the same underlying principle elsewhere: **merge-queue / speculative CI** (validate speculatively against a predicted merge base, re-validate only if the base actually changed), **release-train** (work lands continuously; only the train's scheduled departure is gated), and **build-once-promote-everywhere** (one build artifact is promoted through successive gates rather than rebuilt at each one).

### Sequential-drain union-rebase — N PRs that touch the same shared file

When several parallel PRs each edit the SAME shared file — e.g. a registry test's asserted-membership
set, a pyproject optional-dependency extra, `uv.lock` — merging them still follows one-merge-per-tick,
but each merge is also a **rebase**, not just a fast-forward: drain PRs one at a time, rebase the next
one onto the branch the prior merge just landed, and **union** the assertions rather than taking either
side. For a language-registry-style set (Part 3), that means the rebased test must assert the FULL
current membership (every previously-shipped entry plus the new one), never just "my entry plus
whatever my branch already had."

**A CLEAN rebase (no conflict marker) is NOT proof the union happened correctly.** Git can auto-merge a
text region without a marker and still silently drop a line neither side technically "conflicted" on —
e.g. an import folded into the wrong place, or a set literal that resolves to only one branch's members
instead of both. The only reliable check is **re-running the test suite after every rebase**, not
reading the diff: a dropped import surfaces immediately as `ImportError` at collection time, which a
clean-looking diff will not show you.

Concretely, for this repo's own language-registry campaign, that means re-running
`tests/unit/test_lang_registry.py` (in particular `:84-94`,
`test_language_registry_has_exactly_the_stage2_languages`) after each rebase in the sequence, not just
once at the end — the whole point of a pin test (Part 1 Rule 6) is that it only protects you if it
actually runs against the post-rebase state.

### Current wall-time is much bigger than "~6 minutes" — size watchers accordingly (re-verified 2026-07-03, v1.19.x receipts)

The **"~6 minutes" figure above (and at `AGENTS.md:838`) is stale** — it describes only the `Semantic Release` job's own runtime (still accurate: ~4-5 min in isolation), not the real race window. The real danger window is **squash-merge lands → `chore(release)` commit successfully pushed to `main`**, because `Semantic Release` cannot even *start* until every job in its `needs:` list finishes (`.github/workflows/ci.yml:943`), and that list now includes a 4-OS `native-build-smoke` matrix plus `benchmark-regression`. Measured against four consecutive real releases (`gh run view <run-id> --json jobs`, PR merge → `chore(release)` commit timestamp → `gh run` job `completedAt`):

| Release | PR / commit | push → `chore(release)` on `main` | push → `publish-pypi` | push → `release-tag-smoke` (final gate) |
|---|---|---|---|---|
| v1.19.0 | #343 `ab717a1` | 25m29s | 43m08s | 47m09s |
| v1.19.1 | #344 `80de0b4` | 22m38s | 40m07s | 44m18s |
| v1.19.2 | #345 `bb5dc59` | 43m39s | 1h01m24s | 1h05m48s |
| v1.19.3 | #346 `6b7b518` | 39m55s | 59m16s | 1h03m06s |

So: **~23-44 min before the version-bump commit is even on `main`**, and **~40-66 min before PyPI/the final release-tag-smoke gate confirms full publish**. Treat "~40 minutes" as the practical minimum wait before checking "did the prior release finish yet", not an upper bound — the slower runs (v1.19.2, v1.19.3) topped an hour. **This table's numbers are still NOT re-measured as of this pass (v1.95.0) — they remain the v1.19.x historical sample; treat them as illustrative of the SHAPE of the wait (a 4-OS native-build matrix is the long pole), not as a current SLA.**

**Long pole:** `native-build-smoke (macos-15-intel)` (`ci.yml:549-558`) is **consistently the slowest of its own 4-OS matrix** — every run measured: 15m09s, 9m14s, 15m43s, 11m43s (avg ~13 min) vs ~5 min for `ubuntu-latest`/`macos-latest` and ~10 min for `windows-latest`. It was the exact job whose completion unblocked `Semantic Release`'s start in 2 of the 4 runs (down to single-digit seconds: v1.19.0 completed 12:33:45, `Semantic Release` started 12:33:48; v1.19.2 completed 14:37:54, `Semantic Release` started 14:37:57). In the other 2 runs, `benchmark-regression (ubuntu-latest)` finished a couple minutes later and was the actual pole instead — the two jobs alternate as the true bottleneck, so don't tune a watcher to only one of them. After `Semantic Release` publishes, `build-release-native-assets (macos-15-intel, cpu)` (`:1159-1162`) repeats the same slow-OS pattern (9-12 min, once the whole pipeline's single longest job) before `publish-pypi` and `release-tag-smoke` can run.

**Gate a sequential merge-watcher on ABSOLUTE conditions, never "has the tag/commit changed since I started watching":**

- **Correct:** poll `gh pr view <N> --json state -q .state` until it reads `MERGED`, **and independently** poll `gh run list --workflow=ci.yml --branch main --limit 1 --json status,conclusion` until the latest run reports `status == "completed"` **and** `conclusion == "success"` — require the completed state on **2 consecutive polls** (a multi-job run can transiently look "done" while a late job like `release-tag-smoke` is still finishing) before treating the prior release as fully published and starting the next merge.
- **Wrong / deadlocks:** "wait until the release tag / `chore(release)` commit differs from what it was when my watcher launched." If the prior release **already finished publishing between the last time you looked and the moment the watcher actually started** — normal in a fast merge sequence like v1.19.0→v1.19.3 above, all landed inside roughly an hour — the tag is *already* at the target value the instant the watcher begins polling, so a changed-since-launch condition never fires and the watcher hangs forever on an event that already happened. Compare current absolute state against the registry/PR API, never against a snapshot taken at launch time.
- **This v1.19.x sequence is itself the reaffirmation receipt for one-merge-per-tick:** four releases merged one at a time, each waited out to a confirmed publish before the next merge started, and **zero** `! [rejected] main -> main` push-races occurred — contrast the `v1.17.23`/#319 incident above, which is exactly what happens when that wait is skipped. Two PR-CI runs in this window did report `conclusion: "failure"` (`28657702879` — a capfd-vs-stdout test-capture regression, the round-4 ledger's `--rank`-routing item; `28648738456` — a one-off `macos-15-intel` native-asset build failure): both were **ordinary red PR-branch CI**, fixed and re-pushed before merge, not push-races. Triage tell: a red run on a **PR branch** is a normal fix-and-repush gate; a red **`main`**-branch `Semantic Release` push step with `! [rejected]` in its log is the push-race signature and self-heals on the next push (see above — don't panic-rerun).

---

## Part 8 — PR title drives release intent

CI infers the semantic-release bump from the **PR title** (which becomes the squash-merge commit subject). Use conventional titles (`CONTRIBUTING.md:46-51`, `AGENTS.md:880-889`):

| Title prefix | Effect |
|---|---|
| `feat: ...` | minor release |
| `fix: ...` / `perf: ...` / `refactor: ...` | patch release |
| `feat!: ...` / `fix!: ...` | major release |
| `docs:` / `test:` / `chore:` / `ci:` / `build:` | **no release** |

> **`refactor:` cuts a PATCH release** — a frequent surprise. The ground truth is `scripts/validate_pr_title_semver.py` (`_RELEASE_INTENTS`), NOT the prose table in `AGENTS.md` (which omits `refactor:`). If you title a cleanup PR `refactor:` expecting no release, you will ship a version. Re-verify: `grep -A12 _RELEASE_INTENTS scripts/validate_pr_title_semver.py`.

- Use **Squash and merge** for release-bearing PRs so the validated title becomes the `main` subject.
- **Do not manually create release tags** while semantic-release is active.
- A release-bearing fix is **not complete** after only a branch push / open PR / green PR checks. The final report must name: PR, merge commit, main CI run, CodeQL run, released tag, PyPI publish status, and any public installer dogfood result (`AGENTS.md:862`).

---

## Part 9 — Required local validation (run before push)

From `CONTRIBUTING.md:9-14` and `AGENTS.md:597-601`:

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

**Writing a test for a hang-class bug** (ReDoS, deadlock, lock-race, unbounded subprocess/loop)?
Wrap it per the global skill `anti-hang-test-protocol` first — an unwrapped red-phase test against
un-fixed code can hang the runner itself and look indistinguishable from a stuck build.

Fast agent-critical gate (3–5 min) — complements, does not replace, the full gate:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

**The ruff `--preview` trap (this costs a cycle every time it's missed):** CI runs `ruff format --check --preview .`. Running `ruff format` **without** `--preview` is an **active revert** — it rewrites preview-style lines back on disk, so the next CI `ruff format --check --preview` fails on lines you never meant to touch. Always pass `--preview` to `ruff format`; **never** pass it to `ruff check` (preview lint rules like RUF056 produce false failures that don't match CI) (`CONTRIBUTING.md:22`, `AGENTS.md:604`).

**Windows CRLF false-alarm:** `.gitattributes` pins `*.py`/`*.rs` to `eol=lf`. A bare local `ruff format --check` can false-alarm over LF blobs; run `ruff format --preview <files>` (which normalizes) before commit. Audit real endings with `git ls-files --eol` (`git show` smudges output) (`CONTRIBUTING.md:24`, `AGENTS.md:906`).

**Editing a CRLF-committed file in text mode flips every line ending.** `.gitattributes` only forces
`*.py`/`*.rs` to `eol=lf` (`git cat-file blob origin/main:.gitattributes` — two lines, nothing else
pinned); other committed files keep whatever line ending they were checked in with. `.github/workflows/
ci.yml`, for one, is genuinely CRLF on `origin/main` (verify: `git cat-file blob origin/main:.github/
workflows/ci.yml | od -c | grep -c '\\r'` — non-zero). Opening a CRLF file with a Python text-mode write
(`open(path, newline="\n")`, or any text-mode write without `newline=""`) silently normalizes every line
ending on save, turning an N-line intended change into a whole-file diff of thousands of lines. Fix:
read and write in **binary** mode (`rb`/`wb`) and byte-replace, preserving the file's existing `\r\n`.
Before editing any non-`.py`/non-`.rs` file programmatically, check its actual line ending first — do
not assume LF, and do not assume every CRLF-shaped file stays CRLF forever (re-verify per file; this is
not a fixed list — `uv.lock`, for instance, is currently LF-only on `origin/main`, so don't assume it
needs this treatment without checking).

**A raw `uv lock` churns unrelated lines — hand-splice a new dependency instead.** Running the bare `uv
lock` tool tends to reformat GPU/CUDA marker expressions across the whole file (a local-vs-CI `uv`
version mismatch), turning a one-dependency addition into a ~280-line diff that is mostly noise and hard
to review. For a single new dependency, hand-splice only its own `[[package]]` block (kept alphabetical)
plus its `requires-dist` / optional-dependency references. Verify the result with a local run of the
same check the `Dependency & License Audit` job (`.github/workflows/audit.yml:12`) runs on every
dependency-touching PR — its exact line is `uv export --format requirements.txt --all-extras
--no-emit-project --output-file "$RUNNER_TEMP/python-audit-requirements.txt" --locked`
(`audit.yml:51`); locally, drop the `--output-file` redirect and just confirm exit `0`:

```powershell
uv export --format requirements.txt --all-extras --no-emit-project --locked
```

**Decode the structured CI failure FIRST:** when a CI run fails, open the failing check's **structured JSON output** before reading tracebacks. Theorizing from tracebacks wasted **4 CI cycles** in the June-2026 README-rewrite incident (a README rewrite broke ~14 governance tests + a release-blocker gate); the structured output names the exact gate, file, and line (`CONTRIBUTING.md:26`, `AGENTS.md`).

**Commit-message trap:** `git commit -m "..."` with backticks/`$`/`!` runs shell command substitution and mangles the message. Use `git commit -F <file>` or a single-quoted `<<'EOF'` heredoc (`AGENTS.md:899`).

**Build/toolchain notes:** on this dev box `cargo`/`rustc` are off `PATH` — use `C:/Users/oimir/.cargo/bin/cargo.exe` (or prepend `~/.cargo/bin`). A "hanging" Rust build is almost always slow **LTO that completes** (`maturin develop` ~15s; `--release` is minutes) — do not kill it. For build/env depth see `tensor-grep-build-and-env`.

---

## Part 10 — Pre-merge checklist (run top to bottom)

- [ ] Behavior change → a **failing test written first** (TDD).
- [ ] Change is the **smallest defensible** one.
- [ ] New command → all **4 registration sites** present (Part 3); new search flag → **both front doors** present; new symbol-graph language → all **5 seams** present (Part 3).
- [ ] Any registration in a **set/decorator/table** confirmed by grep/`tg scan`, not just `tg callers`.
- [ ] Backend/router/pipeline touched → **fail-closed** verified; no bare `except` swallow; degraded fallback carries `fallback_reason`.
- [ ] Touches a scorer/graph/ranking surface → a **pin test locked the pre-change ranked output** first; only the intended diff shows (Part 1 Rule 6, C-pin).
- [ ] Touches `apply_policy`/`mcp_server`/native-argv/`index_lock`/auth/money/a migration → a dedicated **adversarial "try to break it"** security pass ran and returned `SHIP` (Part 1 Rule 5) — not just green functional tests.
- [ ] Flag/command touched → **dogfooded on the real binary** (`scripts/dogfood/`), not CliRunner alone.
- [ ] FFI/PyO3 change → proven with a **live call into the built extension**, not mocks.
- [ ] Hot-path change → **benchmarked vs the accepted baseline**; artifact carries launcher mode/kind; no stale in-tree binary.
- [ ] Contract/CI/docs change → **validator-backed test updated**.
- [ ] Multiple PRs touch the SAME shared file (e.g. a registry test, `uv.lock`) → drained sequentially, each rebased onto the prior with a **UNIONED** assertion, test suite **re-run after every rebase** (Part 7, C4).
- [ ] Local gate green: `ruff check` + `ruff format --check --preview` + `mypy src/tensor_grep` + `pytest -q`.
- [ ] Subagent claims **re-run in the real venv** — none trusted as-reported.
- [ ] PR title matches intended release bump; **squash-merge** for release-bearing.
- [ ] Merging: prior release **fully published** (its `chore(release)` on `main` + PyPI shows it) before this merge — **one-merge-per-tick**.
- [ ] Autonomous work stops at a **draft PR** — no auto/admin-merge.

---

## Provenance and maintenance

Volatile facts are dated **2026-07-02, release `v1.17.25`**, with a round-4 refresh dated **2026-07-03, release `v1.19.3`** (Part 7 wall-time section + this table's tag/wall-time rows), a **2026-07-08, release `v1.49.3`** touch-up (Part 1 Rule 5 / Part 10 adversarial-security-gate addition — the Part 7 wall-time numbers themselves are NOT re-measured at v1.49.3, treat them as an illustrative historical sample, not a current SLA), a **2026-07-16, release `v1.78.1`** fix (the stale `37 @app.command` count, actual 44, replaced with a re-verify command instead of a stamped number), a **2026-07-22, release `v1.93.2`** addition (Part 1 Rule 6 pin-first ranking gate / C-pin, #709; Part 7 rapid-window batch-merge / C-batch, #703-706; Part 7 second release-failure shape / C-release-flake, v1.76.9/#612-613 and v1.92.2/#701 — the Part 7 wall-time numbers again NOT re-measured in this pass), and a **2026-07-23, release `v1.95.0`** refresh (Part 3 gained a 3rd registration table — the symbol-graph language registry's 5 seams, `lang_registry.register_language` + `repo_map.py` citations; Part 4 gained a grammar-missing fail-closed worked example; Part 7 gained the sequential-drain union-rebase corollary (C4); Part 9 gained the CRLF-binary-preserve edit landmine and the `uv.lock` hand-splice discipline (C1/C2); and every pre-existing `file:line` citation into Rust/Python source, test, and workflow files in this skill was re-walked against `origin/main` and repointed where drifted — several had moved 20-300 lines since the last pass (e.g. `main.rs`'s `enum Commands` 838→889, the `Semantic Release` job's `needs:` list in `ci.yml` 862→943, `ripgrep_backend.py`'s fail-closed raise sites 88/164/199→126/297/413, which ALSO now raise `BackendExecutionError` there instead of a bare `RuntimeError`). AGENTS.md's own prose citations were re-pointed too (its "Current Handoff" section grew substantially since the last pass), but AGENTS.md is itself mid-refresh in this same campaign, so treat any `AGENTS.md:NNN` citation below as good only as of `v1.95.0` — re-grep by symbol/phrase, don't trust the number blind, before citing it in a future pass. The Part 7 wall-time numbers themselves are STILL not re-measured in this pass — they remain the v1.19.x historical sample. Re-verify anything below before relying on it:

| Claim | Re-verify command |
|---|---|
| Current release tag | `grep release_docs_current_tag AGENTS.md` (was `v1.95.0` as of 2026-07-23 — re-check, it moves every release) |
| Mandatory adversarial security gate (Part 1 Rule 5) | `feedback-fable5-cyber-classifier-audit-on-opus` + `tensor-grep-campaign-orchestration-playbook-2026-07-08` (global memory) — no single code anchor, this is a process rule; verify it is still being applied by checking recent security-touching PR descriptions for a stated adversarial-review verdict |
| 4 command registration sites | `grep -n KNOWN_COMMANDS src/tensor_grep/cli/commands.py`; `grep -n "enum Commands" rust_core/src/main.rs`; `grep -n PUBLIC_TOP_LEVEL_COMMANDS tests/e2e/test_routing_parity.py`; `grep -cn "@app.command" src/tensor_grep/cli/main.py` |
| 2 search-flag front doors | `grep -n SEARCH_PYTHON_PASSTHROUGH_FLAGS rust_core/src/main.rs`; `grep -n _TG_ONLY_SEARCH_FLAGS src/tensor_grep/cli/bootstrap.py` |
| 5 language-registration seams | `grep -n "lang_registry.register_language\|_imports_and_symbols_for_path\|_imports_with_lines_for_path\|_target_language_for_path\|_SUPPORTED_FILE_DEPENDENCY_LANGUAGES" src/tensor_grep/cli/repo_map.py`; `grep -n "LANGUAGE_REGISTRY\|register_language" src/tensor_grep/cli/lang_registry.py` |
| Fail-closed error type | `grep -n "class BackendExecutionError" src/tensor_grep/backends/base.py` |
| Entry point | `grep -rn "bootstrap:main_entry\|main_entry" pyproject.toml src/tensor_grep/cli/bootstrap.py` |
| Local-validation gate commands | `CONTRIBUTING.md` "Local Validation"; `AGENTS.md` "Required Local Validation" |
| PR-title → release-bump schema | `AGENTS.md` "PR Title And Release Intent"; `CONTRIBUTING.md` "Pull Request and Release Intent" |
| Push-race mechanism + latest receipt | `AGENTS.md` "Release publish is not instant — the push-race" |
| Release wall-time / long-pole job (dated 2026-07-03, v1.19.x) | `gh run list --workflow=ci.yml --branch main --limit 5 --json databaseId,createdAt,updatedAt`, then `gh run view <id> --json jobs -q '.jobs[] | {name, startedAt, completedAt, conclusion}'` — check whether `native-build-smoke (macos-15-intel)` / `build-release-native-assets (macos-15-intel, cpu)` / `benchmark-regression (ubuntu-latest)` are still the slowest `needs:` jobs (all 3 confirmed still present as of v1.95.0); re-time push→`chore(release)`→`publish-pypi`→`release-tag-smoke` if the CI matrix has changed since |
| `TG_RG_TIMEOUT_SECONDS` default | `grep -n TG_RG_TIMEOUT_SECONDS src/tensor_grep/cli/subprocess_policy.py` (currently `60.0`, `subprocess_policy.py:75`; the `600` figure AGENTS.md still cites at `:378` predates this default and reads as present-tense there — re-verify whether that AGENTS.md line is itself stale before trusting it) |
| Security round-3 sweep files | `AGENTS.md` "Security Hardening Patterns"; files `src/tensor_grep/cli/{checkpoint_store,session_daemon,session_store,mcp_server}.py` |
| Open round-4 argv item | `AGENTS.md` (native-argv `--` sentinel); `rust_core/src/rg_passthrough.rs` |
| Dogfood harness present | `ls scripts/dogfood/` (`Dockerfile`, `dogfood_features.py`, `README.md`) |

If any command above no longer matches, update this skill in the same change — a wrong runbook is worse than none.
