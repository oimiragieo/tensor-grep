---
name: tensor-grep-release-and-positioning
description: Use when merging a release-bearing PR, writing a PR title for tensor-grep, diagnosing "why didn't my release publish" or "why hasn't npm/the docs site updated", running the post-publish dogfood, or making any external-facing speed/GPU/LSP/benchmark claim about tg. Covers semantic-release mechanics in ci.yml, the push-race + one-merge-per-tick discipline (worked example: the #384-#399 sequence), the npm/docs manual-dispatch publish gap, PyPI/npm/Homebrew/winget publish gates, and the not-faster-grep positioning + reproducibility standard for comparator claims. As of 2026-07-24, v1.95.0.
---

# tensor-grep: Release Mechanics and Public Positioning

External-facing sibling of `tensor-grep-change-control` (which covers the seam-verification /
council process for landing a change). This skill picks up **after code is merge-ready**: how a
merge turns into a published release, and what you are and are not allowed to say about `tg`
publicly once it ships.

## When NOT to use this skill

| Situation | Use instead |
|---|---|
| You're still designing/verifying a code change before merge | `tensor-grep-change-control` |
| A CI job is red and you need to root-cause it | `tensor-grep-debugging-playbook` / `tensor-grep-failure-archaeology` |
| You need to run or interpret a benchmark suite in detail | `tensor-grep-benchmark-and-proof-toolkit` |
| You're writing docs prose, not making a release/positioning decision | `tensor-grep-docs-and-writing` |
| You're deciding CLI flags/config, not release/claims | `tensor-grep-config-and-flags` |

---

## Part 1 — Release mechanics

### 1.1 The actors (do not confuse these two workflows)

- **`ci.yml`** is the *only* path that actually publishes. It runs on every push to `main`, every
  PR, and weekly (`.github/workflows/ci.yml:3-9`). The `release` job (display name **`Semantic
  Release`**, `ci.yml:941-961`) runs `python-semantic-release` and is gated on `github.ref ==
  'refs/heads/main' && github.event_name == 'push' && !contains(commit message, 'skip release')`
  (`ci.yml:944`).
- **`release.yml`** is `workflow_dispatch`-only, targeting an *already-published* tag
  (`gh workflow run release.yml --ref vX.Y.Z`). It is a manual/backfill artifact pipeline, **not**
  triggered by a tag push — a manually-pushed `v*` tag cannot bypass semantic-release
  (`docs/CI_PIPELINE.md:49`). If you're trying to "just push a tag" to force a release, stop: that
  path was deliberately closed.

### 1.2 The gate DAG on `main` (know this before saying "it's not published")

```
release-intent (PR-only title check)
        │
repo-hygiene → smoke → {release-readiness, agent-readiness, windows-agent-readiness,
                         package-manager-readiness, static-analysis, test-python,
                         test-rust-core, cuda-feature-check, search-golden-parity,
                         native-build-smoke, test-gpu-linux, benchmark-regression}
        │  (ALL of the above must succeed — ci.yml:943)
        ▼
   release  ("Semantic Release" job — creates tag + chore(release) commit)
        │
        ├─ build-wheels-pypi / build-sdist-pypi  (only if publish_pypi == 'true')
        ├─ build-release-native-assets            (only if released == 'true')
        │        │
        │        ▼
        │  publish-github-release-assets  (needs: release, build-release-native-assets)
        │        │
        ▼        ▼
   validate-pypi-artifacts → publish-pypi (needs release + both build jobs + publish-github-release-assets)
        │
        ▼
   publish-success-gate  (needs: release, publish-pypi, publish-github-release-assets)
        │
        ▼
   release-tag-smoke
```
(`ci.yml:941` release · `1015` build-wheels-pypi · `1060` build-sdist-pypi · `1102`
validate-pypi-artifacts · `1130` build-release-native-assets · `1228` publish-github-release-assets ·
`1303` publish-pypi · `1340` publish-success-gate · `1420` release-tag-smoke — re-grepped 2026-07-24
against `ci.yml`'s current 1454 lines (byte-identical at the `v1.95.0` tag this skill is pinned to);
every line number in this block shifted +11 from the 2026-07-22/v1.93.2 pass because an 11-line rustup
pinned-toolchain pre-fetch retry loop (#720-#722) was inserted into `test-rust-core`'s setup step
between that pass and this one — job names, `needs:` edges, and DAG shape are otherwise unchanged)

A release is **not** done just because `release` (Semantic Release) went green. It is done when
`publish-success-gate` is green — that job RE-VERIFIES (not merely re-checks job results) GitHub
release asset coverage (`ci.yml:1397`, `scripts/verify_github_release_assets.py`) and PyPI parity
(`ci.yml:1418`, `scripts/validate_release_version_parity.py`) for the exact tag semantic-release
produced — both scripts also run once earlier, inside `publish-github-release-assets`/`publish-pypi`
themselves (`ci.yml:1295`/`1332`), so `publish-success-gate` is a second, independent confirmation
pass, not the only place these checks run.

### 1.3 PR title → release intent

`scripts/validate_pr_title_semver.py:10-26` is the ground truth (enforced by the `release-intent`
PR job, `ci.yml:20-30`). The regex accepts an optional `(scope)` and an optional `!`:

| Prefix | Intent | Note |
|---|---|---|
| `feat:` | minor | |
| `fix:` / `perf:` | patch | |
| `refactor:` | patch | **Not listed in `AGENTS.md`'s prose table, but the validator script treats it as patch** — trust the script over the prose if they ever disagree. |
| `feat!:` / `fix!:` (any type + `!`) | major | |
| `docs:` / `test:` / `build:` / `ci:` / `chore:` / `bench:` | none | no release; `bench:` is likewise absent from `AGENTS.md`'s prose table (same gap as `refactor:` above) — the script is ground truth. |

Anything that doesn't match the pattern fails PR CI outright (`validate_pr_title_semver.py:86-94`).
Squash-merge release-bearing PRs — the PR title becomes the `main` commit subject that
semantic-release reads (`AGENTS.md:891`, `docs/RELEASE_CHECKLIST.md:54`).

### 1.4 What `chore(release)` actually touches

`pyproject.toml:132-154` (`[tool.semantic_release]`) is the contract:

- `commit_message = "chore(release): v{version} [skip ci]"`
- `version_toml` bumps `pyproject.toml:project.version` and `rust_core/Cargo.toml:package.version`
- `version_variables` stamps: `src/tensor_grep/cli/main.py:pkg_version`, `npm/package.json:version`,
  `scripts/tensor-grep.rb:TENSOR_GREP_VERSION`, `scripts/oimiragieo.tensor-grep.yaml`
  (`PackageVersion` + `InstallerUrl`), and a `release_docs_current_tag` token in `AGENTS.md`,
  `README.md`, `SKILL.md`, `docs/SESSION_HANDOFF.md`, `docs/CONTINUATION_PLAN.md`,
  `docs/CONTRACTS.md`.
- `build_command` runs `scripts/stamp_release_assets.py`, stages the touched docs, bootstraps a
  pinned `rustup` toolchain (`1.96.0`), regenerates `Cargo.lock` and `uv.lock` (`uv lock
  --upgrade-package tensor-grep`), and runs `uv build` — all *inside* the semantic-release job,
  which is why that job takes ~6 minutes (see push-race below).

**Practical consequence**: after any release, your local `main` is stale until you fetch it. `git
status` on a pre-release commit will show a version that PyPI has already moved past.

```bash
git fetch origin main --tags
git pull --ff-only origin main
```

Do this **before** any post-release check — see the memory note
`feedback-switch-to-main-after-merge`: a "dogfood FAIL" that was actually a stale local checkout on
a merged branch, not a real regression.

### 1.5 The push-race — the #1 reason a release "silently doesn't publish"

The `Semantic Release` job **builds native assets before it publishes**, so its `git push origin
main` (the `chore(release)` commit) doesn't land until ~6 minutes after the merge that triggered it.
That whole window is a race window (`AGENTS.md:838`).

If **any** other merge lands on `main` during that window — including a no-release `docs:`/`chore:`
PR — the in-flight release's final push is rejected non-fast-forward (`! [rejected]  main -> main`)
and **that version never publishes**. The CI `concurrency` group (`ci.yml:11-17`) serializes *runs*,
not the human/agent act of clicking merge, so it does not prevent this.

- **Receipt**: `v1.17.23` (security batch, #318) failed to publish because a GPU-pause `docs:` PR
  (#319) was merged while #318's release job was still compiling assets (`AGENTS.md:840`).
- **Recovery — do NOT panic-rerun.** The failure self-heals: the *next* push-to-`main` re-runs
  `Semantic Release`, and because the version is derived from git tags (not the failed run's
  in-memory state), it recomputes the correct next version and folds in the orphaned commit. The
  code was already on `main` regardless — only the publish step was behind.
- **Diagnose by decoding the structured job result first**: `gh run view <id> --json jobs` → find
  `Semantic Release` → `--log-failed`. A `! [rejected]  main -> main` line is the push-race
  signature; anything else is a different bug. Do not theorize from a traceback before reading this.

**Discipline — one-merge-per-tick**: merge ONE release-bearing (or potentially-racing) PR, then wait
for its `chore(release): vX` commit to appear on `main` **and** for PyPI to show the new version,
before merging the next one. "Safe to interleave" means *after the prior release has fully
published*, not merely after its PR CI went green (`AGENTS.md:834`).

### 1.5.1 Worked example: the #384-#399 sequence (2026-07-04/05) — 16 PRs, 0 push-race failures

This is not a hypothetical — it is how the "moat P0-6" `--deadline` program plus the round-8
security batch actually shipped, and it is worth re-reading as a receipt that the discipline in 1.5
scales past a single pair of PRs. Sixteen release-bearing PRs (`#384`-`#399`) landed one at a time,
each waiting for the prior `chore(release)` commit to publish before the next merge, with a
one-to-one PR-to-release mapping and **zero** push-race rejections across the whole run:

```
#385 -> v1.30.4   #386 -> v1.30.5   #384 -> v1.31.0   #387 -> v1.32.0   #388 -> v1.33.0
#389 -> v1.34.0   #390 -> v1.35.0   #391 -> v1.35.1   #392 -> v1.36.0   #393 -> v1.37.0
#394 -> v1.38.0   #395 -> v1.39.0   #396 -> v1.39.1   #397 -> v1.40.0   #398 -> v1.40.1
#399 -> v1.40.2
```

(PR numbers are assigned at creation, not merge order — `#384` merged *after* `#385`/`#386`, which
is normal and does not break the one-tick-at-a-time property.) Re-derive this yourself rather than
trusting the table:

```bash
git log --oneline --all | grep -E "chore\(release\)|#3(8[4-9]|9[0-9])\)"
```

Reading that output top-to-bottom (newest-first), every `chore(release): vX` commit sits directly
between two PR-merge commits with no interleaving — the live evidence for "wait for full publish,
not just green CI."

Zooming out further, the surrounding release cadence was not gentle: roughly **40 `chore(release)`
commits landed in a 48-hour trailing window** (`v1.22.0` through `v1.40.2`, verified 2026-07-05):

```bash
git log --format="%ci %s" --all | grep -E "chore\(release\)" | \
  awk -v cutoff="$(date -u -d '48 hours ago' '+%Y-%m-%d %H:%M:%S')" '{ts=$1" "$2; if (ts >= cutoff) c++} END{print c}'
```

At that cadence, one-merge-per-tick is the only thing standing between "39-40 releases published
clean" and "half of them silently dropped to a push-race rejection" — see 1.6.1 below for what this
cadence means for the surfaces that *aren't* on the automatic pipeline (npm, docs).

### 1.6 Publish surfaces and their gates

| Surface | Package/formula identity | Gate/verify mechanism |
|---|---|---|
| PyPI | `tensor-grep` (`pyproject.toml`) | OIDC-based publish, only if `publish_pypi=true` (version not already on PyPI — `ci.yml:965-1013`); `publish-success-gate` re-checks parity (`ci.yml:1392-1418`) |
| npm | `tensor-grep` / bin `tg` (`npm/package.json:2,5-8`) | version stamped by semantic-release `version_variables` |
| Homebrew | `scripts/tensor-grep.rb` (`class TensorGrep`, `TENSOR_GREP_VERSION`) | `ruby -c scripts/tensor-grep.rb` in CI; formula URL must point at the tag's GitHub release asset (`docs/RELEASE_CHECKLIST.md:130-132`) |
| winget | `PackageIdentifier: oimiragieo.tensor-grep` (`scripts/oimiragieo.tensor-grep.yaml:5`) | `winget validate` on Windows, Python validator fallback; `InstallerSha256` stamped from `CHECKSUMS.txt` |
| GitHub release assets | `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu` (built on Intel `macos-15-intel`, not `macos-latest`), `tg-windows-amd64-cpu.exe`, `CHECKSUMS.txt`, `BUNDLE_CHECKSUMS.txt`, package-manager bundle | `publish-github-release-assets` + `scripts/verify_github_release_assets.py --expected-profile native-frontdoor` (`docs/CI_PIPELINE.md:181-186`) |

The default asset profile is CPU-only `native-frontdoor`. An opt-in repo variable
`TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE=native-frontdoor-gpu` additionally builds
`tg-linux-amd64-nvidia` / `tg-windows-amd64-nvidia.exe` (`docs/CI_PIPELINE.md:25`, `ci.yml:1138`).
macOS stays CPU-only either way.

### 1.6.1 The npm/docs publish gap — semantic-release stamps the version, it does not publish either

Read the npm row of the table above carefully: the gate/verify mechanism listed is "version stamped
by semantic-release `version_variables`" — that is a **file edit**, not a publish. `ci.yml` (the
*only* automatic path, per 1.1) contains **zero** `npm publish` or `mkdocs gh-deploy` steps. Verify
this yourself rather than trusting this sentence:

```bash
grep -c "npm" .github/workflows/ci.yml          # -> 0 (2026-07-24: still confirmed 0 hits, no npm job exists)
grep -n "gh-deploy" .github/workflows/*.yml     # -> only .github/workflows/release.yml:379
```

`ci.yml`'s only docs-related step is a **validation** build in the `release-readiness` job
(`ci.yml:81-82,96-99`: `mkdocs build --strict`) — it confirms the docs site still *builds*, it never
runs `mkdocs gh-deploy` to publish it.

The actual publish steps — `npm publish --access public` (`release.yml:338-340`, job `publish-npm`
at `release.yml:314-357`) and `mkdocs gh-deploy --force` (`release.yml:378-379`, job `publish-docs`
at `release.yml:359-379`) — live *only* in `release.yml`, and that workflow is `workflow_dispatch`-only
(`release.yml:8-9`, no `push`/`tag` trigger — see 1.1). Nothing in the automatic `main`-push pipeline
ever invokes it.

**Consequence at the current release cadence** (~40 releases in a 48-hour window, 1.5.1 above):
PyPI moves on every single `chore(release)` commit; npm and the public docs site only move when a
human explicitly runs `gh workflow run release.yml --ref vX.Y.Z` and lets `publish-npm` /
`publish-docs` complete. Nothing forces that dispatch to happen, and nothing alerts on its absence.

**Verified live 2026-07-05** — this is not a theoretical gap:

```bash
python - << 'PY'
import json, urllib.request
print("PyPI:", json.load(urllib.request.urlopen("https://pypi.org/pypi/tensor-grep/json"))["info"]["version"])
try:
    print("npm registry:", json.load(urllib.request.urlopen("https://registry.npmjs.org/tensor-grep"))["dist-tags"]["latest"])
except Exception as e:
    print("npm registry:", e)
PY
```

Result: PyPI reports `1.40.2`. The npm registry returns **HTTP 404** for `tensor-grep`
(`https://registry.npmjs.org/-/v1/search?text=tensor-grep` also returns zero matching results) —
the package has never been published under this name on the public npm registry, even though
`npm/package.json:version` has been correctly stamped to `1.40.2` by every `chore(release)` commit
in the chain. **A stamped version file is not evidence of a published package** — check the
registry (or the docs site's live URL), not the repo, before claiming npm/docs parity with PyPI.

**Before claiming "released" on npm or docs**:

```bash
gh workflow run release.yml --ref v<X.Y.Z>
gh run watch                                                # wait for publish-npm + publish-docs + release-success-gate
python -c "import json,urllib.request as u; print(json.load(u.urlopen('https://registry.npmjs.org/tensor-grep'))['dist-tags']['latest'])"
```

If nobody has ever dispatched `release.yml` for a given tag, the gap isn't a code bug — it's a
missing manual step. The durable fix (not yet built, flag it rather than silently working around
it) is folding `publish-npm`/`publish-docs` into the `ci.yml` gate chain itself so npm/docs parity
doesn't depend on a human remembering a separate dispatch.

### 1.7 Post-publish: dogfood the real artifact, not a mock

Never declare a release "done" from PyPI visibility alone if the installer/update path changed
(`docs/RELEASE_CHECKLIST.md:99-108`). Confirm with API evidence:

```bash
python - << 'PY'
import json, urllib.request
data = json.load(urllib.request.urlopen("https://pypi.org/pypi/tensor-grep/json"))
print(data["info"]["version"])
PY
```

Then run the Docker dogfood harness (`scripts/dogfood/README.md`) — it installs the *published*
package into a clean container and drives the *real* `tg` binary (not `CliRunner`, which bypasses
the `tensor_grep.cli.bootstrap:main_entry` front door and would have missed the v1.14.0 `tg search
--rank` regression that shipped broken):

```bash
docker build --build-arg TG_VERSION=<X.Y.Z> -f scripts/dogfood/Dockerfile -t tg-dogfood scripts/dogfood
docker run --rm tg-dogfood      # exit 0 = every feature works; exit 1 = prints the failing tg <command>
```

Without Docker:

```bash
pip install "tensor-grep==<X.Y.Z>"
python scripts/dogfood/dogfood_features.py   # or TG_BIN=/path/to/tg python scripts/dogfood/dogfood_features.py
```

Compact release checklist from `AGENTS.md:625-635` (run all of these, not a subset):

```bash
gh release view <tag>
pip index versions tensor-grep
uvx --refresh-package tensor-grep --from tensor-grep==<tag> tg --version
tg upgrade
cmd /c tg --version
pwsh -NoProfile -Command "tg --version"
tg doctor --json
```

`tg doctor --json` must show matching sidecar/native versions and surface any foreign-launcher
route (current-shell / fresh-shell / Python-subprocess) — a mismatched version here is a real
release-parity bug, not noise.

### 1.8 Rollback, if a bad release escapes

Full runbook: `docs/RELEASE_CHECKLIST.md:153-170`. Summary:

1. Do not rely on deleting PyPI artifacts as the primary fix — publish a corrected **patch**
   release instead.
2. If GitHub release assets are wrong, rebuild for the *same* tag and rerun
   `scripts/verify_github_release_assets.py`.
3. Homebrew/winget: revert/submit a manifest pointing at the previous known-good version.
4. Always add root cause + mitigation to `CHANGELOG.md` (and `docs/PAPER.md` if
   architecture-impacting), and add/adjust a CI assertion so the specific failure class can't recur.

### 1.9 Release-mechanics checklist (copy-paste before you claim "released")

```
[ ] PR title matches the validator regex (scripts/validate_pr_title_semver.py:10)
[ ] Squash-merged (not merge-commit) so the title becomes the main commit subject
[ ] No other release-bearing PR is mid-flight (one-merge-per-tick — 1.5 above)
[ ] `Semantic Release` job green on the exact commit
[ ] `git fetch origin main --tags && git pull --ff-only origin main`
[ ] `publish-github-release-assets` green for the new tag
[ ] `publish-pypi` green (if publish_pypi was true) AND PyPI JSON API shows the version
[ ] `publish-success-gate` green
[ ] `release-tag-smoke`'s OWN conclusion checked inside the release run (`gh run view <id> --json
    jobs` -> find the `release-tag-smoke` job -> read ITS `conclusion`) -- do not infer this from
    "latest main run green." It is a NEEDS-gated job (`needs: [release, publish-success-gate]`) that
    checks out the release tag and re-runs `scripts/agent_readiness.py` against it; PyPI can keep
    publishing fine for releases at a time while this job stays red underneath, masking a real
    regression (#542 masked for 4 releases since v1.64.4 this way -- see
    `tensor-grep-failure-archaeology` and `tensor-grep-debugging-playbook` for the incident).
[ ] Docker dogfood (1.7) run against the published version, exit 0
[ ] If installer/update path changed: `tg upgrade` + fresh-shell `tg --version` verified
```

---

## Part 2 — Public positioning

### 2.1 The thesis: not faster grep

`tensor-grep` is **not** a universal speed winner over raw text search. Say this plainly in any
public-facing material. The honest comparator map (`docs/tool_comparison.md:1-13`):

| Tool | Role |
|---|---|
| `ripgrep` (`rg`) | cold generic text-search **baseline** — `tg` does not claim to beat it on cold search |
| `ast-grep` | structural search/rewrite **baseline** |
| `Semgrep` | policy/security-scanning baseline (stronger ecosystem than `tg` today) |
| `Zoekt` | indexed search-at-scale baseline (`tg` has no accepted head-to-head yet) |
| `tensor-grep --cpu` | native CPU probe for large-file / count-heavy workloads |

The moat is the **agent-native code-intelligence layer** on top of search — `tg orient` / `callers`
/ `blast-radius` / `defs` / `refs` / `agent` (the Actionable Context Capsule) / `session` — not raw
grep throughput. `docs/tool_comparison.md:62-77` states this explicitly: `rg` still owns cold
generic text search on the current release line; `tg` wins repeated/warm-query search, AST
search+rewrite, and agent-workflow surfaces (machine-readable CLI/NDJSON/session/MCP).

Where `tg` currently loses or ties (state this, don't bury it — `docs/tool_comparison.md:21-24,73-77`):

- Cold generic text search on the current Windows host: `rg` wins the standard-corpus row; `tg` is
  roughly tied on a 200MB large-file row.
- `Semgrep` remains the stronger security/policy scanning ecosystem.
- `Zoekt` remains the external indexed-at-scale baseline; `tg` has local repeated-query wins, not an
  accepted direct bakeoff.
- `ag`, `ack`, `ugrep`, GNU `grep` are **not yet** part of the accepted comparator pack — don't make
  hard claims about them (`docs/tool_comparison.md:87`).

### 2.2 The reproducibility standard — required before ANY benchmark / GPU / LSP claim

This is the actual gate, not aspiration. `AGENTS.md:641`: **"Never claim a speedup without measured
numbers."** Concretely:

1. **Emit a machine-readable artifact.** Benchmark suites write `artifacts/bench_*.json`
   (`docs/PAPER.md:340`). A verbal "it feels faster" is not evidence.
2. **Run it through the regression checker against the accepted baseline**, not an ad hoc rerun:
   ```bash
   python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
   python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
   ```
   `benchmarks/check_regression.py` rejects cross-OS comparisons by default unless explicitly
   overridden, and compares against `benchmark_host_key`/`host_provenance`-tagged baselines
   (`docs/PAPER.md:344,347`) — a Linux CI number is not evidence for a Windows claim, and vice
   versa.
3. **Pick the benchmark that matches what changed** — end-to-end CLI text search
   (`run_benchmarks.py`) for routing/startup/control-plane changes, hot-query
   (`run_hot_query_benchmarks.py`) for repeated-query/cache paths, AST (`run_ast_benchmarks.py`,
   `run_ast_workflow_benchmarks.py`) for structural workflows, GPU (`run_gpu_benchmarks.py`,
   `run_gpu_native_benchmarks.py`) for GPU paths (`AGENTS.md:645-717`). Using the wrong suite is not
   evidence for a different code path.
4. **Reject the change if it regresses**, even if the code is otherwise clean (`AGENTS.md:387,738`).
   Main CI enforces this with a required same-runner base-vs-head benchmark-regression gate
   (`docs/CI_PIPELINE.md:23,42-45`) that blocks merge *before* semantic-release ever runs.
5. **For GPU specifically**: correctness before speed, always. GPU scale gates need 1GB and 5GB rows
   with exact match/file-set correctness for every corpus, no-match must be a valid comparator
   outcome (`rg` exit 1 + empty output vs `tg` no-match), and explicit `--gpu-device-ids` must not
   silently touch unselected devices (`AGENTS.md:367`). Public managed-GPU promotion additionally
   requires `NativeGpuBackend` with `sidecar_used = false`, a direct `rg --json` correctness/timing
   comparison, and the advanced many-fixed-string proof gate versus a fair single-invocation `rg -F
   -e ... -e ...` baseline (`docs/CI_PIPELINE.md:85-91`, the `public-gpu-proof.yml` workflow). Local
   CUDA-feature binaries, sidecar rows, CPU-fallback rows, and single-pattern speed wins are
   *implementation evidence*, not public promotion proof — do not market them as one.
6. **For LSP specifically**: default `--provider native` must never start an LSP provider; a claim
   requiring `lsp_provider_response` / `lsp_proof` / `lsp_operation` / `lsp_resolution_basis` is only
   valid when an opted-in provider request actually confirmed it (`docs/CONTRACTS.md:111`). Mixed
   evidence downgrades confidence and must be surfaced explicitly, not hidden behind one ranked
   result (`docs/CONTRACTS.md:109`).
7. **Preserve failed attempts, not just wins.** `docs/PAPER.md`'s optimization ledger
   (section 3.10) exists so future agents don't re-attempt the same losing idea; update it whenever
   a benchmark candidate is accepted *or* rejected (`AGENTS.md:911-916`).
8. **Internally-verified is not the same gate as publishable.** A worked example (2026-07-16, `tg
   find` campaign #189): the golden-set gate-run showing `rrf` beating `bm25` by **+0.195 ndcg@10 /
   +0.30 recall@10** on the NL golden set is bidirectional-oracle-validated and internally accepted
   — but citing it EXTERNALLY (a blog post, a README claim, a public comparator table) is a
   **separate, CEO-gated decision (#72)** that also covers the earlier P1/P4 tokens-per-correct
   numbers (`tensor-grep-benchmark-and-proof-toolkit` §6). Do not conflate "this artifact clears the
   internal acceptance bar" with "this number is cleared for public release" — the latter needs an
   explicit go from the CEO desk, independent of measurement quality.

### 2.3 Current experimental / open surfaces (label them as such, don't oversell)

| Surface | Status (2026-07-24, v1.95.0 unless noted) | Source |
|---|---|---|
| GPU native backend | **Status refreshed to v1.75.4 (Phase-0 ship), with a 2026-07-21 re-adjudication (B-GPU) since — already live at `v1.95.0`.** Phase-0 SHIPPED (v1.75.0-v1.75.4, PRs #593-#597): NVIDIA native assets built and locally correctness-proven (RTX 4070 `sm_89` / RTX 5070 `sm_120`, 1GB/5GB correctness), gated OFF the public release by the CI Actions var `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; GPU asset publishing needs the non-default `native-frontdoor-gpu`) -- Phase 1 (the flag-flip) remains a reversible, **CEO-held**, not-yet-authorized decision, not a multi-week rebuild. The 2026-07-21 re-adjudication re-tested 10MB-5GB corpora and still found no crossover at any scale (historical worst ~30-35x slower at 5GB; even the best-case 100-pattern fixed-string lane loses to a fair-baseline `rg -F -e ...`), and corrected the shipped `gpu_text_search_positions` kernel's description to a **position-parallel brute-force byte-compare**, not a PFAC/Aho-Corasick automaton (PFAC remains documented future work, never shipped). GPU auto-recommendation stays `false`, and the reviewer-gated `public-gpu-proof.yml` speed-crossover gate remains unmet -- public CUDA-asset publishing is on a deliberate **HOLD** (CEO decision, #169). `docs/BACKLOG.md`'s CEO desk continues to frame the forward direction as CPU semantic search (`tg find`, #189) with GPU held under #169 (earlier entries used the phrase "GPU retired-for-search (#169)") -- read this as a *resourcing* signal (where engineering capacity goes next), not a technical claim that GPU search is proven impossible; the crossover question itself is still open (see `tensor-grep-research-frontier` Problem 1). | `docs/gpu_crossover.md:133-138`, `docs/CONTRACTS.md:80-82`, `AGENTS.md:489-496`, `docs/BACKLOG.md` CEO desk |
| GPU speed claim generally | Not accepted. GPU still loses or times out on 100MB/1GB/5GB public scale checks as of the last dogfood; kept experimental/opt-in until correctness+speed beat both `rg` and `tg --cpu` on accepted artifacts. | `docs/PAPER.md:139` |
| CyBERT / provider-backed `classify` | Opt-in only (`TENSOR_GREP_CLASSIFY_PROVIDER=cybert`), default is local deterministic; useful future reference, not a default performance claim. | `docs/PAPER.md:141-146` |
| Resident AST worker (`tg worker`) | Opt-in (`TG_RESIDENT_AST=1`), hidden from `--help`, workload-dependent — helps startup-dominated repeated micro-workflows, not the default performance path. | `docs/EXPERIMENTAL.md:5-14` |
| LSP semantic provider | Opt-in via `--provider lsp|hybrid`; default `native` never starts it. | `docs/CONTRACTS.md:111` |
| Ranking scorer (`search --rank`, agent capsule, semantic surfaces) | Flat, no-IDF scorer — can silently flip/degrade on corpus change; a degrade-to-ask safety floor exists, the scorer itself is unresolved debt (tracked as capsule-hardening Task #4, ledger B3). Don't market ranking quality without re-checking this. | memory: `tensor-grep-idf-ranking-fragility-2026-06-29`; corroborated live at `AGENTS.md:379` |

### 2.4 Positioning checklist (before any public claim)

```
[ ] Is this claim comparator-specific? ("beats rg on cold search" is different from
    "beats rg on repeated warm queries" — say which.)
[ ] Do I have an artifacts/bench_*.json for it, generated on the same host class as the claim?
[ ] Did check_regression.py pass against the accepted baseline (not just "looked faster locally")?
[ ] If GPU: does it have 1GB+5GB correctness rows AND a fair single/many-pattern rg baseline?
[ ] If LSP: is this native-provider-off by default, and is the LSP claim backed by an actual
    opted-in provider response, not an assumed one?
[ ] Am I calling GPU/LSP/semantic/cybert "experimental" or "opt-in" rather than implying default
    behavior?
[ ] Would `docs/tool_comparison.md`'s "Comparator Policy" (reproducible + checked-in artifact,
    or don't publish the claim) pass a skeptical read?
```

---

## Provenance and maintenance

Volatile facts here will drift. Re-verify before trusting this skill on a stale clone. **2026-07-24,
release `v1.95.0`**: re-verified every citation against the `v1.95.0` tag specifically (not a later
`origin/main` HEAD, which has already moved past it — see below), so the numbers stay re-derivable by
anyone who checks out that exact tag. `ci.yml`'s release-gate DAG (job names, `needs:` edges, shape)
is unchanged since the 2026-07-22/v1.93.2 pass, but every `ci.yml:N` citation in this skill still
shifted +11 lines: an 11-line rustup pinned-toolchain pre-fetch retry loop (#720-#722) landed inside
`test-rust-core`'s setup step between v1.93.2 and v1.94.0. `AGENTS.md` restructured far more unevenly
(it already sits well past `v1.95.0` on `origin/main` — a `docs(skills)` fold-in added two new
top-level sections after this pin), so every `AGENTS.md:N` citation here was re-found by content
match against the `v1.95.0` blob specifically, not derived from a line offset. `docs/PAPER.md`,
`docs/CI_PIPELINE.md`, `docs/RELEASE_CHECKLIST.md`, `docs/tool_comparison.md`, `docs/EXPERIMENTAL.md`,
`release.yml`, and `scripts/validate_pr_title_semver.py`'s structure are byte-identical at the
`v1.95.0` tag and today's tip — no citation drift there, except one real content gap:
`validate_pr_title_semver.py` gained a `bench:` conventional-commit prefix (mapped to `none`) that
section 1.3's table was missing. Section 2.3's GPU row also picked up one substantive update:
`AGENTS.md`'s own 2026-07-21 re-adjudication (already live at `v1.95.0`) corrects the shipped kernel
to a brute-force byte-compare, not PFAC, and reconfirms the HOLD is still the CEO's call (#169). No
release mechanics or other positioning verdict changed — this pass was citations, one missing table
row, and that one GPU-row refresh.

```bash
# Current version + release doc tag
python - << 'PY'
import tomllib
print(tomllib.loads(open("pyproject.toml","rb").read())["project"]["version"])
PY
grep -n "release_docs_current_tag" AGENTS.md | head -1

# PR-title -> release-intent mapping (ground truth over any prose table, including this skill's)
sed -n '10,26p' scripts/validate_pr_title_semver.py

# The gate DAG (job names + needs:) — re-grep after any ci.yml edit
grep -n "^jobs:\|^  [a-zA-Z][a-zA-Z0-9_-]*:$\|needs:" .github/workflows/ci.yml

# Comparator snapshot (numbers move every benchmark refresh)
sed -n '1,90p' docs/tool_comparison.md

# GPU / roadmap status
grep -n "RELEASE_NATIVE_ASSET_PROFILE\|native-frontdoor-gpu" .github/workflows/ci.yml
sed -n '1,20p' docs/gpu_crossover.md
```

If `scripts/validate_pr_title_semver.py`'s regex or `_RELEASE_INTENTS` dict changes, update
section 1.3 here. If `ci.yml`'s `release` job `needs:` list changes, update the DAG in section 1.2.
GPU Phase-0 (correctness-proven native assets, gated off the public release by
`TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE`) shipped v1.75.0-v1.75.4 -- if the CI var default flips to
publish GPU assets, or a speed crossover is actually proven (`public-gpu-proof.yml` passes), update
section 2.3 again and drop the "no crossover proven" framing.
