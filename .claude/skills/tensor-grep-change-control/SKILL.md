---
name: tensor-grep-change-control
description: Use when about to change, review, merge, or release ANY code in tensor-grep — adding a tg command or search flag, touching a backend/router/pipeline, editing CI/release/docs contracts, merging a PR, claiming a fix or speedup is done, or deciding whether a follow-up commit is truly "docs-only"/"comment-only". Encodes the non-negotiable gates (draft-PR-only autonomy, never-trust-a-self-report, no-speed-claim-without-numbers, experimental-until-proven, TDD-first, smallest-change, benchmark-hot-paths, the 4 registration sites, one-merge-per-tick / the push-race, dogfood-the-real-binary, contract-changes-need-validator-tests, a test proving nothing until seen fail on the pre-fix baseline, gating comments/docstrings with the same rigor as code, diff-review-is-not-measurement-review, and the `ast.dump()` behavior-neutral proof technique) and the historical incident behind each.
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

## Part 1 — The seven UNWRITTEN non-negotiables

These are not in a config file; they are CEO-confirmed law. Breaking one is a process failure even if the code is clean.

### 1. Autonomy is draft-PR-only

**Rule:** Never auto-merge, never admin-merge, never auto-restart a service unattended. Every self-acting behavior ships **default-OFF** and graduates only via: council-verify → dry-run (preview what it would do on real data) → a **conscious flag-flip** by a human. The endpoint of any autonomous fan-out is a **draft PR** a human reviews and clicks merge on.

**Why / incident:** The dogfood follow-up workflow ends every fan-out at a draft PR precisely because a post-build adversarial audit once caught a **HIGH CUDA-fork hazard that 203 passing green tests missed** (`AGENTS.md:436`, `AGENTS.md:571`). Green tests are not a merge signal for autonomous work. A model that merges its own PR removes the one gate that catches what the tests can't.

**Applies to:** any agent orchestration, self-upgrade helper, watcher, or "just merge it" impulse.

### 2. Never trust a self-report

**Rule:** A subagent's or model's "tests pass" / "N green" / "I fixed it" is a **hypothesis** until **external state** confirms it: an exit code, a real-binary dogfood, or a `file:line` that actually resolves. Re-run any validation a subagent claims to have passed.

**Why / incidents:**
- Subagents can assert success without executing (`AGENTS.md:434`). Worktree fan-out branches have **no `.venv`**, so an agent's "tests pass" is literally un-runnable in its own tree — you must re-run pytest/ruff/mypy in the real venv before integrating (`AGENTS.md:2212,2241`).
- **Mock-based FFI tests passed GREEN while the real PyO3 bridge was DEAD** — it dropped every forwarded flag and silently fell back to the Python engine. Prove a bridge/FFI change with a **live runtime call into the built extension**, then confirm the flag actually reached `rg` (`AGENTS.md:901`).

**Concrete gate:** For generated/detached code (install scripts, self-upgrade helpers), adversarial-review by **executing** it — `compile()` + `exec()` the generated string and assert behavior (e.g. the checksum gate fires *before* `os.replace`), not substrings (`AGENTS.md:434`).

### 3. No speed / improvement claim without measured numbers

**Rule:** Never claim a speedup, regression, or "improvement" without a measured line **vs the accepted baseline** (not memory). Reject a candidate that is slower — or only "faster" in a microprofile while slower end-to-end — **even if the code is clean**. If a candidate is correct but slower, **revert it and record the attempt** (in `docs/PAPER.md`) so no future agent retries the losing idea.

**Why / incidents & theory:** `rg` (ripgrep) is the **raw cold-text parity baseline**; `ast-grep` is the **structural-search baseline** (`AGENTS.md:344-345`). tg's moat is the agent-native intelligence layer, *not* faster grep — so an unmeasured "it's faster" claim is both unverified and off-strategy. Hard-won architectural truths already in the repo: more caching is **not** always faster; onefile Nuitka binaries are **not** the Windows speed path for plain passthrough; GPU is currently **slower** than CPU (`AGENTS.md:796-826`). Benchmark artifacts must carry `tg_launcher_mode` + `tg_launcher_command_kind` and **refuse stale in-tree binaries by default** — a timing taken through a `.cmd` shim or a stale `rust_core/target/*/tg.exe` is not a claim (`AGENTS.md:364`). Run the *right* benchmark for the area (see `tensor-grep-benchmark-and-proof-toolkit`).

### 4. Experimental-until-proven

**Rule:** GPU, LSP, semantic-search, and provider-backed classify (`cybert`) paths stay **default-OFF and labeled experimental** until correctness **and** speed **and** UX are all proven. Never market an unproven wedge.

