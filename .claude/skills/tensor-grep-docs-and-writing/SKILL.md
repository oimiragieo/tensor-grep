---
name: tensor-grep-docs-and-writing
description: Use when writing or editing any tensor-grep doc of record (AGENTS.md, CLAUDE.md, root SKILL.md, docs/SESSION_HANDOFF.md, docs/CONTRACTS.md, docs/CONTINUATION_PLAN.md, docs/PAPER.md, README.md, docs/benchmarks.md, docs/gpu_crossover.md, mkdocs.yml, or any .claude/skills/*/SKILL.md) — before adding a capability claim, syncing a release-line note, touching a version-stamped line, adding a new governed doc, or a docs-only pytest fails and you don't know which fragment broke it. Covers which doc owns which contract, the two governance layers (semantic-release version_variables + scripts/stamp_release_assets.py auto-stamping vs pytest content-pinning), the cross-doc fragment discipline, mkdocs --strict, and house style.
---

# tensor-grep docs and writing

This is the **docs-of-record runbook**: which file owns which contract, how versions get auto-stamped into prose, how the content-pinning tests work, and how to edit a governed doc without silently redding a test three files away. `tensor-grep` treats docs as **part of the product contract**, not an afterthought — `CONTRIBUTING.md:57-59` and `AGENTS.md` rule 6 both say a workflow/release/docs-contract change is **incomplete** until the matching validator test is updated.

## Who this is for

Two readers at once — write and act to the **lower bound** of each:

- A **Sonnet-class AI** editing docs autonomously: you need the exact file list, exact grep commands, and a hard stop before you delete a pinned sentence.
- A **mid-level human engineer**: you need to understand *why* this repo pins prose with `assert "..." in doc` instead of a single source-of-truth link, so you don't fight the system.

## When to use this skill vs a sibling

| Your task | Use |
|---|---|
| Editing/adding prose in AGENTS.md, CLAUDE.md, root SKILL.md, docs/*, or a `.claude/skills/*/SKILL.md`; "why did my docs edit fail a test" | **this skill** |
| Deciding *whether* a change is allowed to land at all (gates, registration sites, push-race) | `tensor-grep-change-control` |
| The exact external speed/GPU/LSP claim wording rules and semantic-release publish mechanics | `tensor-grep-release-and-positioning` |
| Load-bearing design of the front door / backend contract (the code the docs describe) | `tensor-grep-architecture-contract` |
| A past incident's full story (why a doc says what it says) | `tensor-grep-failure-archaeology` |
| Running/interpreting a benchmark whose numbers get pasted into docs/benchmarks.md or docs/PAPER.md | `tensor-grep-benchmark-and-proof-toolkit` |
| CI/validation-suite mechanics behind the docs gates (`release-readiness`, `ruff format --check --preview`) | `tensor-grep-validation-and-qa` |
| Actually *using* `tg` to search/navigate while writing docs | `tensor-grep` (the usage skill) or `code-search-and-retrieval-reference` |

**No skill routes around change-control.** Docs-contract changes still need a validator-backed test update (`AGENTS.md` rule 6) — this skill tells you *which* test, not an excuse to skip it.

---

## Part 1 — The doc-of-record map

Every governed doc has one job. Do not duplicate another doc's job into it — that is exactly what caused the README to grow an unmaintainable per-release ledger (see Part 5).

| Doc | Owns | Auto-stamped? | Pinned by |
|---|---|---|---|
| `AGENTS.md` | Master agent/contributor rulebook: operating rules, registration sites, security-hardening lens, roadmap sequencing, dogfood-follow-up workflow, current handoff/weak-spots | `release_docs_current_tag:` line + prose (see Part 2) | `tests/unit/test_public_docs_governance.py` (heavy) |
| `CLAUDE.md` (repo root) | **Thin DRY pointer** to `AGENTS.md` for Claude Code — a bullet summary of AGENTS.md sections, nothing load-bearing of its own | no | none (discipline only — see Part 3) |
| `SKILL.md` (repo root) | Governed release/product-positioning doc: current release facts, product read, known weak spots, capsule contract summary | yes | `test_public_docs_governance.py` under the variable name `SKILL_DOC_PATH` |
| `.claude/skills/tensor-grep/SKILL.md` | The **tg-usage skill** (command patterns for an agent driving `tg`) — same basename as root `SKILL.md`, unrelated file, see Part 3 | no | ONE test: `test_benchmark_scripts.py::test_tensor_grep_claude_skill_should_require_non_interactive_action` |
| `.claude/skills/<topic>/SKILL.md` (this library, incl. this file) | Narrow topic runbooks | no | none currently (verified 2026-07-02 — re-check before relying on this) |
| `README.md` | **Marketing/positioning front door only.** NOT the detailed-contract source of truth since the 2026-06-25 rewrite incident | release-ledger links + `post-\`vX\`` GPU labels | `test_public_docs_governance.py` (positive pointers + negative "no ledger regrowth" guards), `test_enterprise_docs_governance.py` |
| `docs/SESSION_HANDOFF.md` | **Live** handoff: current release state, weak spots, per-slice PR/dogfood evidence ledger | yes | `test_public_docs_governance.py` (heavy) |
| `docs/CONTINUATION_PLAN.md` | Historical workstream map; secondary to `SESSION_HANDOFF.md` for "what's current" | yes | `test_public_docs_governance.py` |
| `docs/CONTRACTS.md` | API/CLI/data backward-compatibility guarantees, validated compatibility set | yes | `test_public_docs_governance.py` + `test_enterprise_docs_governance.py` |
| `docs/PAPER.md` | Optimization/benchmark history, **including rejected/failed attempts** — append dated notes, never delete history | GPU dogfood `post-\`vX\`` labels only | `test_public_docs_governance.py` (GPU-story tests) |
| `docs/benchmarks.md` | Accepted benchmark artifacts, frozen comparator sets/scenario packs | GPU dogfood labels | heavy pins across both governance files |
| `docs/gpu_crossover.md` | GPU crossover story / promotion gates | GPU dogfood labels | pinned |
| `docs/routing_policy.md`, `docs/tool_comparison.md`, `docs/world_class_plan.md` | Backend routing policy; comparator positioning; roadmap/closed-program ledger | no | `test_public_docs_governance.py` (dedicated tests per doc) |
| `docs/CI_PIPELINE.md` | **Canonical** CI/release/supply-chain pipeline contract — read before editing `.github/workflows/*.yml` | no | `test_enterprise_docs_governance.py` |
| `docs/SUPPORT_MATRIX.md`, `docs/HOTFIX_PROCEDURE.md`, `docs/EXPERIMENTAL.md`, `docs/RELEASE_CHECKLIST.md`, `docs/installation.md`, `docs/index.md`, `docs/architecture.md`, `docs/package_manager_publish.md`, `docs/runbooks/*` | Enterprise-doc set + published mkdocs site content | no | `test_enterprise_docs_governance.py` + `mkdocs build --strict` (Part 4) |
| `CONTRIBUTING.md` | Contributor process rules; also lists the "Enterprise Docs" set to keep aligned before calling a release enterprise-ready | no | `test_enterprise_docs_governance.py::test_contributing_should_match_semantic_release_flow` |
| `CHANGELOG.md` | **Single source of per-release fix/feature history** — semantic-release-generated. This is where a per-version ledger belongs, not README.md | generated by semantic-release | not hand-pinned |
| `SECURITY.md` | Vulnerability-reporting process | no | existence-checked from README's link |

Full current list of governed prose files: `AGENTS.md`, `README.md`, `SKILL.md`, `docs/SESSION_HANDOFF.md`, `docs/CONTINUATION_PLAN.md`, `docs/CONTRACTS.md`, `docs/benchmarks.md`, `docs/gpu_crossover.md`, `docs/PAPER.md`, `docs/routing_policy.md`, `docs/tool_comparison.md`, `docs/world_class_plan.md` (`tests/unit/test_public_docs_governance.py`), plus `docs/SUPPORT_MATRIX.md`, `docs/HOTFIX_PROCEDURE.md`, `docs/EXPERIMENTAL.md`, `docs/RELEASE_CHECKLIST.md`, `docs/installation.md`, `docs/index.md`, `docs/CI_PIPELINE.md`, `docs/tool_comparison.md`, `mkdocs.yml`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/runbooks/resident-worker.md`, `docs/runbooks/gpu-troubleshooting.md`, `docs/runbooks/cache-management.md` (`tests/unit/test_enterprise_docs_governance.py`).

---

## Part 2 — Two governance layers (and a fast gate)

### Layer A — Version stamping (automatic; do not hand-edit the stamped bits)

Two mechanisms fire together inside the `Semantic Release` job's `build_command` (`pyproject.toml:127`, `[tool.semantic_release]`):

1. **`version_variables`** (python-semantic-release's built-in regex substitution of `name = "X"` / `name: X` style single-line patterns). Current entries (`pyproject.toml:137-149`):
   - `src/tensor_grep/cli/main.py:pkg_version`
   - `npm/package.json:version`
   - `scripts/tensor-grep.rb:TENSOR_GREP_VERSION`
   - `scripts/oimiragieo.tensor-grep.yaml:PackageVersion`
   - `scripts/oimiragieo.tensor-grep.yaml:InstallerUrl`
   - `AGENTS.md:release_docs_current_tag:tf`, `README.md:release_docs_current_tag:tf`, `SKILL.md:release_docs_current_tag:tf`, `docs/SESSION_HANDOFF.md:release_docs_current_tag:tf`, `docs/CONTINUATION_PLAN.md:release_docs_current_tag:tf`, `docs/CONTRACTS.md:release_docs_current_tag:tf`

   The trailing `:tf` is psr's format-hint suffix (candidate reading: "tag format" — it substitutes the `v`-prefixed **tag**, e.g. `v1.17.25`, not the bare `1.17.25` that plain `version_variables` entries get). This explains why every `release_docs_current_tag:` line carries a `v` prefix. If this ever needs real debugging, check the pinned `python-semantic-release@v9` action's `version_variables` format-hint docs — it is not vendored in this repo.

2. **`scripts/stamp_release_assets.py`** (a companion script this repo wrote, run as a plain step in `build_command` before the `git add`). `version_variables`' one-line regex can't rewrite multi-clause prose or derived URLs, so this script owns everything else: the Homebrew formula body (`scripts/tensor-grep.rb`, handles both a bare `TENSOR_GREP_VERSION = "..."` constant and a raw `version "..."` line), the winget manifest's comment header + `PackageVersion:` + `InstallerUrl:` (which embeds `vX` inside a GitHub download path), and roughly 18 distinct prose regexes across two doc groups (`scripts/stamp_release_assets.py:39-114`):
   - `RELEASE_DOC_PATHS` = `AGENTS.md`, `README.md`, `SKILL.md`, `docs/SESSION_HANDOFF.md`, `docs/CONTINUATION_PLAN.md`, `docs/CONTRACTS.md` — stamps "current tagged version is `vX`", "current `vX` (shell/version resolution|positioning|release line)", "latest complete public PyPI/release-asset distribution is also `vX`", "- Latest tagged version: `vX`", "- Current release tag: `vX`", "- GitHub release: <.../releases/tag/vX>", the PyPI pinned-install proof line, "- GitHub release assets: `vX` has uploaded", and the "Latest tagged/complete PyPI release: [`vX`](.../releases/tag/vX)" link pair.
   - `GPU_DOGFOOD_DOC_PATHS` = `README.md`, `docs/benchmarks.md`, `docs/gpu_crossover.md`, `docs/PAPER.md` — stamps every `` post-`vX` `` label.
   - Run it yourself: `python scripts/stamp_release_assets.py` (writes) or `python scripts/stamp_release_assets.py --check` (rc `1` if any stamped doc has drifted from `pyproject.toml`'s version — this is what CI's `release-readiness` job runs).
   - `build_command` finishes with `git add AGENTS.md README.md SKILL.md docs/SESSION_HANDOFF.md docs/CONTINUATION_PLAN.md docs/CONTRACTS.md docs/benchmarks.md docs/gpu_crossover.md docs/PAPER.md ...` — **stamping a file on disk without adding it here means the commit never includes it.** (See Part 6 for what this means when adding a new governed doc.)

**Do not hand-edit any of the stamped fragments above** (the `release_docs_current_tag:` line, "current tagged version is `vX`", the GitHub-release / PyPI-proof lines, the `` post-`vX` `` labels). They are overwritten on every release; a hand-edit just creates diff noise the next release clobbers. The one deliberate exception, already shipped (`docs/SESSION_HANDOFF.md:38`, `a78e33c fix: harden post-release docs governance`): **"Latest verified release proof" blocks and "What `vX` closed:" narrative are kept SEPARATE from the auto-stamped current-tag labels** specifically so a release commit stays locally testable without a hand-authored proof block going stale the moment the tag line moves. See `tests/unit/test_stamp_release_assets.py::test_stamp_release_assets_preserves_verified_release_proof_blocks` for the exact contract this preserves.

### Layer B — Content-pinning tests (pytest string containment)

`tests/unit/test_public_docs_governance.py` and `tests/unit/test_enterprise_docs_governance.py` do **not** check structure — they check that specific literal fragments exist verbatim (`assert "some exact phrase" in doc`) across specific doc sets. This is the load-bearing house style to internalize:

- A single behavior/claim is frequently required in **multiple docs at once**. Example (`test_public_docs_governance.py:387-421`): the "Dogfood follow-up workflow" fragments (`"PR order"`, `"thinktank"`, `"Gemini"`, `"contract test"`, …) must appear in **all three** of `AGENTS.md`, `SKILL.md`, `docs/SESSION_HANDOFF.md` — add the workflow note to only one and the test fails on the other two.
- Some tests assert **negatively** — a fragment must NOT appear. Two important negative guards:
  - `test_public_docs_should_not_contain_unaccepted_gpu_or_cold_rg_marketing` bans `"mathematically guaranteeing"`, `"0ms interpreter lag"`, `"peak theoretical throughput"`, `"further buries"`, `"designed to win on larger files"`, `"GPU-ready"`, `"GPU-accelerated"` from `README.md`, `docs/benchmarks.md`, `docs/gpu_crossover.md`, `docs/PAPER.md`.
  - The `handoff_docs` loop in `test_handoff_docs_should_record_current_release_state_and_fast_gate` bans `"Latest complete public release PR"` and `"Latest complete public release commit"` from every handoff doc — this is the guard that stops the README's old per-release ledger from regrowing (see Part 5).
- `test_public_ast_positioning_should_not_claim_ast_grep_parity` bans the literal phrase `"ast-grep parity"` everywhere, and requires the accepted alternative phrasing — but the exact accepted wording **differs per doc**: root `SKILL.md` must contain `"validated useful slice"`, `AGENTS.md` must contain `"useful validated AST slice"`. Don't assume uniform wording across docs; check the specific assertion.
- Exact backtick/punctuation matters. `` "current `v1.9.10` positioning" `` and `current tagged version is \`v1.9.10\`` are different literal strings the stamping regexes and the pytest assertions both match on — copy the surrounding punctuation from an existing pinned sentence rather than freehand-typing a new one.

### Layer C — The fast agent-readiness gate (not pytest, runs in seconds)

`scripts/agent_readiness.py` has a `docs-claim-check` probe (`validate_docs_claims`, `scripts/agent_readiness.py:537`) that re-checks a **smaller** fragment set (`f"v{expected_version}"`, `"python scripts/agent_readiness.py"`, `"context_consistency"`, `"tg agent"`, `"agent-capsule-hardcases"`, `"validated compatibility set"`, `"broad generated-root scan"`, `` "rg` remains" ``, `"ast-grep"`) across the same six `RELEASE_DOC_PATHS`-shaped docs, plus a version-drift check using the same "current `vX` (shell/version resolution|positioning|release line)" pattern the stamping script writes. Run it locally as a fast pre-push smoke test:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

This is what caught (and was itself the root cause of 4 wasted CI cycles in) the June-2026 README-rewrite incident — see `tensor-grep-failure-archaeology` for the full story; the operational lesson for docs work specifically is: **decode the structured failing check first**. `docs-claim-check` failing tells you a *version or fragment* problem; it does not by itself tell you *which* pytest in Layer B also broke — run Layer B directly (Part 4) rather than theorizing from the readiness JSON alone.

### Layer D — The published mkdocs site (a separate universe)

`mkdocs.yml` defines a **subset** of `docs/*.md` as the published site nav (currently: `index.md`, `installation.md`, `CI_PIPELINE.md`, `SUPPORT_MATRIX.md`, `CONTRACTS.md`, `EXPERIMENTAL.md`, `RELEASE_CHECKLIST.md`, `HOTFIX_PROCEDURE.md`, `package_manager_publish.md`, `architecture.md`, `benchmarks.md`, `tool_comparison.md` — verify with `grep -A2 '^nav:' mkdocs.yml`). CI's `release-readiness` job runs `mkdocs build --strict` (`.github/workflows/ci.yml:96-99`), which **fails the build on any broken internal link or nav reference**, not just missing content. `docs/SESSION_HANDOFF.md`, `docs/CONTINUATION_PLAN.md`, `docs/PAPER.md`, `docs/gpu_crossover.md`, `docs/routing_policy.md`, `docs/world_class_plan.md` are **repo-internal only** — they are pytest-governed (Layer B) but are NOT part of the published site and don't need mkdocs nav entries. Before editing a file that IS in the nav, run the strict build locally:

```powershell
pip install mkdocs-material
mkdocs build --strict
```

---

## Part 3 — The "SKILL.md" name collision (read this before touching any SKILL.md)

Three different files share (or nearly share) the name `SKILL.md`. Confusing them is the single most likely mistake this skill exists to prevent:

1. **`SKILL.md`** (repo root) — a **governed release/product-positioning doc**, pinned heavily by `test_public_docs_governance.py` under the variable `SKILL_DOC_PATH = Path("SKILL.md")`. Auto-stamped (Part 2, Layer A). Treat edits here with the same discipline as `AGENTS.md`.
2. **`.claude/skills/tensor-grep/SKILL.md`** — the **tg-usage skill**: command patterns, argument order, the registration-audit workflow, for an agent *driving* `tg`. Same basename, unrelated content and governance. It has **no release-state section and no `release_docs_current_tag:` line** — it is NOT part of the version-stamping set (Part 2, Layer A). Its only version reference is an inline `As of vX.Y.Z` note inside the Registration-Audit Workflow section (`.claude/skills/tensor-grep/SKILL.md:69`, currently `v1.17.1`), which is not machine-stamped and must be hand-updated if it goes stale; **do not assume the two `SKILL.md` files need the same edit.** Exactly one pytest reads it: `tests/unit/test_benchmark_scripts.py::test_tensor_grep_claude_skill_should_require_non_interactive_action`, which asserts the file still contains `"do not ask for confirmation"` and `"make the change directly"` (Non-Interactive Mode section, `.claude/skills/tensor-grep/SKILL.md:73,75`) **and** `"want me to apply this?"` (separate Rules section, `.claude/skills/tensor-grep/SKILL.md:86`) (`tests/unit/test_benchmark_scripts.py:9758-9760`). AGENTS.md's own Skills section (`AGENTS.md:249`) says to "Keep it in sync whenever commands/flags change" — that sync is currently **discipline, not full pytest coverage**; only those three literal fragments are machine-checked.
3. **`.claude/skills/<topic>/SKILL.md`** (this file's siblings — `tensor-grep-change-control`, `tensor-grep-architecture-contract`, etc.) — narrow runbooks, one per topic. As of 2026-07-02, **none of these are pytest-pinned** (verified: `grep -rln '\.claude/skills' tests/` matches only `test_benchmark_scripts.py`, which reads only file #2 above). Re-run that grep before relying on this — a future governance test could start pinning this library.

## `CLAUDE.md` stays a pointer — do not duplicate AGENTS.md into it

The repo's own `CLAUDE.md` states its job explicitly: *"Claude Code auto-loads this `CLAUDE.md`; `AGENTS.md` (read by other agents) holds the full rules, so this file points there to keep them DRY."* No pytest currently enforces this (verified: no test reads `CLAUDE.md`), so the only thing stopping it from rotting into a second copy of `AGENTS.md` is discipline. When `AGENTS.md` gains a new load-bearing section, add **one bullet** to `CLAUDE.md`'s summary list (matching its existing bullet style — short imperative phrase + the AGENTS.md section it points to), not the full prose.

---

## Part 4 — Runbook: editing a governed doc without breaking a test you didn't run

1. **Before changing or removing any sentence**, grep the *exact* phrase you're about to touch against the governance suites:
   ```powershell
   uv run pytest tests/unit/test_public_docs_governance.py tests/unit/test_enterprise_docs_governance.py -k "not slow" -q
   ```
   or, cheaper, just text-search for the fragment first (use `tg`, per the workspace's own dogfooding rule):
   ```powershell
   tg search "exact phrase you plan to remove" tests/unit
   ```
   If it's pinned, you have two choices, both legitimate: (a) keep the fragment and change only the surrounding prose, or (b) change the fragment **and** update the pinning assertion in the **same commit** (`AGENTS.md` rule 6 / `CONTRIBUTING.md:57-59`). Silently deleting a pinned sentence without touching the test is not allowed — it is the docs-equivalent of routing around a registration site.
2. **If you're adding a new capability/behavior claim** that should be visible across the doc set (most product-facing behaviors are — check how the closest existing claim is pinned, e.g. `tg agent` / Actionable Context Capsule spans `AGENTS.md`, `README.md`, `SKILL.md`, `docs/CONTRACTS.md`, `docs/SESSION_HANDOFF.md`, `docs/CONTINUATION_PLAN.md` per `test_agent_docs_should_lock_agent_context_capsule_roadmap`), write the **same exact fragment text** into every doc in that group, then either reuse an existing loop-style assertion or add a new one following the pattern of the tests already in `test_public_docs_governance.py` (a `docs = {...}` dict + a `for path, content in docs.items(): assert "..." in content` loop; this file has ~30 such tests to copy the shape from).
3. **Run the full docs-governance surface before pushing:**
   ```powershell
   uv run pytest tests/unit/test_public_docs_governance.py tests/unit/test_enterprise_docs_governance.py tests/unit/test_stamp_release_assets.py -q
   uv run pytest tests/unit/test_benchmark_scripts.py -k tensor_grep_claude_skill -q
   python scripts/agent_readiness.py --output artifacts/agent_readiness.json
   ```
   If you touched a file in mkdocs' nav (Part 2, Layer D), also run `mkdocs build --strict`.
4. **Never hand-edit the auto-stamped fragments** (Part 2, Layer A). If a governance test is failing only because the stamped version looks wrong locally, run `python scripts/stamp_release_assets.py` (not a hand edit) and re-check — a genuinely wrong *pyproject.toml* version is a release-mechanics problem, not a docs problem (see `tensor-grep-release-and-positioning`).
5. **Never add a banned marketing fragment** (Part 2, Layer B negative list) to `README.md`, `docs/benchmarks.md`, `docs/gpu_crossover.md`, or `docs/PAPER.md`, and never claim `"ast-grep parity"` anywhere.

---

## Part 5 — Runbook: adding a new doc to the governed set

Adding a brand-new file that should join the auto-stamp/pytest-governed set has its own **N-site registration** shape — the same universal bug class `AGENTS.md`/`tensor-grep-change-control` describe for commands and search flags (miss one site, it fails *quietly* — the file just never gets stamped or never gets checked, with no error).

| # | Site | What to add |
|---|---|---|
| 1 | `pyproject.toml` → `[tool.semantic_release].version_variables` | `"path/to/new_doc.md:release_docs_current_tag:tf"` — only if the doc should carry the auto-stamped tag line |
| 2 | `scripts/stamp_release_assets.py` → `RELEASE_DOC_PATHS` or `GPU_DOGFOOD_DOC_PATHS` | add the relative path so the prose-regex stamping pass covers it |
| 3 | `pyproject.toml` → `build_command`'s `git add ...` list | add the path — **stamping without `git add` here means the release commit never includes the file's stamped content** |
| 4 | The relevant test file (`tests/unit/test_public_docs_governance.py` or `test_enterprise_docs_governance.py`) | add the doc to whichever `docs = {...}` dict(s) it should be checked alongside, with its required fragments |
| 5 (site-only) | `mkdocs.yml` → `nav:` | only if the doc should be part of the published site (Part 2, Layer D) |

Before claiming this is done, re-grep all five sites for the new path — the same discipline as the command/flag registration audit in `tensor-grep-change-control`.

---

## Part 6 — Why README.md is thin now (the ledger-regrowth guard)

Until 2026-06-25, `README.md` carried a full "## Current Release State" section: per-release fix/feature/release commit hashes, CI/CodeQL run IDs, PyPI line, and a hand-maintained "What `vX` closed:" changelog ledger. It drifted every release and, when force-rewritten as pure marketing copy, broke ~14 governance tests plus a separate release-blocker gate (`agent-readiness` needing the AST probe + a stale `uv run` dev-sync issue) — 4 CI cycles were wasted theorizing from tracebacks instead of reading the structured failing-check output first (full incident: `tensor-grep-failure-archaeology`).

The resolution, encoded directly in the test file's comments (`test_public_docs_governance.py:57-67, 247-263`): **`README.md` is now a marketing/positioning doc only.** Detailed contract facts live in their dedicated docs (`AGENTS.md` / `SKILL.md` / `docs/SESSION_HANDOFF.md` / `docs/CONTRACTS.md` / `docs/CONTINUATION_PLAN.md`), and per-version history lives in `CHANGELOG.md` + GitHub Releases — never in README.md. The negative assertions (`"Latest complete public release PR"` / `"Latest complete public release commit"` must NOT appear) exist specifically so this ledger cannot silently regrow. **If you're tempted to paste a per-release fix list into README.md, put it in `CHANGELOG.md` instead** — that's exactly the mistake this guard exists to catch.

---

## Part 7 — Templates

### 7a. A new `docs/SESSION_HANDOFF.md` release-line entry

Match the exact observed pattern (`docs/SESSION_HANDOFF.md:32-60`) — one bullet per release, past tense, naming the PR and the concrete behavior:

```
- Closed vX.Y.Z <short gap name> gap: PR #NNN <does what, concretely — name the files/flags/fields
  touched, not just "fixes a bug">.
```

### 7b. A dogfood-follow-up per-slice evidence-ledger entry

Required fields, per `AGENTS.md:275` and pinned by `test_agent_workflow_docs_should_preserve_dogfood_research_pr_slice_process`: PR order; slice scope; Exa research anchors (or `"not applicable"` **with a stated rationale**); thinktank/planning consensus; subagent ownership; Gemini review result; validation commands; PR CI; main CI; for release-bearing slices additionally semantic-release, release assets, PyPI, and public release dogfood evidence. Copy the shape of an existing entry in `AGENTS.md`'s "Current post-`vX` dogfood slice ledger" rather than inventing a new field order.

### 7c. `docs/PAPER.md` — append, never rewrite

`PAPER.md` preserves failed attempts on purpose (`AGENTS.md` "Documentation Discipline": *"The paper should preserve failed attempts too, so future agents do not retry the same losing ideas."*). The observed convention is a dated blockquote appended at the point of writing, e.g. `> post-\`vX\` dogfood GPU performance note (YYYY-MM-DD): ...` — do not delete or rewrite an old dated note to "clean up"; append a new one that supersedes it and say so in the new note's text.

---

## Part 8 — House style (observed, not invented)

- **Dense, factual, hedged prose over adjectives.** State the mechanism ("routes to `NativeCpuBackend` because the GPU sidecar reported `sidecar_used = true`"), not a claim ("blazing fast"). The banned-marketing-fragment list in Part 2 is the enforced floor of this rule.
- **Exact identifiers in backticks**, and copy them verbatim from an existing doc rather than retyping — `tg agent`, `NativeGpuBackend`, `gpu_evidence_status`, `context_consistency`. A missing backtick or a respelled field name breaks nothing structurally but silently stops matching a pinned pytest substring elsewhere.
- **Date-stamp the state, not just the facts.** Governed docs open with `As of <date>, the current tagged version is \`vX\`, ...` — keep this pattern; it's what both `validate_docs_claims` and the stamping regexes match on.
- **Never claim a speedup or "improvement" without a measured number vs the accepted baseline** — this is a docs rule too, not just a code rule (`AGENTS.md` "Performance Discipline" #4: *"Do not update docs or the paper with speed claims until the benchmark line is accepted."*). See `tensor-grep-benchmark-and-proof-toolkit` for how to produce that number.
- **Historical notes are additive, not destructive** (Part 7c) — this is the opposite convention from `SESSION_HANDOFF.md`'s single "Current Release State" block, which IS meant to be replaced by the stamping script each release. Know which doc you're in before deciding whether to append or overwrite.
- **A doc is stale and known to be stale is better than silently wrong.** `docs/SESSION_HANDOFF.md`'s "Last updated: 2026-06-28" header and its prose log (last entry describing `v1.13.23`) trail the auto-stamped `release_docs_current_tag: v1.17.25` line by several release lines as of this writing — the tag line is correct (auto-stamped), the prose narrative below it is not yet refreshed. This is a real, currently-open example of Part 1's point: two parts of the same doc can be governed at different granularities (one line machine-stamped, the surrounding narrative hand-maintained) — don't assume the whole file is current just because the top line is.

---

## Part 9 — Pre-merge checklist for any docs change

- [ ] Identified which doc(s) in Part 1's table own this claim — not just the first one that came to mind.
- [ ] Grepped the exact phrase being changed/removed against `tests/unit/test_public_docs_governance.py` and `test_enterprise_docs_governance.py` **before** editing.
- [ ] New claim written into **every** doc a matching existing pytest loop requires (Part 4, step 2) — or a new loop-style assertion added if none exists yet.
- [ ] No banned marketing fragment introduced (Part 2 Layer B negative list); no `"ast-grep parity"` claim.
- [ ] No hand-edit of an auto-stamped fragment (Part 2 Layer A) — ran `python scripts/stamp_release_assets.py` instead if a stamp looked wrong.
- [ ] New governed doc → all 5 registration sites in Part 5 confirmed present.
- [ ] `README.md` touched → confirmed no per-release ledger content reintroduced (Part 6).
- [ ] `SKILL.md` touched → confirmed **which** of the three `SKILL.md` files (Part 3) was actually intended.
- [ ] `CLAUDE.md` touched → change is a short pointer bullet, not duplicated AGENTS.md prose.
- [ ] Ran `uv run pytest tests/unit/test_public_docs_governance.py tests/unit/test_enterprise_docs_governance.py tests/unit/test_stamp_release_assets.py -q` and `tests/unit/test_benchmark_scripts.py -k tensor_grep_claude_skill -q` green.
- [ ] Touched a mkdocs-nav'd file → `mkdocs build --strict` green.
- [ ] Ran `python scripts/agent_readiness.py` (the `docs-claim-check` probe) as a fast pre-push smoke test.

---

## Provenance and maintenance

Volatile facts are dated **2026-07-02, release `v1.17.25`**. Re-verify anything below before relying on it — a wrong runbook is worse than none.

| Claim | Re-verify command |
|---|---|
| Current release tag | `grep release_docs_current_tag AGENTS.md` |
| `version_variables` full list | `sed -n '/\[tool.semantic_release\]/,/^\[/p' pyproject.toml` |
| `stamp_release_assets.py` doc-path groups | `grep -n "RELEASE_DOC_PATHS\|GPU_DOGFOOD_DOC_PATHS" scripts/stamp_release_assets.py` |
| `build_command`'s `git add` list stays in sync with the doc-path groups above | `grep -n "build_command" pyproject.toml` |
| Root `SKILL.md` pytest variable name | `grep -n "SKILL_DOC_PATH" tests/unit/test_public_docs_governance.py` |
| The one test pinning `.claude/skills/tensor-grep/SKILL.md` | `grep -n "skills/tensor-grep" tests/unit/test_benchmark_scripts.py` |
| No test yet pins `.claude/skills/<topic>/SKILL.md` (this library) | `grep -rln "\.claude/skills" tests/` (expect only the file above) |
| Banned marketing fragments | `grep -n "banned_fragments" -A10 tests/unit/test_public_docs_governance.py` |
| README ledger-regrowth negative guard | `grep -n "Latest complete public release" tests/unit/test_public_docs_governance.py` |
| Enterprise doc set | `sed -n '1,20p' tests/unit/test_enterprise_docs_governance.py` |
| mkdocs strict-build CI gate | `grep -n "mkdocs build" .github/workflows/ci.yml` |
| mkdocs nav doc set | `sed -n '/^nav:/,/^markdown_extensions:/p' mkdocs.yml` |
| Fast docs-claim-check fragment list | `sed -n '/def validate_docs_claims/,/^def /p' scripts/agent_readiness.py` |
| `docs/SESSION_HANDOFF.md` prose-vs-tag-line staleness (Part 8) | `head -10 docs/SESSION_HANDOFF.md` vs the last `- Closed vX...` entry's version |

If any command above no longer matches what's in this file, update the skill in the same change.