**Why / incidents:**
- **GPU** Phase-0 SHIPPED (v1.75.1-v1.75.4, PRs #594-#597 -- #593/v1.75.0 was an UNRELATED
  `tg orient`/`tg agent` improvement that landed in the same version range by publish order, not part
  of the GPU wave; AGENTS.md's "GPU Phase-0 hardening wave" addendum records the same range): NVIDIA native assets are built and locally correctness-proven (RTX 4070 `sm_89` / RTX 5070 `sm_120` -- `docs/gpu_crossover.md`), but gated OFF the public release by the CI Actions var `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; GPU asset publishing needs the non-default `native-frontdoor-gpu`) -- Phase 1 is now a reversible flag-flip, not a multi-week rebuild. That flip publishes assets only: no speed crossover is proven vs `rg`/`tg_cpu`, GPU auto-recommendation stays `false`, and the reviewer-gated `public-gpu-proof.yml` speed-crossover gate remains unmet (`grep -n "Public managed GPU promotion" docs/CONTRACTS.md` — was cited at `:80-82`, now `:123`; the old anchor pointed at the `--column`/`-c` flag list). Any GPU-requested fallback must surface `gpu_evidence_status = unsupported`, `gpu_proof = false`, `native_gpu_unavailable` (`AGENTS.md:843`). The only *candidate* CUDA wedge is many fixed strings over a large corpus — never single-pattern cold grep.
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

### 7. "Not mine" / "CI doesn't flag it" is not a disposition — but ownership decides WHERE the fix lands

**Rule:** Authorship, CI visibility, tracked-vs-untracked status, and whether a finding sits inside the
current task's stated scope are all irrelevant to whether a real defect gets FIXED — but ownership decides
WHERE the fix may land. If you find a defect while doing something else, never reason your way past it;
the disposition is one of:

- **Your own / isolated tree (a worktree you own, a fresh branch off `origin/main`):** fix it in the same
  turn, in place.
- **Another agent's in-flight WIP, a file marked do-not-touch, or any foreign dirty state:** do NOT edit
  in place. PRESERVE the foreign dirty/untracked state exactly as found, RECORD the finding
  (`file:symbol` + repro) in the durable place for it (the owning PR/issue, the tracker, the handoff
  doc), and fix it in an owned/isolated tree or after EXPLICIT ownership transfer from the owner/human.
  A concurrent writer's tree is shared state: an in-place "fix" can collide with a rewrite in flight,
  and `git stash` / `git add -A` in a shared tree can destroy another agent's work — grep AGENTS.md for
  "Never edit a worktree a live agent owns" and "`git stash` Is UNSAFE Once Parallel Worktrees Exist"
  (the 2026-08-02 receipt); never stamp those as line numbers.

The only legitimate stop is a hard blocker (needs a build/fire, is irreversible, or is human/CEO-gated),
and that gets a tracked follow-up with a concrete acceptance test, never a sentence of justification.

**Why / incidents:** A lint/audit finding was named out loud and then waved past **twice** with exactly
this reasoning -- "not my file," "CI doesn't flag it" -- and both times the underlying defect was real. A
constraint on one verb (e.g. "do not **commit** this file") is not permission on another (silently
generalizing it into "do not **fix** this file" and leaving a live bug in the tree). The 2026-08-12
retention audit then found the ORIGINAL wording of this rule ("fix it in the same turn -- in place, even
... in another agent's in-flight WIP") contradicting AGENTS.md's never-edit-a-live-agent's-worktree law
and the 2026-08-02 parallel-worktree receipts — the wave-past failure and the ownership failure are two
distinct defects, and the rule must close BOTH, not trade one for the other.

**Applies to:** any lint/grep/audit finding you surface incidentally while doing something else, regardless
of who owns the file, whether it is tracked by git, or whether CI currently exercises it.

---

### 8. Before merging ANY PR, assert its base is `main` — a "skipping" rollup is an ABSENT gate

`.github/workflows/ci.yml` filters `pull_request: branches: ["main"]`, and that filter matches the
**base** ref. A stacked PR (base = another feature branch) therefore **never triggers `ci.yml` at
all** — and `gh pr checks` prints that absence as `skipping` while `mergeStateStatus` reports
`MERGEABLE`. Both read as benign.

Measured 2026-08-21: PRs #1068 and #1070 had **exactly one** check run each across their entire
life (`Dependabot Automation`, conclusion `skipped`). Control proving it is the base ref and not
the branch name or a runner outage: #1065, same `test/` prefix but base `main`, showed
`SUCCESS=39`. **Both stacked PRs went RED the moment real CI ran** — and they carried
error-handling hardening, sitting one click from merge with no test, lint, security, or
cross-platform evidence whatsoever.

```bash
gh pr list --state open --json number,baseRefName    # every row must say "main"
```

Two mechanics worth knowing before you try to fix one:

- `gh pr edit --base main` alone does **not** restore CI. It fires `pull_request` action `edited`,
  which is not in the default trigger set. **Close/reopen** (action `reopened`, which is) does.
- After the parent squash-merges, the child conflicts, because it still carries the parent's
  individual commits against a squashed `main`. Rebase with
  `git rebase --onto origin/main <parent-tip>` to drop exactly the absorbed commits.

Related: **A123** in `AGENTS.md`.

### 9. The file-size ratchet forbids GROWTH — pay for an addition, never raise the pin

`scripts/file_size_budget.py` fails any allowlisted file that grows: *"An allowlisted file may
shrink, never grow."* A 20-line security fix took `cli/main.py` 13,523 → 13,543 and CI rejected it.
Raising the pin is explicitly forbidden ("never raise it to make a new unreviewed handler pass").

**Pay for the addition instead**: move an equivalent amount OUT of the file, ideally something
cohesive with where it's going. The 2026-08-21 fix moved a scan-guardrail helper from `main.py`
into `scan_guardrails.py` (main.py → 13,512, budget 0 regressions, grandfathered 27 → 26).

Two constraints on what you may move:

- **A symbol tests monkeypatch by attribute cannot move.** Relocating it breaks the patch target
  with **no import error**, so the test keeps passing while patching nothing. Check with
  `grep -rn "<symbol>" tests/` before moving anything.
- **Do not merge same-named things without comparing them.** `_BROAD_GENERATED_SCAN_DIR_NAMES`
  exists in BOTH `cli/main.py` (22 entries — adds `.claude`, `.git`, `AppData`) and
  `cli/scan_guardrails.py` (19). Collapsing them would have silently changed behaviour. Pass the
  set in as a parameter instead. (A132)

**And know that the limit is currently UNREACHABLE for the three giants.** Run the repo's own
instrument before proposing any split:

```bash
uv run python scripts/measure_split_floor.py
```

It reports `SPLIT CANNOT REACH THE LIMIT` for `repo_map.py` (6,715 lines locked), `main.py`
(7,416) and `mcp_server.py` (2,506) — all against a 1,500 limit, all locked to their facades by
monkeypatch targets. The binding constraint is the **test strategy**, not code organisation, so the
honest options are to reduce monkeypatch coupling (a programme, not a refactor) or to state the
exception rather than carry an allowlist entry implying a completion that cannot come. The tool
states its own direction of error: it is a LOWER bound and function-only, so a patched module-level
CONSTANT is invisible to it — the real floor is never lower. (A130)

### 10. A QUEUED run is not protected — batch the merges, then STOP pushing

`ci.yml` sets `cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}`, which reads as "never
cancel a main run". **That reading is wrong in the case that matters.** The flag governs runs
already IN PROGRESS. A run still **QUEUED** in the same concurrency group is superseded by the next
push regardless.

This repo is runner-scarce — main runs sit queued for tens of minutes — so **every merge cancelled
the previous release run before it started.** Measured 2026-08-21 on
`gh run list --branch main --workflow=ci.yml`: `6909018` cancelled, `2d02a22` cancelled, `0eebab5`
cancelled. Three consecutive main runs, all killed while queued.

**This was a second, independent cause of "tagged but not published"** alongside PYPI-SIZE-CAP, and
it was initially misattributed entirely to the cap. Clearing the cap alone would not have fixed
publishing.

**The protocol, superseding "one merge per tick":**

1. Verify every candidate PR is genuinely green (see §11 — count is not enough).
2. **Merge them all in one burst.** The release is cumulative from the last tag, so merging more
   before the run starts LOSES NOTHING and gains a single publish covering everything.
3. **Then stop pushing entirely** — including docs PRs, which consume the same runners.
4. Wait for `gh run list --branch main --workflow=ci.yml --limit 1` to read **`completed`**, not
   merely to exist. A created run is not a protected run.
5. Verify the release **per-artifact** (A124): a tag or version appearing proves nothing; the
   expected filename set does.

**The same effect bites PR branches, where `cancel-in-progress` IS true.** Re-pushing to
"re-trigger CI" starves it: measured on one branch, `08a7fe20` cancelled, `16fc31d1` queued 30+
minutes and never started, head SHA with no run at all. Each rebase-push / fix-push /
empty-commit-push killed the queued predecessor. **The remedy is the opposite of the instinct: stop
pushing.** Before concluding CI is "broken", check queue depth
(`gh run list --limit N --json status`) — a sibling branch's run sitting queued identifies runner
scarcity rather than a dispatch fault. Laws **A133**, **A134**.

### 11. Assert checks by NAME — a count cannot tell a matrix run from CodeQL

A CI-watching script used `if total > 5 and pending == 0 -> GREEN`. Seven CodeQL + Dependabot
entries satisfy that, so it reported **two PRs on which `ci.yml` had never run** as TERMINAL GREEN,
and both became merge candidates on that basis.

```bash
# WRONG -- 7 CodeQL entries pass this
[ "$total" -gt 5 ] && [ "$pending" -eq 0 ] && echo GREEN

# RIGHT -- a real matrix run contains test-* checks
testcount=$(gh pr view "$pr" --json statusCheckRollup \
  -q '[.statusCheckRollup[]|.name]|map(select(startswith("test-")))|length')
[ "$testcount" -lt 4 ] && echo "NO-CI: absent gate, not a pass"
```

This is A123's "absent gate renders as a pass" with the faulty instrument being your own. Law
**A135**.

### 12. One change can trip several independent ratchets — say which case you are in

A single new `except Exception` had to satisfy BOTH the disposition ledger
(`docs/audits/2026-08-20-handler-dispositions.json`, which records WHAT it is) and
`TOTAL_BROAD_HANDLERS_CEILING` (which bounds HOW MANY exist). A single moved function tripped the
file-size ratchet AND the silent-loss census. They are deliberately separate gates; satisfy each on
its own terms.

The distinction that decides the response:

- **RELOCATION** — re-pin, but PROVE it: the total must be unchanged and the moved sites
  byte-identical. Measured example: `main.py` 6→4, `scan_guardrails.py` 5→7, **total 41→41**, with
  both new sites read and confirmed identical to the ones that left.
- **GROWTH** — harden or disposition it. **Never re-pin.** A ratchet exists because "every added
  site is a new way for an incomplete result to report success".

Write which case you are in **beside the number**, so nobody later cites your relocation as
precedent for absorbing real growth. Law **A137**.

## Part 2 — The written Operating Rules

From `AGENTS.md` "Operating Rules" (`:856`) and `CONTRIBUTING.md`:

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
| 2 | `Commands::X` enum variant + dispatch arm (native front door) | `rust_core/src/main.rs` | `grep -n "enum Commands" rust_core/src/main.rs` (was `:889`, now `:910`) |
| 3 | `PUBLIC_TOP_LEVEL_COMMANDS` (parity contract test) | `tests/e2e/test_routing_parity.py` | `grep -n "PUBLIC_TOP_LEVEL_COMMANDS = " tests/e2e/test_routing_parity.py` (was `:18`, now `:46`); asserted by `test_top_level_help_visible_commands_match_public_contract` (was `:563-564`, now def `:583`, asserts `:592-593`) |
| 4 | `@app.command` function (Typer entry point) | `src/tensor_grep/cli/main.py` | `grep -c "@app.command" src/tensor_grep/cli/main.py` (re-run before citing a count — it drifts every release; do not trust a stamped number) |

### Adding a search flag (`tg search --myflag`) — 2 front doors (miss one → `rg: unrecognized flag` crash for installed users)

| # | Front door | File | Verified anchor |
|---|---|---|---|
| 1 | `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (native allowlist) | `rust_core/src/main.rs` | grep `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (was `:183`, now `:204`) |
| 2 | `bootstrap._TG_ONLY_SEARCH_FLAGS` (Python bootstrap allowlist) | `src/tensor_grep/cli/bootstrap.py` | `grep -n "_TG_ONLY_SEARCH_FLAGS" src/tensor_grep/cli/bootstrap.py` — def `:50`, checked at `:404` (was cited as checked at `:355`) |

**Why / incident:** The `tg search --rank` flag missed one of the two front doors. CliRunner tests were green — because CliRunner bypasses the bootstrap front door (Part 5) — so the crash shipped and only surfaced for users of the published binary (`AGENTS.md:405-410`). The **CI registration-completeness gate is BLOCKING since v1.17.1 (#282)** and its extractor is comment-aware (`#`-commented entries are not counted as registered) (`AGENTS.md:414`).

**Audit procedure before claiming a registration change is done:**
- `tg callers <registration-function>` lists every *callable* registration in ~1s — **but the call graph cannot see set/list/decorator/dispatch-table registrations** (e.g. `_TG_ONLY_SEARCH_FLAGS` is a *set*, `@router.post` a decorator). `--rank` lived in a set, so `callers` would never have found it.
- So **grep / `tg scan`** the set/decorator/table sites too. Confirm your new entry appears in **all** sites (`AGENTS.md:412`).

### Registering a new symbol-graph language — 5 seams (miss one → a silent half-integration)

**Jargon:** the *symbol-graph tier* is the deep per-language layer behind `tg defs`/`tg source`/
`tg imports`/`tg callers`/`tg agent` — distinct from plain text search (any language, via `rg`
passthrough). As of this pass **10** languages are registered: python, javascript, typescript, rust, go,
java, php, csharp, **c, cpp** (`lang_registry.LANGUAGE_REGISTRY`, pinned by
`test_language_registry_has_exactly_the_stage2_languages` in `tests/unit/test_lang_registry.py` --
grep the test NAME, not a line number). C/C++ ARE registered, via `lang_c.py`/`lang_cpp.py`; an
earlier revision of this section said they were not, which mattered because this is the skill that
gates every new-language change.

The registry entry point is `lang_registry.register_language(lang_registry.LanguageSpec(...))`
(`src/tensor_grep/cli/lang_registry.py:118`), called once per language inside
`src/tensor_grep/cli/repo_map.py`. **Do not cite a stamped count here** -- run
`grep -c "lang_registry.register_language(" src/tensor_grep/cli/repo_map.py` (10 as of 2026-07-27).
This line previously carried a hardcoded 8 and went stale the moment C/C++ landed, which is exactly
the failure the `@app.command` row in Part 10 already fixed by replacing a number with a command. A language's extraction
callables can live either inline in `repo_map.py` (python/rust/java) or in a dedicated `lang_<x>.py`
module mirroring `lang_go.py` (go/php/csharp — a separate module avoids an import cycle back into
`repo_map.py`); both are contract-consistent.

Registering the `LanguageSpec` is necessary but not sufficient — 5 more call sites either dispatch on
the registry or hardcode a language list directly, and missing one is a **silent half-integration** (the
language works for some commands and quietly does nothing for others):

| # | Seam | Feeds | File | Verified anchor |
|---|---|---|---|---|
| 1 | `_imports_and_symbols_for_path` | `tg imports` (import list + symbols) | `repo_map.py` | grep `def _imports_and_symbols_for_path` (was `:6244`, now `:6627`; branches `:6650-6679`) |
| 2 | `_imports_with_lines_for_path` | `tg imports`' line-numbered spans | `repo_map.py` | `grep -n "^def _imports_with_lines_for_path" src/tensor_grep/cli/repo_map.py` (was `:6440`, now `:6832`) — dispatches ALL 10 as of the top-10 campaign's final waves (python/js/ts/rust/java inline; go/php/csharp/c/cpp via their `lang_*` module extractors, `repo_map.py:7089-7116`; the old "go/php/csharp fall through to `[]`" note predates the top-10 wave) |
| 3 | `build_symbol_source_from_map` | `tg source` | `repo_map.py` | grep `def build_symbol_source_from_map` (was `:15815`, now `:16326` -- 511 lines adrift) |
| 4 | `_target_language_for_path` | **MOST-FORGOTTEN.** Feeds the `tg agent` capsule's query-language-vs-target-language confidence gate (`agent_capsule.py`) | `repo_map.py` | grep `def _target_language_for_path` (was `:7383`, now `:7867`) -- the function's own comments say "MOST-FORGOTTEN seam" at each of the 4 newest branches, grep that phrase rather than trusting sub-line numbers; skip it and the capsule can silently report "no target language" for a real target instead of downgrading confidence honestly |
| 5 | `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` | `tg imports <file>`'s file-dependency-resolution "supported" gate | `repo_map.py` | grep `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` (no line number: the file has been split, grep the symbol) — all 10 as of the top-10 campaign's final waves: `frozenset({python, javascript, typescript, rust, java, go, php, csharp, c, cpp})` (the `frozenset` beside that symbol in `repo_map.py`); go/php/csharp/c/cpp joined at the raw-imports tier — their deeper `import_update_target`/true `import-string -> target-file` resolution is still `None` (tracked follow-ups in `docs/BACKLOG.md`), so those files honestly report unresolved import edges instead of a fabricated resolved list |

**Fail closed for a missing grammar.** Every language added since the registry existed
(go/java/php/csharp) sets `provenance_when_missing="grammar-missing"` in its `register_language(...)`
call (grep `language_id="go"`; was `repo_map.py:6090`, now ~`:6368`) — never `"regex-heuristic"` — so a file whose tree-sitter grammar
package isn't installed surfaces as an honest `resolution_gaps` entry via
`_language_coverage_gaps_for_universe` (`grep -n "^def _language_coverage_gaps_for_universe" src/tensor_grep/cli/repo_map.py` — was `:8461`, now `:8478`; the fail-closed branch — `grep -n "fail_closed = True" src/tensor_grep/cli/repo_map.py`, now `:8521` — was previously cited as `:8019`, which today lands inside an unrelated AST-symbol-matching helper, `_thin_cli_dispatcher_call_targets`, not this function at all) instead
of a silent empty result. This is Part 4's Backend Fail-Closed Contract, applied inside the language
registry (see Part 4's own worked example below).

**Audit procedure:** grep all 5 seams plus the registration call
(`grep -n "lang_registry.register_language\|_imports_and_symbols_for_path\|_imports_with_lines_for_path\|_target_language_for_path\|_SUPPORTED_FILE_DEPENDENCY_LANGUAGES" src/tensor_grep/cli/repo_map.py`),
then widen `tests/unit/test_lang_registry.py:84-94` (`test_language_registry_has_exactly_the_stage2_languages`) to include the new language — this is a **pin test for registry membership** (same
principle as Part 1 Rule 6's ranking pin, applied to a set instead of a ranked list): it fails loud the
moment a rebase silently drops a language (see Part 7's sequential-drain corollary below).

### A census's population is itself a defect surface -- curate it, don't derive it

Every table above (4 command sites, 2 flag front doors, 5 language seams) is a **census**: a claim that
"these are all the places this thing lives." The census itself is where the recurring bugs live, not just
the code it protects.

**Never add a census member by reasoning it is covered -- CALL it.** The native-argv `--`-sentinel
completeness census (the round-4 argv item tracked in this skill's provenance table below,
`rust_core/src/rg_passthrough.rs`) had its population wrong **four times in one session** -- 5, then 8,
then 10, then 13 members -- and every single miss was the same judgment: "builder A transitively covers
builder B." Each was disproved only by deleting B's own guard and watching the suite stay green anyway.
One of the missed members (`_build_command`) carried the worst possible cost of the four: a path of
`-U`/`--update-all` reaching ast-grep's `run` is its **auto-fix switch**, so an unguarded miss there turns
a read-only scan into a file rewrite. **Rule:** treat a census list as **curated, not complete** -- prove
membership by deleting the candidate's guard and confirming red, and re-derive the whole list by sweep
every release; never claim it final (compare Part 3's own "do not cite a stamped count" rule for the
language-registration count above -- same discipline, applied to a security-relevant guard instead of a
head-count).

**Enumerate EMITTERS/ARTIFACTS, not the mechanism they happen to share.** A census keyed on a common
implementation mechanism (e.g. "every builder that uses decorator/pattern X") can report "N of N covered"
and still be wrong by one, because a sibling can produce the identical artifact by an entirely different
mechanism -- sharing no type, no decorator, nothing a mechanism-keyed grep would match -- and land
uncovered by the guard the census was supposed to feed. The same trap recurs one level down inside a
single site: **a function is not the unit, the artifact is** -- two independently-built argv sequences
constructed dozens of lines apart inside one function let a whole-function substring match report the
first one's sentinel as "covering" the second one's bare, unguarded positional. **Rule:** key a census on
the *shape of the output* (every place this exact artifact gets constructed), never on a shared
implementation mechanism -- a sibling that reaches the same artifact by a different path is exactly the
member a mechanism-keyed search cannot see.

**Generated code is a second interpreter and must join the population.** Discover every production
spawn/exec root, parse every statically resolvable payload as its own source unit, and fail closed on
dynamic/unparseable payloads. Resolve local imports, aliases, rebinding, and shadowing. Sanction exact
`source:callsite:operation:destination-provenance` fingerprints, not whole functions. Prove the census
with ordinary and generated-source mutation controls. Receipt: #859's codemap-only ratchet missed three
live writers plus generated helper sinks.

### A shared builder's flag belongs to its consumers, not its neighbors (#876, fixed #880)

**Rule:** before adding a flag/param to a SHARED builder, enumerate every consumer of what it builds and
ask which of them CONSUME the thing the flag changes -- not just where the flag fits among its neighbors.

**Why / incident:** `-q` was added to `RipgrepBackend._build_cmd` -- the shared argv builder, where ~30
other flags already live, so it looked like the natural home. `_build_cmd` has FOUR consumers; only ONE
streams rg's stdout, the other THREE parse it, and `-q` makes rg print nothing. Measured on the real
binary:

    rg --count-matches needle f.txt -> "2"     with -q -> ""
    rg -l             needle f.txt -> "f.txt"  with -q -> ""
    rg --json         needle f.txt -> 5 lines  with -q -> 1

So `tg search -q --count` on a MATCHING file reported `total_matches=0`, exit 1 -- a false no-match plus
an exit-contract violation, shipped in #876. A flag that alters OUTPUT belongs to the consumers that
stream, not the ones that parse.

**Applies to:** any shared builder (argv, query, request) with more than one consumer -- grep every call
site and classify each "streams the result" or "parses the result" before adding a flag that changes what
gets printed.

---

## Part 4 — Backend fail-closed contract (the silent-wrong-answer bug class)

**Jargon:** a *ComputeBackend* is a search engine implementation (CPU regex, Rust, GPU, ast-grep, …) behind a common interface (`src/tensor_grep/backends/base.py`).

**Rule (`backends/base.py:7`, `AGENTS.md:2090`):** Every backend **MUST raise `BackendExecutionError` on a real failure** — never return a clean empty / `0-match` result, and never silently swap to an engine that cannot preserve the requested semantics. The search loop catches `BackendExecutionError` to fall back **visibly**; a swallowed failure reaches a coding agent as a trustworthy "no matches" — the one failure a context tool cannot afford.

- **Fail closed** for any flag the fallback cannot preserve — e.g. `--pcre2` through a non-PCRE2 engine must **raise, not swap**.
- If a degraded fallback is *legitimate* (e.g. heuristic classify when the model is down), make it **visible**: set `fallback_reason` (and a distinct `routing_reason`) on the result so JSON/CLI consumers can tell degraded from real. **Never label heuristic output as model output.**
- Validate an untrusted response shape (e.g. a model's class count vs a fixed label list) before indexing, so a mismatch degrades instead of raising an `IndexError` a broad `except` then swallows.

**Why / incidents (this contract is violated repeatedly):** the Rust/PCRE2 bridge ran `--pcre2` through the Python-regex engine (wrong results); the ast-grep OOM mask read a killed subprocess as a clean 0-match; a tree-sitter invalid-query silently returned 0 matches; CyBERT labeled keyword-heuristic hits as real model output. The recurring smell is a **bare `except Exception:` that returns empty or falls to a different engine** (`AGENTS.md:442`). The same rule extends to any router/pipeline that could silently override explicit user intent — e.g. an explicit `--gpu` request quietly routed to CPU must raise `ConfigurationError` or emit a diagnostic (`AGENTS.md:448`; fix shipped in `src/tensor_grep/core/pipeline.py`). A `SafeBackendMixin` + fault-injection conformance CI gate is the planned structural fix so this stops recurring file-by-file.

**Concrete example outside `backends/` (the same contract, a different subsystem):** the multi-language
symbol registry (Part 3) applies this identically. `LanguageSpec.provenance_when_missing` must be
`"grammar-missing"` (never `"regex-heuristic"`) for any language with no text-heuristic fallback —
go/java/php/csharp all set it this way in their `register_language(...)` call (grep `language_id="go"`; was `repo_map.py:6090`, now ~`:6368`)
— so `_language_coverage_gaps_for_universe` (`grep -n "^def _language_coverage_gaps_for_universe" src/tensor_grep/cli/repo_map.py` — was `:8461`, now `:8478`) can tell "grammar not installed, fail
closed" apart from "language has a regex fallback, degrade quietly" at its branch — `grep -n "fail_closed = True" src/tensor_grep/cli/repo_map.py`, now `:8521` (was cited as `:8019`, which today lands inside an unrelated AST-symbol-matching helper, not this function). Get
this backwards (label a no-fallback language `"regex-heuristic"`) and a grammar-missing file would read
as a clean, silent "zero symbols found" instead of an honest gap — precisely the failure class this Part
exists to prevent, just reached through a registry field instead of a bare `except`.

**Domain note (ripgrep):** tg's default regex path matches invalid UTF-8; **PCRE2 requires valid UTF-8 and transcodes** — which is *why* swapping `--pcre2` to a non-PCRE2 engine changes results, not just performance. `rg` exit code `1` with empty output is a legitimate "no match" (`AGENTS.md:367`); exit code `2` with matches already parsed is treated as **partial** (kept + `result_incomplete=True`, the "surface degraded, don't discard" posture this Part argues for, not a swallow); any other case (`2`+ with nothing parsed, or `>2`) is a **real ripgrep failure** — `ripgrep_backend.py` raises `BackendExecutionError` (not a bare `RuntimeError`) on `returncode > 1 and not partial` at three call sites — `grep -n "if result.returncode > 1 and not partial" src/tensor_grep/backends/ripgrep_backend.py` (was `:126`, `:297`, `:413`, now `:127`, `:308`, `:426`) — and this must not be swallowed as non-fatal.

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

**A banked hypothesis is not exempt from this gate, even your own (#736, 2026-07-24).** A one-line
memory note claiming a C symbol-graph mis-kind was fixable by "requiring `function_declarator`
outermost" was FALSIFIED by re-deriving it against a live-dumped AST before any fix code was written —
a function-pointer variable's declarator chain also has `function_declarator` outermost, so that tell
cannot distinguish it from a real function. The real tell lived one level deeper (what the node's own
`declarator` field wraps). Treat a carried-forward "we already know the fix" note the same as a fresh
AI-drafted plan: cite `file:line` against the CURRENT code before dispatching it, not just against your
memory of a prior session. See `tensor-grep-failure-archaeology` and `tensor-grep-add-language` for the
full worked example (the declarator-shape table).

**A gate's disclosed edge case is still-open work, not a new backlog item, while the PR is draft (same
#736).** An independent Opus gate returned `SHIP` on that fix but disclosed the first cut now dropped a
narrower case (redundant-paren real-function prototypes, `int (foo)(void);`) it hadn't dropped before.
Because the PR was still draft, the refinement landed in the SAME PR before un-drafting — zero new
known-limitations shipped. Read a `SHIP`-with-disclosed-edge verdict as "fix this before un-drafting,"
not "ship now, file it for later" — the marginal cost of fixing it in-PR is near zero.

**A test proves nothing until it has been seen to FAIL on the pre-fix baseline (#737, 2026-07-24).** The
C++ sibling of the #736 fix shipped a new test for shape 9 that, on independent re-derivation, turned out
to pin the IN-CLASS member-fn-ptr shape — already excluded on pre-fix `main` through an unrelated code
path — while the shape the fix actually repaired (file-scope) had no test at all. "I added a test, it's
green" is not evidence the test would have caught the bug; only a RED result against the pre-fix code
is. Full mechanism (the two AST shapes, which code path excludes which): `tensor-grep-validation-and-qa`
Part 1 point 18.

**Gate the prose with the same rigor as the code — a false claim in a comment survives forever because
CI can never fail on it (#739, 2026-07-24).** A de-flake PR's own follow-up commit fixed the test
correctly but justified leaving a sibling test alone with a comment claiming its timing ratio "genuinely
correlates and cancels load" — measured, this was false (the ratio's `max()` floor always wins for that
sibling; see `tensor-grep-validation-and-qa` Part 1 point 19 for the numbers). Review a comment or
docstring that makes a factual/measured claim exactly as skeptically as a code change: ask "did anyone
actually verify this," not "does this read as plausible."

**Diff review is not measurement review — for a quantitative fix (perf, de-flake, any numeric claim),
the gate must re-measure, not just re-read (#739, 2026-07-24).** A set of diff-level checks (test-only
diff, zero `src/` changes, perturbation reverted, call sites intact) on the same PR were each correct and
collectively insufficient — the degenerate-baseline bug above (Part 6, and `tensor-grep-validation-and-
qa` Part 1 point 19) was only caught by independently re-running the numbers, not by reading the diff
shape. Extend the mandatory adversarial security gate's "actually try to break it" posture to
quantitative claims: actually re-measure them.

**Your verification instrument can be the thing that's wrong (2026-07-24).** Spot-checking a sibling
docs PR for whether its "DEFERRED" honesty caveat was present, `grep -ciE "DEFERRED\|deferred"` returned
ZERO hits — which briefly read as the caveat missing. It was there, verbatim; the command was broken.
In `grep -E` (extended regex), `\|` matches a **literal pipe character**, not alternation — extended-
regex alternation is a bare `|`, and the backslash-escaped `\|` form belongs to basic-regex/`sed`
syntax, not `-E`. The search was therefore for the literal 9-character string `DEFERRED|deferred`,
which matched nothing. **Rule:** when a verification check contradicts an otherwise-careful report,
re-test the INSTRUMENT against known-present content before concluding the report is false — a false
negative from a malformed pattern is indistinguishable from a real absence, and acting on it sends a
spurious correction. Same family as this repo's git-bash/MSYS `gh --json`-parsing quirks (favor
`python` over `jq`/raw `/`-path expressions there for the same reason) — a tool that silently does
something other than what its syntax suggests, rather than failing loudly. Concretely here: `grep -E`
alternation is a bare `|`, not `\|`.

**Prove "docs-only"/"comment-only" with `ast.dump()`, not eyeballing (2026-07-24).** When a follow-up
commit needs to be certified behavior-neutral to justify skipping a redundant full gate re-run, parse
both revisions with `ast`, strip docstrings (plain comments never enter the AST), and diff
`ast.dump(tree)` between them — an identical dump is proof of zero behavioral change, strictly stronger
than a `git diff` read. Used this session on `lang_cpp.py` and `test_index_lock_concurrency.py`'s
comment-only revisions.

**Refactoring code that lives inside a generated string is not refactoring code.** Extracting a shared
helper out of two duplicated blocks is a real DRY win only if both call sites execute in the *same*
interpreter. If one of them instead lives inside a `textwrap.dedent(...)`-built string that gets written
to disk and run as a **standalone script** (e.g. by the Windows Task Scheduler, which cannot `import
tensor_grep`), then "extracting the shared helper" replaces inline code with a call the standalone script
has no way to resolve -- a `NameError` at runtime, invisible at review time because the diff still reads as
a clean DRY cleanup. This was caught only because an existing test pins the **generated script's text**
byte-for-byte, not because the refactor itself was re-read carefully enough to notice. **Rule:** before
applying any "extract a shared helper" refactor, check whether either call site is a string later written
to disk / `exec`'d / handed to another process -- a DRY fix that crosses that boundary is a correctness bug
wearing a cleanup's clothes.

**A plan's stated base commit is a claim, not a fact -- prove it (2026-08-01).** A plan must state its
base commit AND prove it with `git rev-parse origin/main`; an auditor re-derives that base rather than
accepting the plan's header. A planning agent stated one commit as its base while its own citations
matched a different one, and `origin/main` was 15 minutes ahead of both -- its Item 1 "warned" about a
trap that was already live on `main`, and two of its items were already shipped.

**Plan approval is scoped to exact bytes and expires on a changed premise (2026-08-02).** Hash the design
and implementation plan for every council round. If the live-code deep dive changes the writer
population, API visibility, fallback model, file scope, or any other load-bearing premise, the old SHIP
verdict no longer applies: amend, re-hash, and re-review before dispatching a build.

Name the canonical hash artifact and method. On Windows, two clean-filter-equivalent worktrees can have
different raw bytes because of mixed line endings; do not compare an on-disk worktree hash from one seat
to a Git-blob or normalized-text hash from another. Also validate the cross-task dependency graph: every
service, public registration, and producer must exist before the first consumer or behavioral RED. A
missing-command/import failure cannot stand in for the behavior the test claims to exercise.

**A green artifact cannot clear a different artifact (2026-08-03).** Record the PR head, canonical
worktree plan hashes, review hashes, merge SHA, and published version separately. A green PR at commit A
does not approve uncommitted plan B; an architecture `SHIP` does not substitute for adversarial-security
`SHIP`. When using Cursor or another economical builder/reviewer, send the exact resulting bytes and
prompt to Sol before treating the result as cleared.

**Security vocabulary must compile to an enforceable primitive.** “Atomic CAS,” “trusted signer,”
“owned PATH token,” and “kill descendants” are not implementation contracts. A plan names the concrete
OS/API call, flags, protected authority root, opened identity, failure behavior, and RED that breaks the
weak form. On Windows this means, for example, transacted-registry calls or fail-closed (not a process
lock dressed up as CAS); exact offline WinTrust/root-policy flags rather than Organization text;
directory volume/file identity rather than PATH spelling; and kill-on-close plus breakaway denial.
PATH, adjacency, environment, and a caller/install-command digest never discover installer authority.

**Resource ledgers begin before route selection and cross every front door.** Test bootstrap, full CLI,
direct native, rg/sidecar delegation, and every matcher engine independently. An engine that cannot
charge construction and inner-loop work is refused before child creation. For each limit, observe
cap−1/cap/cap+1 and mixed-source totals; accepting only separate counters or rejecting the inclusive cap
must fail unchanged tests.

**Static manifest ≠ live execution receipt.** The committed manifest defines exact required nodes/jobs
and contains no live run identity. The verifier independently derives repository/commit/run/attempt/job/
runner/artifact namespace from the current Actions context, then cross-checks receipt fields, Python
JUnit population, and stable-Rust node census. A JSON record that merely repeats its own identity fields
is self-attestation, not anti-replay evidence.

**Review failures are handled narrowly.** If a broad review prompt times out, retry the exact disputed
paragraph/invariant. Preserve severity and final-vendor validation. A no-verdict seat is recorded as
failed and replaced; it is never inferred as `SHIP` and need not stall all progress indefinitely.

**RED and CI evidence must discriminate (2026-08-03, AGENTS A61–A82).** Behavioral RED pins the exact
expected reason — crash, import, panic, and setup errors are not RED. Route/start evidence comes from
the actual producer/constructor and test-owned OS/raw proof, never a hardcoded bool or a production
hook that self-attests before start. Containment authenticates writer/client provenance and proves
alive-before → dead-after plus cleanup; Event/EOF/PID text alone is insufficient. Crypto negatives use
a valid API operation, an exact refusal class, and an exportable/trusted positive control. Security
grammar validates full sections/types/flags/effective authority and rejects unknown and inherit-only
forms; substring principals are not acceptance. Resource-owning protocols name close primitives and
prove exact-once reverse cleanup on success, `BaseException`, and cleanup failure while preserving the
primary error. RED scaffolds cannot enable partial public behavior or unbounded work before the guard.
Immutable-SHA CI clearance needs a real run, expected per-node outcomes, raw artifacts, and the exact
population — no run is no clearance. Security green is point-in-time: a fresh fixable advisory blocks
merge and is upgraded across every live direct/constraint floor, the lock, validator tests, and user
remediation text before a new exact-head audit; never ignore a vulnerability with a fixed release.
**A77–A82 (2026-08-06 PM):** never pipe `gh pr checks` into a stdin-eating heredoc (false ALL_TERMINAL);
usage-limit seats are FAILED not pending; READY→BLOCKED stamps retarget governance pins in the same PR;
gate tip bytes under review not archaeological RED SHAs; HIGH receipts ≠ Sol SHIP; AMEND_SPINE when
board READY contradicts reconcile BLOCKED (START_NOW = docs/R0/D1 only).

**Search twins and respect public boundaries.** After retiring a defect shape, grep sibling adapters and
helpers for the same pattern; a zero-retry fix in `RustCoreBackend` did not protect two copies in
`CPUBackend`. Separately, zero in-repo callers cannot authorize deleting an exported Rust `rlib` method.
Pin exact public function types and require an explicit breaking/deprecation/migration decision for
removal.

**No-follow safety begins before the helper call.** Treat `.resolve()`/`realpath()` on a caller-selected
leaf before an approved writer as a violation because it erases symlink identity. Handle-relative
publication alone is also insufficient when missing directories are created path-wise: anchor directory
creation, temporary creation, and publication to opened identity-verified parents, then Event-test leaf
and parent/junction swaps on Unix and Windows.

The same anchoring rule covers stable lock/fence creation, reads and publication of the protected index,
and repository-controlled configuration reads. Bound config file/count/aggregate bytes, reject mappings
outside the workspace before reading targets, and Event-test intermediate-parent swaps. Every deferred
security/compatibility behavior gets a stable tracker ID, owner, threat boundary, and reopen trigger.

---

## Part 7 — Push discipline & the push-race (one-merge-per-tick)

**The real publish is the `Semantic Release` JOB inside `.github/workflows/ci.yml`**, gated `github.ref == 'refs/heads/main' && github.event_name == 'push'`. `release.yml` is `workflow_dispatch`-only, so a manually-pushed `v*` tag **cannot** bypass semantic-release (`AGENTS.md:2723`).

That job **compiles native assets before publishing → it runs ~6 minutes**, and that entire window is a race window:

> If **any** other merge lands on `main` during that window — **including a no-release `docs:`/`chore:` PR** — it advances `main`, and the in-flight release's final `git push origin main` (the `chore(release)` version-bump commit) is **rejected non-fast-forward** (`! [rejected] main -> main`), so **that version never publishes**.

**Why / incident:** `v1.17.23` (a security batch, #318) failed to publish because the GPU-pause `docs:` PR (#319) was merged while #318's release job was still compiling assets (`AGENTS.md:840`). The CI concurrency group serializes *runs*, not the *human act of clicking merge* — it is necessary but **insufficient**.

**Discipline = one-merge-per-tick:** merge ONE → wait for its `chore(release): vX [skip ci]` commit on `main` **and** the new version on PyPI → then merge the next. "Safe to interleave" means *after the prior release fully published*, not after its PR CI is green (`AGENTS.md:834`).

**Recovery — do NOT panic-rerun:** the failure self-heals. The next push-to-`main` re-runs `Semantic Release`; because the version is **derived from git tags** (not the failed run's state), it recomputes the correct next version and covers the orphaned `fix:`/`feat:` commit. The fix's *code* was already on `main` — only the publish step was behind. Diagnose by decoding the structured job result first: `gh run view <id> --json jobs` → find `Semantic Release` → `--log-failed`. A `! [rejected] main -> main` line is the push-race signature (`AGENTS.md:844`).

**A second, DIFFERENT release-failure shape (C-release-flake) — rerun immediately; do not wait it out.** A flaky `needs:`-list job (e.g. a timing-sensitive lock-concurrency test, a transient dependency-install flake) can make `Semantic Release` report `skipped` rather than `failure` — no tag, no `chore(release)` commit, PyPI unchanged. This is **not** the push-race shape (no `! [rejected]` line). Do NOT wait for an unrelated push to clear it: the recovery is `gh run rerun --failed` on the SAME run, immediately (re-executes only the failed job, not the whole pipeline). A later green main push CAN also self-heal this shape — the `Semantic Release` job runs on every eligible main push (verify the `release` job's `if:` condition: `grep -n "github.event_name == 'push'" .github/workflows/ci.yml` — it additionally guards `!contains(github.event.head_commit.message, 'skip release')`) and the version is **derived from git tags**, not the failed run's state — but "a future push will eventually clear it" is not a strategy, it is an abandoned rerun; the failed run is the thing you own, so rerun it. Receipts: v1.76.9/#612-613 (a timing-flaky heartbeat test widened + rerun), v1.92.2/#701 (the index-lock concurrency test rewritten to a scheduler-independent Event-handshake contract after 2 releases of flaking). **Tell the two shapes apart by reading the job conclusion, not by symptom-guessing:** `! [rejected] main -> main` in the `Semantic Release` job's own log = push-race, self-heals; a `skipped` conclusion with no rejection line = a `needs:`-job flake, needs `gh run rerun --failed`. Cross-link: `tensor-grep-debugging-playbook` §2.

Other push rules: don't push from a dirty worktree if `origin/main` moved with unrelated local changes; a branch push / open PR starts **PR CI only** — it is not a release (`AGENTS.md:830-832`).

### Precedence: strict serialization is the DEFAULT; batch forms are narrow, named exceptions

The merge regimes below do NOT sit at equal weight — when in doubt, the strictest applies:

1. **DEFAULT — one-merge-per-tick (strict serialization).** For any release-bearing PR: merge ONE →
   wait for its `chore(release)` commit on `main` AND the new version on PyPI → then merge the next.
   The Part 10 checklist item enforces this default.
2. **Narrow MONITORED exception — C-batch (next subsection).** Several ALREADY-CI-green releasing PRs
   may merge ~15-20s apart in ONE gate-open window and produce one combined release, but ONLY when:
   every PR in the batch is already independently green, the merges happen inside one green window, and
   the operator watches the NEWEST main run to full completion (run-id polled by id, job population
   present — see "Two merge-gate blind spots" below), not just each PR's own CI.
3. **Non-releasing PRs (`docs:`/`test:`/`chore:`/`bench:`) batch freely ONLY when no release is in
   flight or planned** (A31; AGENTS.md "Batch the non-releasing, serialize the releasing" — grep the
   phrase, don't stamp the line). They create no publish to race, so their only gate is "the newest
   main run completed"; the moment a release is in flight or next in the queue, they fall back to the
   DEFAULT wait, because ANY merge landing inside a release window can reject the in-flight publish
   (v1.17.23/#318/#319 receipt above).

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

The **"~6 minutes" figure above (and at `AGENTS.md:2723`) is stale** — it describes only the `Semantic Release` job's own runtime (still accurate: ~4-5 min in isolation), not the real race window. The real danger window is **squash-merge lands → `chore(release)` commit successfully pushed to `main`**, because `Semantic Release` cannot even *start* until every job in its `needs:` list finishes (`.github/workflows/ci.yml:943`), and that list now includes a 4-OS `native-build-smoke` matrix plus `benchmark-regression`. Measured against four consecutive real releases (`gh run view <run-id> --json jobs`, PR merge → `chore(release)` commit timestamp → `gh run` job `completedAt`):

| Release | PR / commit | push → `chore(release)` on `main` | push → `publish-pypi` | push → `release-tag-smoke` (final gate) |
|---|---|---|---|---|
| v1.19.0 | #343 `ab717a1` | 25m29s | 43m08s | 47m09s |
| v1.19.1 | #344 `80de0b4` | 22m38s | 40m07s | 44m18s |
| v1.19.2 | #345 `bb5dc59` | 43m39s | 1h01m24s | 1h05m48s |
| v1.19.3 | #346 `6b7b518` | 39m55s | 59m16s | 1h03m06s |

So: **~23-44 min before the version-bump commit is even on `main`**, and **~40-66 min before PyPI/the final release-tag-smoke gate confirms full publish**. Treat "~40 minutes" as the practical minimum wait before checking "did the prior release finish yet", not an upper bound — the slower runs (v1.19.2, v1.19.3) topped an hour. **This table's numbers are still NOT re-measured as of this pass (v1.95.0) — they remain the v1.19.x historical sample; treat them as illustrative of the SHAPE of the wait (a 4-OS native-build matrix is the long pole), not as a current SLA.**

**Long pole:** `native-build-smoke (macos-15-intel)` (`ci.yml:549-558`) is **consistently the slowest of its own 4-OS matrix** — every run measured: 15m09s, 9m14s, 15m43s, 11m43s (avg ~13 min) vs ~5 min for `ubuntu-latest`/`macos-latest` and ~10 min for `windows-latest`. It was the exact job whose completion unblocked `Semantic Release`'s start in 2 of the 4 runs (down to single-digit seconds: v1.19.0 completed 12:33:45, `Semantic Release` started 12:33:48; v1.19.2 completed 14:37:54, `Semantic Release` started 14:37:57). In the other 2 runs, `benchmark-regression (ubuntu-latest)` finished a couple minutes later and was the actual pole instead — the two jobs alternate as the true bottleneck, so don't tune a watcher to only one of them. After `Semantic Release` publishes, `build-release-native-assets (macos-15-intel, cpu)` (`:1159-1162`) repeats the same slow-OS pattern (9-12 min, once the whole pipeline's single longest job) before `publish-pypi` and `release-tag-smoke` can run.

**Gate a sequential merge-watcher on ABSOLUTE conditions, never "has the tag/commit changed since I started watching":**

- **Correct:** poll `gh pr view <N> --json state -q .state` until it reads `MERGED`, then **capture the release run's ID once and poll THAT ID** — `gh run view <run-id> --json status,conclusion` — until it reports `status == "completed"` and `conclusion == "success"`, and independently confirm PyPI carries the new version.

  ⚠ **NOT `gh run list --branch main --limit N`.** That form was prescribed here until 2026-08-01 and it is BROKEN as a merge gate: it is a windowed query, so unrelated rows (a second `Push on main`, a Dependabot `Graph Update`) fill the window and push the real release run out of it. Measured that day: `--limit 3` reported **0 runs in flight** while the release was mid-publish; `--limit 20` found it; `--limit 1` — the strictest form, and the one this bullet used to recommend — is the most exposed of all. Merging on that reading rejects the in-flight release's push. Query ONE OBJECT BY ITS UNIQUE ID, never a list plus a filter (`tensor-grep-debugging-playbook` §20).

  Also check the **job's own** conclusion, not the run's: `release-tag-smoke` sat red for four releases while PyPI kept publishing, and a run-level roll-up hid it.

  ⚠ **The same law binds the PR side, and it is a DIFFERENT gate people get wrong (2026-08-02, #903).** To decide "is this PR's suite finished", do NOT break on *"nothing is pending in `gh pr checks`"*. **The check rollup is a list that is still being BUILT** — GitHub dispatches jobs progressively, so early in a run every check present can be terminal while most of the suite has not been created yet. Measured on #903: the rollup held **11** checks where the comparable docs PR #901 had **48** — `Formatting & Linting`, all six `test-python`, all six `test-rust-core`, `native-build-smoke` and `search-golden-parity` among the 37 absent. The run itself then reported `jobs dispatched=3`, and minutes later `30`. A monitor keyed on "no pending" would have printed `TERMINAL, failures=0` and merged a PR whose tests never ran.

  **The correct PR-completion gate, same shape as the main gate above:** capture the `ci.yml` run id for the head SHA (`gh run list --branch <branch> --workflow ci.yml --json databaseId,headSha`), then poll **that id** — `gh run view <id> --json status,conclusion` — until `status == "completed"`, and carry a **job-count floor** so a run that concludes with a handful of jobs is treated as not-having-run rather than green. A rollup answers "is anything pending *of what exists so far*"; only the run answers "is it done".

  **Population check, cheap and decisive:** compare the check count against a recently-merged comparable PR. `11` vs `48` was the entire tell, and it is invisible without the sibling. (Benign neighbour: `cancelled` runs on superseded SHAs are your own force-pushes, not failures.)

  This trap was already documented in the operator's memory index — *"queried BY RUN ID"* and *"guard the total-check count"*, in one sentence — and a monitor violating both was built anyway in the same session. **That is why it is written HERE, in the gate's own definition, instead of only in a lesson file: a documented rule does not fire on its own.**
- **Wrong / deadlocks:** "wait until the release tag / `chore(release)` commit differs from what it was when my watcher launched." If the prior release **already finished publishing between the last time you looked and the moment the watcher actually started** — normal in a fast merge sequence like v1.19.0→v1.19.3 above, all landed inside roughly an hour — the tag is *already* at the target value the instant the watcher begins polling, so a changed-since-launch condition never fires and the watcher hangs forever on an event that already happened. Compare current absolute state against the registry/PR API, never against a snapshot taken at launch time.
- **This v1.19.x sequence is itself the reaffirmation receipt for one-merge-per-tick:** four releases merged one at a time, each waited out to a confirmed publish before the next merge started, and **zero** `! [rejected] main -> main` push-races occurred — contrast the `v1.17.23`/#319 incident above, which is exactly what happens when that wait is skipped. Two PR-CI runs in this window did report `conclusion: "failure"` (`28657702879` — a capfd-vs-stdout test-capture regression, the round-4 ledger's `--rank`-routing item; `28648738456` — a one-off `macos-15-intel` native-asset build failure): both were **ordinary red PR-branch CI**, fixed and re-pushed before merge, not push-races. Triage tell: a red run on a **PR branch** is a normal fix-and-repush gate; a red **`main`**-branch `Semantic Release` push step with `! [rejected]` in its log is the push-race signature and self-heals on the next push (see above — don't panic-rerun).

---

### Two merge-gate blind spots (2026-08-04)

- **A release landing mid-review invalidates every open PR's green, and per-PR CI cannot see it by
  construction.** A PR's checks run against a base predating the release; the release ships; the
  identical commit is out of tolerance at merge. Receipt: v1.103.0 published 21:06Z, #928 merged
  green 21:32Z and reddened main (run 30952799876). This is the Form 10 semantic-merge law with TIME
  rather than content as the second slice, so rebase-and-run-the-union does not cover it -- the
  colliding slice did not exist when the union ran. **Gate: before clicking merge, check whether a
  release published since the PR's last CI run** (compare the CHANGELOG.md head / latest
  `chore(release)` commit timestamp against the PR run's timestamp; if a release landed, re-run the
  PR's CI or rebase first). And read the FAILURE COUNT, not the red-row count: #930 showed "7
  failing lanes" that were ONE gate (6331 passed, 1 failed).
- **A `needs:`-gated job is ABSENT, not pending, until its gate finishes.** Twelve jobs are
  smoke-gated — re-derive with BOTH forms, because since #977's `changes` job ten read
  `needs: [smoke, changes]` and only two still read bare `needs: smoke`, so the old single-form
  `grep -c 'needs: smoke'` under-counts 12 as 2:
  `grep -cE 'needs: (smoke|\[smoke, changes\])' .github/workflows/ci.yml` — plus `release`
  naming smoke in its needs list, so pre-smoke a PR exposes only the ungated check-runs and a settle
  probe of `all(bucket != "pending")` is VACUOUSLY TRUE over a view that structurally cannot contain
  a test lane (observed: 11 -> 39 check-runs the instant smoke ended). **A merge or settle gate must
  assert the heavy lanes are PRESENT by name or count**, never "nothing is pending".

### Session-retention lessons (2026-08-12)

- **Stale-branch content reconciliation: `git cherry` + proves PATCH-ID distinctness, NOT novel
  content.** A clean `git cherry` only says the patches are not byte-identical to upstream; the same
  fix can land as a different patch. Enumerate the branch's touched paths, diff EACH endpoint against
  the target, and confirm the symbols/tests actually exist on the target; for historical-blob claims
  use blob identity (`git rev-parse <rev>:<path>`) plus pickaxe (`git log -S"<exact string>"`), not
  commit-message reading.
- **Reconciliation is not cleanup.** Preserve dirty/untracked state exactly as found; stage only
  explicit paths; deletions are PROPOSED, never executed, unless separately authorized. Same root as
  Part 1 Rule 7: foreign tree state is evidence, not litter.
- **Long-lived published branches: union-MERGE `main` INTO the branch (not rebase)**, then verify the
  per-node outcome map before/after the merge plus a live job-population census — a rebase rewrites
  the branch's published history and invalidates every prior run/verdict keyed on its SHAs.
- **Mixed dispositions stay mixed (A41).** `shipped + blocked`, `fixed + retired`,
  `implemented + demand-gated` are each TWO outcomes, not one; never flatten a sub-outcome pair into
  a single flattering word when recording status.

## Part 8 — PR title drives release intent

CI infers the semantic-release bump from the **PR title** (which becomes the squash-merge commit subject). Use conventional titles (`CONTRIBUTING.md:46-51`, `AGENTS.md:880-889`):

| Title prefix | Effect |
|---|---|
| `feat: ...` | minor release |
| `fix: ...` / `perf: ...` | patch release |
| `feat!: ...` / `fix!: ...` | major release |
| `docs:` / `test:` / `chore:` / `ci:` / `build:` / `refactor:` | **no release** |

> **`refactor:` publishes NOTHING (measured on PR #915, merged `3faf500`)** — a frequent surprise. Two authorities govern, and they disagree: `scripts/validate_pr_title_semver.py` (`_RELEASE_INTENTS`) ACCEPTS `refactor:` as patch-intent, but that script only gates the TITLE. The PUBLISHER, `[tool.semantic_release]` in `pyproject.toml`, sets no `commit_parser`/`patch_tags`, so the default angular parser applies and its patch types are `fix`/`perf` only. Measured on PR #915: Semantic Release logged *"No release will be made, 1.102.4 has already been released!"*, `publish-pypi` was skipped, and PyPI stayed at 1.102.4 — an unreleased `refactor:` ships with the next `fix:`/`feat:` merge. Re-verify BOTH authorities: `grep -A12 _RELEASE_INTENTS scripts/validate_pr_title_semver.py` (what the title gate accepts) and the `[tool.semantic_release]` block in `pyproject.toml` (what actually ships).

- Use **Squash and merge** for release-bearing PRs so the validated title becomes the `main` subject.
- **Do not manually create release tags** while semantic-release is active.
- A release-bearing fix is **not complete** after only a branch push / open PR / green PR checks. The final report must name: PR, merge commit, main CI run, CodeQL run, released tag, PyPI publish status, and any public installer dogfood result (`AGENTS.md:862`).
- **PR metadata is part of the reviewed artifact.** Re-read the title, body, comments, examples, and
  counts after every scope-changing push. State each count's population/denominator. PR #910 was green
  while its Markdown/Python example was malformed and its status counts were stale; independent prose
  review, not CI, caught both.

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

**Before `git checkout -b` / `git stash` / any branch switch during a live dogfood session,**
run `python scripts/check_unstaged_skill_edits.py`. Three times in one campaign a real skill
correction under `.claude/skills/*/SKILL.md` — once a REVERSAL nobody would re-derive from
memory — sat MODIFIED but unstaged in the main checkout, a branch switch away from being lost.
The script fires only on the unstaged half of a change (staged/committed edits are silent, by
design) and is silent itself on a clean tree; see `tests/unit/test_unstaged_skill_edit_guard.py`
for the guard's own regression coverage. It is not wired into CI — a CI checkout is always
clean, so it can never fire there and would be theater; it exists for the local/drain-session
moment where the loss actually happens.

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
- [ ] Relying on a census/coverage list (registration sites, argv-sentinel guards) → each member **CALLED** (guard deleted, suite re-run red) not reasoned as covered, and keyed on the **artifact**, not a shared mechanism (Part 3).
- [ ] Census includes generated interpreters, aliases/shadowing, independent raw candidates, and
  ordinary/generated mutation controls; sanctions are exact callsite fingerprints.
- [ ] Caller-selected writer touched → raw leaf identity preserved; directory creation and publication
  parent-handle anchored; Event-gated leaf and parent/junction swaps green on affected platforms.
- [ ] Plan gate → one canonical hash method used by every seat; producer/service/registration exists
  before each consumer and behavioral RED.
- [ ] Lock/config reader touched → fence plus protected RMW/read share one verified directory handle;
  config file/count/aggregate caps and out-of-workspace/swap tests are green.
- [ ] Deferred security behavior → stable canonical ID, owner, threat boundary, and reopen trigger.
- [ ] Draft implementation PR exists → owning tracker row is `IN_FLIGHT` with its real PR number.
- [ ] Class fix → sibling/twin shapes searched; public API retained unless a deliberate breaking plan
  authorizes removal.
- [ ] A "DRY, extract the shared helper" refactor → checked that neither call site is a generated string later written to disk / `exec`'d / run standalone (Part 6).
- [ ] Any defect noticed in passing, regardless of authorship/CI-visibility/scope → fixed now or filed as a concrete tracked blocker, never waved past (Part 1 Rule 7).
- [ ] Local gate green: `ruff check` + `ruff format --check --preview` + `mypy src/tensor_grep` + `pytest -q`.
- [ ] Subagent claims **re-run in the real venv** — none trusted as-reported.
- [ ] WSL and Windows venv roots stayed disjoint — no WSL `uv --project /mnt/c/...` touched the
  canonical Windows `.venv`; canonical verification ran from PowerShell.
- [ ] PR title matches intended release bump; **squash-merge** for release-bearing.
- [ ] PR body/comments/examples/count denominators re-reviewed against the final head commit.
- [ ] Merging: prior release **fully published** (its `chore(release)` on `main` + PyPI shows it) before this merge — **one-merge-per-tick** is the DEFAULT; C-batch is the narrow monitored exception, and non-releasing PRs batch only in a release-free gap (Part 7 "Precedence").
- [ ] Autonomous work stops at a **draft PR** — no auto/admin-merge.

---

## Provenance and maintenance

Volatile facts are dated **2026-07-02, release `v1.17.25`**, with a round-4 refresh dated **2026-07-03, release `v1.19.3`** (Part 7 wall-time section + this table's tag/wall-time rows), a **2026-07-08, release `v1.49.3`** touch-up (Part 1 Rule 5 / Part 10 adversarial-security-gate addition — the Part 7 wall-time numbers themselves are NOT re-measured at v1.49.3, treat them as an illustrative historical sample, not a current SLA), a **2026-07-16, release `v1.78.1`** fix (the stale `37 @app.command` count, actual 44, replaced with a re-verify command instead of a stamped number), a **2026-07-22, release `v1.93.2`** addition (Part 1 Rule 6 pin-first ranking gate / C-pin, #709; Part 7 rapid-window batch-merge / C-batch, #703-706; Part 7 second release-failure shape / C-release-flake, v1.76.9/#612-613 and v1.92.2/#701 — the Part 7 wall-time numbers again NOT re-measured in this pass), and a **2026-07-23, release `v1.95.0`** refresh (Part 3 gained a 3rd registration table — the symbol-graph language registry's 5 seams, `lang_registry.register_language` + `repo_map.py` citations; Part 4 gained a grammar-missing fail-closed worked example; Part 7 gained the sequential-drain union-rebase corollary (C4); Part 9 gained the CRLF-binary-preserve edit landmine and the `uv.lock` hand-splice discipline (C1/C2); and every pre-existing `file:line` citation into Rust/Python source, test, and workflow files in this skill was re-walked against `origin/main` and repointed where drifted — several had moved 20-300 lines since the last pass (e.g. `main.rs`'s `enum Commands` 838→889, the `Semantic Release` job's `needs:` list in `ci.yml` 862→943, `ripgrep_backend.py`'s fail-closed raise sites 88/164/199→126/297/413, which ALSO now raise `BackendExecutionError` there instead of a bare `RuntimeError`). AGENTS.md's own prose citations were re-pointed too (its "Current Handoff" section grew substantially since the last pass), but AGENTS.md is itself mid-refresh in this same campaign, so treat any `AGENTS.md:NNN` citation below as good only as of `v1.95.0` — re-grep by symbol/phrase, don't trust the number blind, before citing it in a future pass. The Part 7 wall-time numbers themselves are STILL not re-measured in this pass — they remain the v1.19.x historical sample. A **2026-07-24, release `v1.98.2`** pass added Part 6's banked-hypothesis (#736) and gate's-disclosed-edge (#736) paragraphs. A further same-day pass, **release `v1.98.3`**, added Part 6's four newest paragraphs (a test proving nothing until seen fail on the pre-fix baseline, #737; gating comments/docstrings with the same rigor as code, #739; diff-review-is-not-measurement-review, #739; the `ast.dump()` behavior-neutral proof technique). A coordinator review of that same pass added a fifth Part 6 paragraph (a verification instrument — a malformed `grep -E \|` alternation — can itself be the thing that's wrong) and the concrete clock-resolution figure into the CI/Release Rules mirror of the timing-flake lesson. A
**2026-07-31** pass added Part 1 Rule 7 ("not mine"/"CI doesn't flag it" is not a disposition), a new Part
3 subsection on census-population fallibility (curate the list by deletion-proof, enumerate by artifact
not shared mechanism), and a new Part 6 paragraph (refactoring code that lives inside a generated string
is not refactoring code) — doc-only, no release tag re-verified for this pass. A **2026-08-01** pass
added a Part 3 subsection on shared-builder flag placement (`-q` landed in `RipgrepBackend._build_cmd`,
the shared argv builder, and suppressed output for the three of its four consumers that parse rather than
stream — #876, fixed #880: enumerate consumers before adding a flag that changes output) and a Part 6
one-liner requiring a plan to prove its stated base commit with `git rev-parse origin/main` rather than
have an auditor accept the header — doc-only, no release tag re-verified for this pass. Re-verify anything below before relying on it:

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
| `TG_RG_TIMEOUT_SECONDS` default | `grep -n TG_RG_TIMEOUT_SECONDS src/tensor_grep/cli/subprocess_policy.py` (currently `60.0`, `subprocess_policy.py:75`; the `600` figure AGENTS.md still cites at `:853` predates this default and reads as present-tense there — re-verify whether that AGENTS.md line is itself stale before trusting it) |
| Security round-3 sweep files | `AGENTS.md` "Security Hardening Patterns"; files `src/tensor_grep/cli/{checkpoint_store,session_daemon,session_store,mcp_server}.py` |
| Open round-4 argv item | `AGENTS.md` (native-argv `--` sentinel); `rust_core/src/rg_passthrough.rs` |
| Dogfood harness present | `ls scripts/dogfood/` (`Dockerfile`, `dogfood_features.py`, `README.md`) |
| #737 shape-9/9a/9b test split (pre-fix-baseline paragraph) | `grep -n "shape9a_filescope_member_fn_ptr_variable\|shape9b_inclass_member_fn_ptr_variable" tests/unit/test_lang_cpp.py` |
| #739 structural marker-order test (ast.dump / comment-gating paragraphs) | `grep -n "def test_create_checkpoint_lock_does_not_wrap_expensive_work" tests/unit/test_index_lock_concurrency.py` |

## Retention folds (2026-08-13)

- **A102 — input-brief facts are hypotheses.** When dispatching a fix/build seat, the brief's stated
  facts (version numbers, SHAs, "already shipped" claims) are hypotheses until the seat re-derives
  them from the tree; two of seven retention fix-wave seats corrected facts in their own briefs.
  Require seats to verify load-bearing input facts before writing and to report any that fail.
- **A98 — spot-check census.** Declaring N dirty files "stale"/"novel" from one file's header is a
  claim about the one file checked; use a mechanical per-file diff or explicit per-file disposition.

If any command above no longer matches, update this skill in the same change — a wrong runbook is worse than none.


## Retention (2026-08-15) — merge + docs CI traps

- **A118:** Local gh pr merge can fail with main already used by another worktree while GitHub
  already merged. Judge gh pr view --json mergedAt (or the merge API); never double-merge.
- **A119:** Docs-only PR changes skips are not a cheap main push — main always runs the full matrix.
- **A117:** Operator “skip Fable” waives that seat for the named docs packet only — not product code,
  spend, or CEO_GATED flips (extends A74).
- **A120:** A shell timeout must exceed the probe duration plus grace; otherwise the harness,
  rather than the subject, creates the timeout.
- **A121:** Raising `request_queue_size` without an aggregate pre-auth cap enlarges the DoS surface.
- **A122:** Demand evidence plus a merged design is not shipment; product work still needs explicit
  authorization and its required build/security gates. See `tensor-grep-demand-gate-measurement` for
  the demand proof and `tensor-grep-design-authorization-ladder` for design authorization.

