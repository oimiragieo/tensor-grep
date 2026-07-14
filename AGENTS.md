# AGENTS.md

This file explains how agents should work in `tensor-grep`.

## Goal

`tensor-grep` is trying to become a fast, scalable search tool that combines:

- `ripgrep`-class text search
- AST / structural search
- indexed repeated-query acceleration
- optional GPU / ML paths
- AI-harness-friendly search and edit behavior

The repo should be treated as a benchmark-governed, contract-heavy codebase. Do not optimize by guesswork.

## Backlog & working process

The canonical prioritized work list lives in **[docs/BACKLOG.md](docs/BACKLOG.md)** — read it for what to
work next and how. It is kept in sync with the CLI task store; GitHub (`gh pr list`) is the source of truth
for PRs. **Subagents:** treat each backlog item's description + files + status as your brief. **CEO status** =
summarize its SHIPPING + P0/P1 sections.

The standing multi-model pipeline for any substantive item: deep-dive → **Fable audit** (find + fix-idea,
cite `file:line`) → **Exa** recency + competitive research (you are trained on stale data — verify current
facts) → plan (superpowers skills) → thinktank/Fable review the plan → **Sonnet build, TDD** → verify in the
REAL venv (`uv run --no-sync`; a worktree "tests pass" is a hypothesis, re-run in the main venv) →
`ruff check` + `ruff format --preview` + `mypy` → codex/Fable review the PR → **PR → drain**
(one-merge-per-publish, the push-race rule) → repeat until no issues. Isolate code agents with
`isolation:'worktree'`. Match model to task (haiku scan / sonnet build / opus+fable review). Run the
common-sense gate before pending any question to the CEO. Keep docs (this file, `docs/BACKLOG.md`,
`docs/SESSION_HANDOFF.md`, skills, CLAUDE.md) synchronized as work lands.

## Campaign Orchestration Disciplines (2026-07-08, hard-won)

Running a multi-PR drain+build campaign so fixes *land* instead of piling up. Each rule is a fix for a
concrete failure observed this session.

- **A1 — WIP cap.** No new *build* dispatch while >5 PRs are undrained OR the `main` gate is red. A red
  gate is a drop-everything hotfix that jumps the queue. Prevents "churning not completing" — generating
  faster than the ~40–66 min/publish drain empties (backlog stays constant-size = the smell).
- **A2 — A self-firing drain-cron beats a long-lived background drain.** A short-lived per-fire cron that
  merges ONE lowest-CLEAN PR (`gh pr merge --squash --delete-branch`, push-race-checked) is robust; a
  long-lived `drain.sh &` background process kept *dying* during the long CI/publish waits (and an inner
  `&` in a `run_in_background` wrapper orphaned it). Each fire is short-lived, so nothing can be killed
  mid-run. Push-race gate per fire: the latest `chore(release)` tag must be on PyPI AND `main` CI
  `completed` before merging.
- **A3 — Mandatory adversarial security gate before merge.** Every security PR — touching `apply_policy`
  / `mcp_server` / `*_backend` / an index-or-session lock / auth / money / migration — gets an Opus "try
  to BREAK it, cite `file:line`, default FIX-FIRST if uncertain" review *before* merge. Not a rubber
  stamp: this session it returned SHIP on some and caught real issues on others (a symlink RCE bypass; a
  lock-release TOCTOU). `codex` is the nominal second vendor but its WSL path is unreliable → Opus is the
  reliable substitute. Verdict shape: `SHIP` | `FIX-FIRST(+file:line + repro + minimal fix)`.
- **A4 — Resume a dead agent from its transcript.** A background subagent that dies with "terminated
  early due to an API error: 500" is REVIVED by `SendMessage` to its `agentId` (partial work intact) — do
  NOT re-dispatch fresh (loses the work). Happened 3× this session; all recovered.
- **A5 — Don't kill a build on staleness.** A complex build (a redesign + heavy test rewiring) legitimately
  runs >10–15 min between output flushes. A "stale > N min" heuristic kill destroys a *working* agent (a
  build was killed twice before its kill-note proved it was mid-work). Trust the completion notification;
  diagnose a suspected hang from the kill-note's last line, not an mtime guess.
- **A6 — Anti-hang test protocol.** Wrap every test run in a shell `timeout` (`timeout 120 uv run
  --no-sync … pytest … --timeout=15`), and write the fix *before* the red-phase adversarial test — a
  ReDoS/deadlock red-test executed against un-fixed code IS the hang it is testing. Distinguish
  slow-but-protected from hung by exit code (124 timeout / 137 SIGKILL), not elapsed time.
- **A7 — Harvest a worktree agent's work, then re-verify.** A worktree agent's "tests pass" is a
  hypothesis (its venv may lack the compiled `rust_core` ext). Cherry-pick its commit onto a fresh branch
  off `origin/main`, re-verify in the real venv + `ruff`/`format --preview`/`mypy` + a live smoke, THEN the
  gate, THEN PR.
- **A8 — Fable is reachable only via `Agent(model:fable)`.** A Workflow `agent()` call cannot reach Fable —
  it silently falls back to the session model. Dispatch Fable design/audit seats as `Agent` subagents,
  never inside a `Workflow`.

## Current Handoff

release_docs_current_tag: v1.75.2

As of 2026-06-26, the current tagged release state is `v1.75.2`, and the latest complete public PyPI/release-asset distribution is also `v1.75.2`. The stable installer, release-native asset publication, managed-native `tg upgrade` refresh path, stale tensor-grep-owned `tg.com` bridge refresh after upgrade, native-front-door CLI parity fixes, Windows `.cmd` quoted-pattern launcher fix, native-first Windows PATH ordering, top-level validation-command contract, local default `classify`, classify provider provenance, fixed multi-pattern native CPU search, GPU scale benchmark correctness gates, launcher-route observability, benchmark launcher attribution, scoped GPU device probing, benchmark launcher warnings, opt-in `tg agent` Actionable Context Capsule, mixed-language capsule confidence/validation alignment, GPU benchmark recommendation hygiene, edit JSON/rollback safety, explicit language/file-name agent ranking, Windows validation-command quoting, docs/version governance, `$file` / `{file}` validation placeholder substitution, native CUDA correctness gates, ambiguous capsule alternative-target surfacing, root help-menu diagnostics, foreign launcher diagnostics, benchmark promotion-gate taxonomy, agent workflow benchmark governance, capsule alternative-confidence capping, generic provider-token `secrets-basic` regex rules, release-docs synchronization, release wheel Cargo prefetch retries, native GPU/search accuracy hardening, explicit Windows Python subprocess launcher repair, agent capsule hardcase routing, Windows subprocess bridge ranking hardening, and long-lived agent-loop memory/cache caps are released through `v1.75.2` GitHub assets and PyPI. Follow-up work should focus on context/session latency, GPU production viability, token economy, call-site evidence, AST parity roadmap, classify provider/cache UX, and keeping docs synchronized with release proof.

- Recent fix commits:
  - `a840cd4 fix(search): tg search --rank errored in plain-text mode (#275)`
  - `1137537 fix(license): declare Apache-2.0 consistently across Cargo.toml + npm (#271)`
  - `b0c7cf6 fix: harden v1.13.14 dogfood contracts`
  - `1e09e59 fix: bound agent-loop memory and dogfood contracts`
  - `21e5437 fix: collect capsule call-site evidence`
  - `8a73f8d fix: harden agent bridge ranking`
  - `b601366 fix: harden agent output budget hygiene`
  - `2aebac6 fix: harden ast cli contract hygiene (#140)`
  - `bbc08e4 fix: harden rg flag contract aliases (#139)`
  - `21627d2 fix: harden v1.12.8 dogfood contracts`
  - `f848748 fix: route cold rg-shaped searches to rg (#137)`
  - `c2e483a fix: harden exe bridge agent ranking (#136)`
  - `cdbdfcc fix: accept ast run pattern aliases (#135)`
  - `3940b15 fix: bound map and context agent outputs (#134)`
  - `0f03e58 fix: cap compat routing artifact payloads (#132)`
  - `b746dec fix: bound edit-plan repo scans (#131)`
  - `55c1f1d fix: harden v1.12.7 release positioning governance (#133)`
  - `da44a2f fix: harden v1.12.6 dogfood cli contracts`
  - `1783e92 fix: harden Windows subprocess exe bridge`
  - `f75e24a fix: harden gpu proof benchmark hygiene`
  - `affe7a7 fix: keep rust validation for agent cli intents`
  - `6b2016c fix: clarify ast subset positioning`
  - `b038ed5 fix: restore compat schema governance`
  - `aeead68 fix: align public search flag routing`
  - `a78e33c fix: harden post-release docs governance`
  - `2100122 fix: harden release docs stamp governance`
  - `361e0db fix: harden public GPU unavailable routing`
  - `87d4ca4 fix: accelerate fixed multi-pattern native search`
  - `ada6a47 fix: expose classify provider provenance (#110)`
  - `6ad69b5 fix: harden agent capsule hardcases (#109)`
  - `9ddd20b fix: expose GPU promotion blockers`
  - `dd995fc fix: add explicit Windows subprocess launcher repair`
  - `b0df720 fix: harden v1.10.8 release docs governance`
  - `6ee1d53 fix: harden v1.10.7 dogfood followups`
  - `57f9ada fix: harden gpu search accuracy contracts`
  - `03db0ff fix: harden v1.10.4 dogfood followups`
  - `8aecfea fix: harden release wheel retries`
  - `ca9df12 fix: harden v1.9.9 dogfood followups`
  - `21449bf fix: add agent workflow benchmark governance`
  - `f300cf3 fix: refresh stale tg.com bridge after upgrade`
  - `4ff7a77 fix: clarify GPU benchmark promotion gates`
  - `05ea29e fix: harden v1.9.5 dogfood blockers`
  - `23e5f52 fix: harden GPU gates and launcher diagnostics`
  - `646b089 fix: harden docs governance and validation placeholders`
  - `73c5f91 fix: harden agent ranking docs and validation quoting`
  - `faf67ed fix: harden edit JSON and capsule validation trust`
  - `5791489 fix: harden agent capsule trust alignment`
  - `e2bd7c2 fix: scope GPU probing and benchmark launcher warnings`
  - `ab2635a fix: expose launcher route observability`
  - `015fad9 fix: harden public launcher and agent contracts`
  - `e6d09a5 fix: preserve quoted patterns in Windows cmd shim`
  - `7742258 fix: harden native front-door CLI parity`
  - `4dcc6d7 fix: refresh managed native front door after upgrade`
  - `8420cab fix: harden stable installer and upgrade resolution`
  - `6f82d14 fix: publish GitHub release native assets from main CI`
  - `7b38bbb perf: use native front door for managed installs`
  - `ef0c114 fix: harden v1.8.23 dogfood regressions`
  - `19e515d fix: add generated-root scan guardrails`
  - `8a061ee fix: improve agent context trust and rg parity`
  - `1bf2c76 fix: ignore stale native binaries in dev resolution`
  - `10cac14 fix: polish CLI version help and doctor diagnostics`
  - `a5fa279 fix: write WSL bash shims with LF newlines`
  - `98fa9ab fix: harden Windows and WSL installer shims`
  - `e2ebbd2 fix: uninstall stale Python tg launcher owners`
  - `6c2e59c fix: skip inaccessible PATH entries in Windows installer`
  - `32293c0 fix: harden Windows launchers and path-list output`
  - `f98a6e4 fix: correct Windows installer pinned extras`
  - `1a06cba fix: remove stale Windows tg launchers`
  - `379b22f fix: harden tg resolution and rg path parity`

**Historical release proof (pre-v1.17.11 — retained for the audit trail). The authoritative current-release facts are the `release_docs_current_tag` / current-tag fields above; the run IDs below are OLD (v1.11.0–v1.13.x) and are NOT proof of the current release:**

- `v1.11.0` GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.11.0> exists, but main CI run `25834508800` was cancelled during release-native asset publication; `publish-success-gate` failed and PyPI latest remains `1.10.10`.
- Main CI run `26513809791`: passed the pre-release matrix, semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- Main dynamic/CodeQL run `26513808787`: passed on the `3c0c213` merge commit
- Release commit `bd7035c`: published `v1.13.23` with `[skip ci]` after main CI completed
- Previous `v1.13.22` proof runs `26473492381` and `26473490540` remain retained as historical release proof
- Previous `v1.13.21` proof runs `26450640497` and `26450639894` remain retained as historical release proof
- Previous `v1.13.20` proof runs `26437847778` and `26437847528` remain retained as historical release proof
- Previous `v1.13.19` proof runs `26431129535` and `26431129155` remain retained as historical release proof
- Previous `v1.13.18` proof runs `26425383595` and `26425914836` remain retained as historical release proof
- Previous `v1.13.15` proof runs `26386327552`, `26386327168`, `26386976717`, and `26386978124` remain retained as historical release proof
- Main CI run `25951521056`: passed the pre-release matrix, semantic-release, PyPI wheel/sdist validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- Main CodeQL run `25951813292`: passed on the `v1.12.14` release line
- PyPI pinned install: `uvx --refresh-package tensor-grep --from tensor-grep==1.75.2 tg --version` reports `tensor-grep 1.75.2`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.75.2>
- Main CI run `25866871838`: passed the pre-release matrix, semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- GitHub release assets: `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, checksums, winget manifest, Homebrew formula, and publish instructions are uploaded and verified on `v1.12.14`
- Public `v1.12.14` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.14 tg --version` verified `tensor-grep 1.12.14`; the release includes `21e5437 fix: collect capsule call-site evidence` while preserving `8a73f8d fix: harden agent bridge ranking`, `b601366 fix: harden agent output budget hygiene`, `2aebac6 fix: harden ast cli contract hygiene (#140)`, `bbc08e4 fix: harden rg flag contract aliases (#139)`, and the accepted v1.12.8-v1.12.13 dogfood contract fixes. Public managed GPU is not promotion-ready.
- Public `v1.12.12` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.12 tg --version` verified `tensor-grep 1.12.12`; the release includes `b601366 fix: harden agent output budget hygiene` while preserving `2aebac6 fix: harden ast cli contract hygiene (#140)`, `bbc08e4 fix: harden rg flag contract aliases (#139)`, `21627d2 fix: harden v1.12.8 dogfood contracts`, `f848748 fix: route cold rg-shaped searches to rg (#137)`, `da44a2f fix: harden v1.12.6 dogfood cli contracts`, bounded map/context output, `tg run --pattern`, Windows subprocess bridge ranking hardening, `a78e33c fix: harden post-release docs governance`, `361e0db fix: harden public GPU unavailable routing`, `2100122 fix: harden release docs stamp governance`, and the `87d4ca4 fix: accelerate fixed multi-pattern native search` CPU lane from `v1.11.3`. Explicit public GPU requests without sidecar configuration report native GPU unavailable and fall back to `NativeCpuBackend`; public managed GPU is not promotion-ready.
- Public `v1.11.5` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.11.5 tg --version` verified `tensor-grep 1.11.5`; the release includes `a78e33c fix: harden post-release docs governance` while preserving `361e0db fix: harden public GPU unavailable routing`, `2100122 fix: harden release docs stamp governance`, and the `87d4ca4 fix: accelerate fixed multi-pattern native search` CPU lane from `v1.11.3`.
- Public `v1.11.2` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.11.2 tg --version` verified `tensor-grep 1.11.2`; the release also exposes classify provider provenance so JSON harnesses can distinguish local deterministic classification from opt-in provider-backed classification.
- Public `v1.10.10` GPU evidence remains experimental: explicit managed GPU requests still report `GpuSidecar` / unsupported rather than a qualifying `NativeGpuBackend` row, so no GPU speed promotion is made.
- Public `v1.10.8` dogfood: release CI, assets, PyPI, `uvx --refresh-package tensor-grep --from tensor-grep==1.10.8 tg --version`, managed `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, and direct managed native `tg.exe` all verified `1.10.8`. Python `subprocess.run(["tg", "--version"])` still resolved the foreign Together CLI `tg.exe` from Machine PATH on this host; `tg doctor --json` reported the route as `foreign` with Machine PATH remediation and did not delete or overwrite unrelated launchers.
- Public `v1.10.7` dogfood: release CI, assets, PyPI, managed `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, and managed native `tg.exe` all verified `tg 1.10.7`. The remaining public-launcher blocker was Python `subprocess.run(["tg", ...])` resolving a foreign Together CLI `tg.exe` when Windows `CreateProcess` chooses `.exe` ahead of the tensor-grep `.com` bridge in the same directory.
- Public `v1.9.11` source/GitHub/PyPI dogfood: the release-wheel retry follow-up prefetches Cargo dependencies before PyPI artifact builds, publishes all PyPI distributions, and `uvx --from tensor-grep==1.9.11 tg --version` reports `tensor-grep 1.9.11`.
- Public `v1.9.10` source/GitHub-asset dogfood: the release contains the v1.9.9 dogfood follow-ups, but PyPI publication was incomplete until the v1.9.11 release-wheel retry follow-up published a replacement patch.
- Public `v1.9.9` dogfood: direct managed native `C:\Users\oimir\.tensor-grep\bin\tg.exe --version` reports `tg 1.9.9`; PyPI `tensor-grep==1.9.9` resolves; `uvx --from tensor-grep==1.9.9 tg --version` reports `tensor-grep 1.9.9`; `tg update` advanced the managed sidecar and front door from `1.9.8` to `1.9.9`; fresh `cmd`, unprofiled `pwsh`, and the managed native front door report `tg 1.9.9`.
- Prior public update dogfood: `tg update` from `v1.9.3` initially hit PyPI propagation lag, then installed sidecar `tensor-grep==1.9.4`, scheduled/refreshed the managed native front door, and verified `tg 1.9.4`. Profiled PowerShell, `cmd`, `pwsh -NoProfile`, WSL, Git Bash, and direct managed native `tg.exe` resolved `tg 1.9.4`; `tg doctor --json` reported `version = 1.9.4`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and a `path_tg_launcher_warning` for current shells that still route through the compatibility shim before fresh-shell PATH.
- Prior public installer dogfood: rerunning `scripts/install.ps1` for `v1.8.31` put `C:\Users\oimir\.tensor-grep\bin` ahead of compatibility shim directories on User PATH. A simulated fresh shell resolves `C:\Users\oimir\.tensor-grep\bin\tg.exe` before `C:\Users\oimir\bin\tg.cmd`.
- Public launcher dogfood: `cmd /c tg`, direct managed `tg.cmd`, native `tg.exe`, and Python `subprocess.run([...])` preserve fresh quoted no-match phrases and return exit `1` without false-positive stdout.
- Post-`v1.9.6` local dogfood: native CUDA release search passes exact match/file-set correctness on both RTX 4070 (`sm_89`) and RTX 5070 (`sm_120`) smoke corpora plus 1GB/5GB scale gates, but remains slower than both `rg` and `tg_cpu`; GPU sidecar rows are marked unsupported for native CUDA scale gates unless the benchmark uses a CUDA-enabled native binary; root `tg --help` advertises current agent/GPU/launcher/validation settings; and `tg doctor --json` classifies unrelated first-PATH `tg` commands such as Together CLI as `foreign` with explicit remediation. On this host, local fresh-shell dogfood was repaired non-destructively by placing a tensor-grep `tg.com` bridge ahead of the foreign `tg.exe` in the same directory after `tg update` moved from 1.9.5 to 1.9.6, because Machine PATH ordering was not writable.
- Session handoff: `docs/SESSION_HANDOFF.md`
- Current follow-up work is tracked in `docs/SESSION_HANDOFF.md`: keep release-native assets verified, preserve the managed installer fallback when assets are absent, keep sidecar and native front-door versions aligned after `tg upgrade`, keep current-process vs fresh-shell launcher routing visible in `tg doctor`, preserve benchmark launcher command-kind attribution and warnings, harden the opt-in `tg agent` context capsule/token-economy surface without changing raw search contracts, keep mixed-language capsule confidence/validation alignment honest, and keep GPU/provider paths experimental until correctness, speed, and UX are proven.

The latest accepted release line fixed the Windows `--files-with-matches` rg-backed argument-vector failure, raw rg-style no-path `--files-with-matches` output, malformed pinned Windows installer extras, root-based path-list output, `-0/--null` path-list/count parsing, `tg ast-info --json`, argv-safe PowerShell shims, UTF-8 path-list output, inaccessible PATH-entry handling, managed shim installation, stale Python package cleanup when an old `Python*\Scripts\tg.exe` shadows managed shims, argv-safe `.cmd` bridging, Git Bash / WSL no-extension shims, WSL-aware `/mnt/c/...` paths, LF-only generated bash shims, one-line default version output with verbose details behind `--verbose`, public `Usage: tg` help text, explicit `doctor` diagnostics for stale in-tree native binaries, implicit stale-native skipping for dev searches, public `--format rg` help text for exact ripgrep-style output, context-render/MCP trust invariants, validation command provenance, sorted rg parity edges for files-with-matches, files-without-match, replacement output, and PCRE2 output, multiline rg parity forwarding, exact-symbol context ranking over camel/snake bridge heuristics, explicit language/file-name ranking for Python intent, session stale-file filtering and no-runner validation consistency, embedded checkpoint fallback for MCP rewrite apply when standalone native `tg` is unavailable, inline scan rule severity/message preservation, uppercase `API_KEY` secret scanning, explicit broad generated-root scan refusal unless callers bound the search or opt in, managed native front-door refresh after `tg upgrade`, native-front-door parity for `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, and `tg classify --format json`, classify fallback before expensive provider/model setup when unavailable, GPU benchmark no-match correctness handling, Windows `.cmd` quoted multi-word no-match patterns from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])`, Windows installer User PATH ordering that puts the managed native front-door directory ahead of compatibility shim directories, top-level `validation_commands` on both `context-render` and `edit-plan` JSON, deterministic local default `classify` unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` opts into CyBERT/Triton, GPU benchmark defaults/correctness checks for 1GB and 5GB scale rows, explicit GPU device probing that does not initialize or warn about unselected GPUs, benchmark script warnings when timings include shim or interpreter overhead, stale in-tree native binary benchmark refusal by default, parseable edit JSON and rollback on validation failure, quoted Windows validation commands with spaces, `$file` / `{file}` validation placeholder substitution, per-edited-file validation for directory rewrites, and docs-governance tests aligned with current release metadata.

Known current weak spots:

- `rg` remains the raw cold exact-text benchmark; `tg` should be treated as the agent-native code intelligence layer.
- `ast-grep` remains the structural-search feature/performance baseline; `tg run` is a useful validated AST slice, not a blanket ast-grep replacement.
- `context-render` and MCP context output are agent trust surfaces. `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, selected files/sources, follow-up reads, and `rendered_context` must agree or `context_consistency` must report the omission and confidence downgrade.
- Agents must inspect top-level `ambiguity` before editing. `ambiguity.status = "tie_requires_confirmation"` is a hard stop for autonomous edits. `ambiguity.status = "tie_resolved"` is acceptable only when `ambiguity.resolved_by` contains explicit evidence.
- Default JSON/LLM context rendering must include executable behavior for selected functions. Compact rendering can strip low-value text, but it must not reduce selected code to signatures unless a future summary-only profile explicitly asks for that.
- Validation commands are hints with provenance. Require `validation_plan[].detection`, do not suggest npm/package-manager commands without `package.json` evidence, do not suggest Python test commands without Python/test/project evidence, and omit commands entirely when no runner evidence exists.
- Validation commands must align with the selected primary target language unless verified cross-language dependency evidence exists. `validation_alignment` should report filtered mismatches; do not silently pair a TypeScript primary target with pytest-only validation or a Python primary target with JS-only validation.
- Unbounded broad generated-root scans are hostile to unattended agents. `tg search --files --hidden` and no-ignore/unrestricted fallback scans now refuse roots that are generated/cache/dependency directories, or that contain them, unless the request is bounded by `--glob`, `--type`, or `--max-depth`, or explicitly opts in with `--allow-broad-generated-scan`. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in. `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- `tg map`/`tg orient` and `tg inventory` scan different file-count tiers by design, not by bug: `tg map`/`tg orient` AST-index a bounded set of files (`--max-repo-files` defaults to `DEFAULT_AGENT_REPO_MAP_LIMIT = 2000`, `src/tensor_grep/cli/repo_map.py`, full parse per file; note the separate per-file caller-scan ceiling `CALLER_SCAN_FILE_CEILING = 512`), `tg inventory` walks up to `DEFAULT_MAX_INVENTORY_FILES = 50000` files (`src/tensor_grep/cli/inventory.py`, stat + 8KB sniff, no parse), and a raw `tg search` scans the full tree with no file-count cap. Do not read a larger `tg inventory` total than `tg map`'s `files` count on the same repo as a discrepancy to fix.
- Prefer `blast-radius` over `impact --symbol` when direct symbol impact matters.
- Windows launcher/path-list hardening should force UTF-8 for managed shims and Python path-list output; still scope broad file-list commands to avoid generated-tree volume.
- If `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, or Python `subprocess.run(["tg", "--version"])` resolves a tensor-grep-owned or self-identifying tensor-grep `Python*\Scripts\tg.exe` ahead of the managed native front door, treat it as installer regression evidence. The Windows installer and `tg repair-launcher` should remove verified-owned launchers or back up self-identifying orphaned tensor-grep launchers instead of only warning about them. If that command reports another product's version, treat it as a foreign PATH-shadow blocker: report remediation and keep readiness failing, but do not delete or overwrite the unrelated launcher unless the operator explicitly runs `tg repair-launcher --allow-foreign-rename`, which backs it up first. Python subprocess resolution is a separate Windows contract because `CreateProcess` can choose a foreign same-directory `tg.exe` even when shells prefer a tensor-grep `tg.com` bridge through `PATHEXT`.
- Normal PowerShell should invoke `tg` or `tg.ps1`. Directly invoking `C:\Users\oimir\bin\tg.cmd` from PowerShell with an unescaped metacharacter such as `|` is still a `cmd.exe` parser limitation; quote the argument for `cmd.exe` or use the PowerShell shim. The quoted multi-word no-match pattern case from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` is a public launcher contract and must not split into a shorter false-positive search plus bogus paths.
- Implicit native-binary resolution must ignore stale in-tree binaries such as `rust_core/target/debug/tg.exe` and `rust_core/target/release/tg.exe`. `uv run tg doctor --json` should report them under `skipped_native_tg_binaries`, set `rust_binary_version_status = stale-skipped`, and keep `search_acceleration_backend = rust-core-extension` when the embedded extension is available. Rebuild with `C:/Users/oimir/.cargo/bin/cargo.exe build --manifest-path rust_core/Cargo.toml --release` or pin `TG_NATIVE_TG_BINARY` to opt in to a specific standalone binary.
- Raw unsorted output ordering is semantic parity, not golden stdout parity. Use `--sort path` when deterministic path ordering matters and `--format rg` when automation needs exact ripgrep-style text formatting. Sorted files-with-matches, files-without-match, and replacement output are rg parity regression surfaces in the validated compatibility set.
- `tg search --json` is tensor-grep aggregate JSON, not ripgrep JSON Lines. `tg search --format rg --json` is the explicit ripgrep JSON Lines compatibility route and deliberately emits raw rg events without the tensor-grep envelope. `tg search --ndjson` is tensor-grep's flattened streaming row schema, not the rg event schema. Do not describe default `--json` or `--ndjson` as rg JSON compatibility.
- `edit-plan`, MCP `tg_edit_plan`, and session edit-plan should keep the agent command-surface budget flags aligned with `agent` / `context-render` (`--max-files`, `--max-sources`, `--max-tokens`, and related schema fields) while preserving the core contract that edit-plan emits no rendered source text.
- `tg new` must never silently ignore unknown scaffold arguments and write root files. Unsupported shapes should fail before writing; supported rule/test/util scaffolds must respect `--base-dir` and create only the requested item.
- Stable managed install scripts and `tg upgrade` are part of the public launcher contract. When release-native assets exist, the public front door should launch the matching native `tg` binary first and set `TG_SIDECAR_PYTHON` / `TG_NATIVE_TG_BINARY`; Python remains the sidecar or fallback, not the normal exact-text first hop. On Windows, put the managed native front-door directory ahead of compatibility shim directories on User PATH so `cmd`, unprofiled PowerShell, and Python subprocess calls resolve `~/.tensor-grep/bin/tg.exe` before the slower argv-safe `.cmd` bridge. A release that updates installer URLs is incomplete until GitHub release assets are uploaded and verified, not merely PyPI-published. Stable installers should clear stale package metadata before resolving `tensor-grep`, check native installer command exit codes before committing the staged install, and stage the new managed environment plus front-door files before replacing an existing install. `tg upgrade` should skip yanked PyPI releases, never report "latest PyPI version" from unchanged local metadata without verifying the target Python can import `tensor_grep`, refresh the managed release-native front door to the verified sidecar version, schedule a Windows retry helper when the running native `tg.exe` is locked, and require the scheduled Windows self-upgrade helper to verify the expected version too.
- `tg doctor --json` should expose launcher route state, not just version parity. Check `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, `python_subprocess_path_tg_first_launcher_kind`, `path_tg_launcher_warning`, and any `*_is_foreign` / `*_foreign_remediation` fields before interpreting Windows benchmark results; an existing shell can still be using the slower compatibility shim after User PATH has been fixed for fresh shells, Python subprocesses can resolve differently from shells, and unrelated tools can own a different `tg` command.
- Cold-path benchmark artifacts should include both `tg_launcher_mode` and `tg_launcher_command_kind`. Benchmark scripts should emit top-level warnings when the timed `tg` command is a `.cmd` shim, `uv`, Python-module route, or stale in-tree native tg binary. Stale in-tree native binaries must block claim-quality benchmark scripts by default unless the operator passes `--allow-claim-unsafe-launcher` for exploratory timing. Do not compare or market timings until native-exe, `.cmd` shim, `uv`, Python-module, and stale-binary routes are separated in the artifact with `tg_binary_version_status`.
- The native front door must not reject public flags advertised by the Python CLI. If a surface is still Python-backed, route it to the sidecar deliberately and add a public-native regression test plus dogfood coverage for the installed command shape. Current parity-sensitive examples are `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, `tg classify --format json`, advertised rg-style search flags, and option-first root `tg ...` forwarding.
- `classify` should be quiet and deterministic by default. It should use local heuristics unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` explicitly opts into the CyBERT/Triton provider, and provider failures should fall back before tokenizer/model loading.
- GPU benchmark correctness must treat no-match as a real comparator outcome. `rg` exit code `1` with empty output is valid when `tg` also returns no matches. GPU scale gates should include 1GB and 5GB rows and exact match/file-set correctness for every >=1GB GPU corpus before any GPU promotion claim. Explicit `--gpu-device-ids` routing must not initialize or warn about unselected GPUs.
- GPU benchmark auto-recommendation must remain false unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at the required scale and declared workload class. The current CUDA-native speed wedge is many fixed strings over a large corpus; single-pattern cold grep remains an `rg` lane. Unsupported-device inventory warnings must not be attached to unrelated selected-GPU timing rows. Any GPU-requested CPU fallback or sidecar route must surface `gpu_evidence_status = unsupported`, `gpu_proof = false`, `native_gpu_unavailable`, and `not_gpu_proof_reason`; unsupported rows must use `promotion_evidence = false`. Public managed GPU promotion additionally requires managed NVIDIA front-door provenance from `tg-native-metadata.json`, direct `rg --json` 1GB/5GB match-identity correctness, and `benchmarks/run_gpu_native_benchmarks.py --public-managed-proof` producing `public_managed_promotion_ready = true` and `public_gpu_proof = true` from the dispatch-only `public-gpu-proof.yml` workflow; local CUDA-feature binaries are implementation evidence, not public managed promotion proof.
- `edit-plan` and `context-render` JSON should expose top-level `validation_commands` so agents do not need command-specific parsing to find the validation list.
- Token-efficiency work must be opt-in and contract-aware. Lessons from `rtk` point toward a bounded agent output profile with hard caps, grouped excerpts, truncation, and omission counts; do not change raw `--format rg`, `--json`, or `--ndjson` semantics to save tokens.
- The product wedge is not "faster grep." It is an agentic code-intelligence runtime: given a task, identify what matters, explain why, emit bounded context, suggest validation, preserve rollback, and report confidence. `tg agent` / Actionable Context Capsule is the opt-in command for that workflow.
- The Actionable Context Capsule contract includes the primary file/function, route rationale, bounded source snippets with line maps, detected validation commands, risk level, suggested edit order, checkpoint or rollback metadata, omission counts, confidence, call-site evidence status, and an "ask user before editing" recommendation when uncertainty or risk is high. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected.
- Capsule confidence must be honest when query language hints, exact symbol intent, primary target language, selected snippets, and validation commands disagree. In mismatch cases, cap both `confidence.overall` and `primary_target.confidence`, expose `query_language_hints`, `primary_target_language`, `validation_alignment`, and `validation_filtered_count` in `context_consistency`, and require ask-before-editing.
- Future search-intent routing should label evidence honestly as `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, or `stale/uncertain`. The router can combine text search, AST, symbol graph, imports, tests, and docs, but it must report the route instead of hiding backend choice.
- LSP provider availability is not proof of working semantic navigation. Treat `tg lsp-setup` / `tg doctor --with-lsp` availability as install evidence only; provider-backed navigation must report `health_status`, `health_check`, `lsp_proof`, `lsp_evidence_status`, and `not_lsp_proof_reason` when it falls back to native evidence. A navigation row counts as LSP proof only when it carries `lsp_provider_response = true` from a completed provider request; `provenance = "lsp-*"` alone is not enough. Keep `lsp` / `hybrid` optional and experimental until real provider-backed requests are latency-bounded, reliable, and measurably better on accepted hardcase artifacts.
- `tg callers` and `tg blast-radius` JSON carry an additive `result_incomplete` field (v1.17.0, #281). `result_incomplete = true` means the scan hit an output or scan cap and the call-site list is TRUNCATED — do not treat a truncated zero-caller result as confirmed dead code. A clean scan that resolves zero callers emits a separate "resolved zero-caller" caveat, and even then is not proof of dead code: the call graph cannot see set/list/decorator/dispatch-table registration sites. Cross-check with `tg scan` or pattern grep before removing a zero-caller symbol.
- `tg callers` is Python-first (`docs/harness_api.md`): call-site resolution matches Python AST call nodes most reliably and can under-match or run for minutes on large TypeScript/JS repos. Dogfood receipt (v1.19.3): on a TS-heavy repo, `tg refs` returned 14 reference sites for a symbol where `tg callers` returned 1. Prefer `tg refs` for TS/JS symbol navigation; still cross-check with `tg scan`/grep per the registration-completeness blind-spot note above.
- Running `tg search PATTERN` with no path (or `tg search --glob X -l` without a scoped path) against this repo hangs ~600 s then errors: tg's own index dirs (`.tensor-grep/`, `_tg_refs/`, `.tg_semantic_index/`) and the vendored `benchmarks/external_repos/` tree are not auto-excluded and hit the default `TG_RG_TIMEOUT_SECONDS=600`. Scoped search runs in ~0.4 s. Workaround: always scope `tg search` to an explicit path (e.g. `tg search PATTERN src/`). Planned fix: own-dir excludes + fail-fast timeout + trigram-hybrid index.
- BM25/IDF-ranked surfaces (`tg search --rank`, agent-capsule, local semantic search) are sensitive to corpus changes: adding code that introduces or repeats query-adjacent terms lowers those terms' corpus-wide IDF, which can flip a ranking result and silently degrade a safety behavior. This IDF blast-radius is invisible to the call graph (no caller/callee edge exists for a ranking shift). Harden tie/marker detection to be robust to IDF shifts rather than relaxing a failing test — relaxing masks a real degradation. Tracked as capsule-hardening Task #4 (ledger B3).

## Operating Rules

1. Start with a failing test when behavior changes.
2. Make the smallest defensible change.
3. Run local gates before pushing, but keep them scoped on this desktop unless the user explicitly approves heavy validation. Prefer targeted tests locally and use PR/main CI for full pytest, full Rust test/clippy matrices, benchmark suites, release asset builds, and other high-memory gates.
4. Benchmark every hot-path change.
5. Reject regressions even if the code is otherwise clean.
6. Do not change workflow, release, or docs contracts without updating the validator-backed tests.
7. Do not run `wsl --shutdown`, restart WSL, stop Docker/WSL services, kill WSL processes, or reboot/restart the host as memory cleanup without explicit user approval. Other agents use WSL. If memory pressure is observed, first collect read-only process/memory evidence, stop only tensor-grep-owned processes you started, and ask before touching unrelated processes.

## Adding a Command or Flag

Adding a top-level `tg COMMAND` requires four registration points or the new command silently misroutes:

1. `KNOWN_COMMANDS` in `src/tensor_grep/cli/commands.py` — the Python-side known-command registry.
2. A `Commands::X` passthrough variant and a matching dispatch arm in `rust_core/src/main.rs` — the native front door must know about it.
3. `PUBLIC_TOP_LEVEL_COMMANDS` in `tests/e2e/test_routing_parity.py` — the contract test that enforces parity between Python and native.
4. A `@app.command` function in `main.py` — the Typer app entry point.

Adding a search flag (e.g. `tg search --myflag`) requires two front doors or the flag leaks to ripgrep and causes an `rg: unrecognized flag` crash at runtime:

1. `SEARCH_PYTHON_PASSTHROUGH_FLAGS` in `rust_core/src/main.rs` — the native binary's allowlist.
2. `bootstrap._TG_ONLY_SEARCH_FLAGS` in `src/tensor_grep/cli/bootstrap.py` — the Python bootstrap's allowlist (the Python front door runs before the Typer app and forwards plain searches to rg).

Missing either slot lets the flag reach ripgrep for users who install the published binary while your CliRunner tests pass cleanly — exactly how the `--rank` crash shipped undetected.

**Registration-completeness is a universal bug class, not a tg quirk.** "Add a thing that must be registered in N places, miss one, it fails *quietly*" hit tg here (the `--rank` flag missed one of two front doors) and a downstream user's billing code (a new `/v1` route missed the cron registration + a `test_route_scope_coverage` exemption — green tests, broken route). Before claiming any registration change is done, **enumerate all N sites**. `tg callers <registration-function>` lists every *callable* registration in ~1s — but the call graph **cannot see set/list/decorator registrations** (an allow-list like `bootstrap._TG_ONLY_SEARCH_FLAGS`, `@router.post`, dispatch tables), and those are often the missed site (`--rank` lives in a *set*, not a call — `callers` would never have found it), so **grep / `tg scan` those**. Confirm your new entry appears in *all* sites. This is the default audit path (`tg callers` for blast radius → `tg scan` for pattern bugs → `tg doctor --with-lsp` for diagnostics); the principle is Hard Rule 6 in `verify-plan-against-code`, and the call-graph blind spots are in `tensor-grep-code-audit` (P7).

As of v1.17.1 (#282), the CI registration-completeness gate is BLOCKING — a registration mismatch fails the CI run, not just warns. The checker's member extractor is now string/comment-aware, so `#`-commented entries are no longer surfaced as false registered members.

## Dogfood the Real Binary, Not CliRunner

The `tg` entry point is `tensor_grep.cli.bootstrap:main_entry`. It intercepts plain text searches and forwards them to ripgrep **before** the Typer app sees the argv. `CliRunner` invokes the Typer app directly and bypasses this front door entirely — so bugs in the bootstrap routing layer are invisible to unit tests.

After adding or changing a search flag or command, dogfood the **installed published binary** using the harness at `scripts/dogfood/` (Dockerfile + `dogfood_features.py`). The harness installs the real PyPI wheel and runs every public command shape through the actual `tg` binary. Do not rely on `CliRunner` alone for routing coverage.

## Verify AI-Drafted Plans Against the Real Code Before Building

Before implementing a plan produced by an AI subagent or any external planning pass, check every factual claim in the plan against the real source files by citing `file:line`. A claim with no citation should be treated as a hypothesis, not a fact.

This matters because AI-generated plans have a consistent failure mode: they identify plausible-sounding edit locations that do not match the actual code structure (dead code paths, renamed symbols, already-fixed lines). A verification pass that reads the real files before implementation is not overhead — it is the gate that prevents wasted cycles. A council or read-only review that cites file:line evidence caught 5 blockers in two unverified plans in a single session.

Re-run any validation a subagent claims to have passed — subagents can assert success without executing. For PRs that ship generated or detached code (install scripts, Windows self-upgrade helpers), adversarial-review by EXECUTING the code, not only reading it: `compile()` + `exec()` the generated string and assert the behavior (e.g. that the checksum gate fires BEFORE `os.replace`, and that the fail-closed branch is reachable). Test behavior, not substrings.

After building, run a mandatory post-build ADVERSARIAL AUDIT — a distinct named stage from the pre-build planning council. This audit caught a HIGH CUDA-fork hazard that 203 passing tests missed. A finding or claim with no `file:line` citation is DISCARDED. Re-audit → fix-wave → re-audit until ZERO must-fix findings remain; that zero-finding state is the convergence gate before promoting a build to a draft PR.

## Backend Fail-Closed Contract

Every `ComputeBackend` MUST raise `BackendExecutionError` on a real failure — never return a clean empty / `0-match` `SearchResult` (see `backends/base.py`), and never silently swap to a different engine that cannot preserve the requested semantics. The search loop catches `BackendExecutionError` to fall back **visibly** (e.g. to CPU); a swallowed failure or a silent engine swap reaches the user (or a coding agent) as a trustworthy "no matches" — the one failure a context tool cannot afford.

This contract is violated repeatedly. The recurring anti-pattern is a bare `except Exception:` that returns an empty result or falls through to a different engine. Instances fixed across audits: the Rust/PCRE2 bridge (ran `--pcre2` through the Python-regex engine), the ast-grep wrapper OOM mask (a killed subprocess read as a clean 0-match), the tree-sitter query swallow (invalid pattern → silent 0-match), and CyBERT's classify fallback (keyword-heuristic hits labeled as real model output). When a path CAN fall back to a different engine:

- **Fail closed** for any flag/contract the fallback cannot preserve (e.g. `--pcre2` through a non-PCRE2 engine): raise, do not swap.
- If a degraded fallback is legitimate (e.g. heuristic classification when the model is down), make the swap **visible**: set a `fallback_reason` (and a distinct `routing_reason`) on the `SearchResult` so JSON/CLI consumers can tell degraded output from real output. Never label heuristic output as model output.
- Validate an untrusted response shape before indexing (e.g. a model's class count vs a fixed label list) so a mismatch degrades gracefully instead of raising an uncaught `IndexError` that a broad `except` then swallows.

The same discipline applies beyond backends: any router/pipeline that can silently override an explicit user intent (e.g. an explicit `--gpu` request quietly routed to CPU) must instead raise `ConfigurationError` or emit a diagnostic. A systemic `SafeBackendMixin` + a fault-injection conformance CI gate (every registered backend must raise, not return empty, when its engine call fails) is the planned structural fix so this stops recurring one file at a time.

## Roadmap Sequencing (2026-07-02)

The GPU native-backend program (the P1 CUDA-PFAC kernel and beyond) is **held at the already-shipped P0 harness** — the correctness taxonomy, the loud non-promotional fallback, and the `doctor`/proof fields. Do **not** advance to P2–P4 (kernel, correctness gates, fair-bench, device/CI proof) until three CPU-only, every-install wins ship first:

1. **Local hybrid semantic search** (BM25 + CPU dense embeddings fused with RRF, no API key) — the #1 validated user ask and the biggest competitive gap. Reference architecture: MinishLab `Semble` (tree-sitter chunking + `potion-code-16M` Model2Vec + BM25 + RRF, CPU-only, MIT).
2. **`tg registration-check` productized** as a first-class command — a real-use-validated agent-native differentiator no plain grep/ast-grep offers.
3. **A Bloom-filter n-gram chunk prefilter** for the slow non-literal-regex full-scan path in `rust_core`, which is far more broadly felt than the GPU program's narrow many-pattern-resident niche.

Rationale: the project's own docs place raw search speed (where GPU competes) in the **parity tier, not the moat**; GPU is currently slower than CPU with no promotion-ready path; and the heuristic auto-GPU route is effectively dead code whenever ripgrep is installed (the common case). The moat is the **agent-native context layer** (`orient` / `callers` / blast-radius / the token-efficient capsule), so engineering capacity funds that first. Explicit `--gpu-device-ids` stays supported and must fail loud when it cannot be honored (see the Backend Fail-Closed Contract).

## Security Hardening Patterns (Round-3 audit lens)

A round-3 security sweep (shipped v1.17.23–v1.17.25) fixed four recurring classes. Each is a **sweep target**, not a skill: current models already apply the fix when *writing fresh code* (baseline-tested), so these live here to be checked proactively — the bugs lived in already-committed code where no one re-verified. When you touch the named area, confirm the pattern holds.

- **Symlink-follow disclosure** (any tree walk or copy that snapshots/restores a user/repo tree). Following symlinks copies the *content* of out-of-root targets into the snapshot — and can re-materialize them on restore. Use `os.walk(root, followlinks=False)` + `shutil.copy2(src, dst, follow_symlinks=False)`. Fixed in `checkpoint_store.py` (`_filesystem_snapshot_entries` + all 3 copy sites).
- **Pre-auth unbounded read / no timeout** (any socket/pipe handler that reads *before* authenticating). Bound the read (`readline(max_bytes + 1)` + refuse over-cap) and set a socket timeout **before** the auth check, or an unauthenticated client exhausts memory or pins a worker thread. Fixed in `session_daemon.py` (`_read_bounded_request_line` + handler `timeout`).
- **Atomic-write permission window** (any temp-then-rename of a sensitive file, e.g. a token). Create the temp at the restrictive mode from byte one via `os.open(path, O_WRONLY | O_CREAT | O_EXCL, mode)` — never `write_text()`-then-`chmod`, which leaves a world-readable window; `O_EXCL` also refuses a pre-existing temp/symlink. Fixed in `session_store.py` (`_write_json_atomic`).
- **Native-argv flag injection** (CWE-88; the MCP-276 threat class — a *live* CVE family in MCP servers: CVE-2026-5058 aws-mcp-server, CVE-2026-23744, CVE-2026-30623 Anthropic MCP SDK). Any builder that appends a user/LLM-controlled value as a positional to a subprocess/native `tg`/`rg`/`git` command. A list-argv (`shell=False`) stops *shell* injection but **not** *flag* injection: a value beginning with `-` is parsed by the child's own option parser as a flag. Insert a `--` end-of-options sentinel **before** the user positionals. CAVEATS worth knowing: `--` protects only what comes *after* it (a user positional *before* `--` is still injectable); it does not gate `--flag=VALUE`; and not every binary honors it — **dogfood the real binary** (`tg search -- --weird` matches; `tg search --weird` errors). Fixed in `mcp_server.py` (`_build_rewrite_command`, `_build_index_search_command`); **remaining tg sweep** (tracked): the other native-argv builders + MCP write-path confinement. The three defenses layer — validate the value, list-argv, and `--` — and none alone is complete.

## EvidenceReceipt Signing (Ed25519)

`tg evidence emit` always attaches a keyless `receipt_sha256` integrity digest; `--sign` additionally Ed25519-signs it (`tg evidence verify` / `keygen` / `pubkey`), so a separate downstream consumer (e.g. gotcontext) can verify a receipt without ever holding a key that could forge one — the reason this uses an asymmetric algorithm rather than `tg audit`'s same-operator HMAC-SHA256 (`audit_manifest.py`). All crypto is isolated in `src/tensor_grep/cli/evidence_signing.py`. Two points worth knowing when touching this area:
- **S2 trust-bootstrap**: an embedded public key only proves internal self-consistency, never authenticity — `verify` always reports the signer's fingerprint (recomputed from the actual key bytes, never a claimed label) and only upgrades `key_trusted` to `True` against an out-of-band pinned `--trusted-key`/`TG_EVIDENCE_TRUSTED_KEYS`, compared with `hmac.compare_digest`. `--require-trusted` is the flag that fails `valid` closed on an unpinned key.
- **Fail-closed**: `--sign` with no resolvable key (or `cryptography` unavailable) is a non-zero exit with no receipt written — never a silent unsigned fallback (the `--pcre2` anti-pattern this contract exists to prevent). Full wire format + canonicalization rule: [docs/CONTRACTS.md](docs/CONTRACTS.md#8-evidencereceipt-signing-tg-evidence-emit---sign--tg-evidence-verify); design spec: `docs/plans/backlog-100/cluster-124-evidence-signing.md`.

## Skills

Three kinds of skills apply to this repo; load the relevant one before non-trivial work.

- **Using `tg` itself** — `.claude/skills/tensor-grep/SKILL.md` (+ `REFERENCE.md`): the agent-usage skill for the command surface (`search`, `search --rank`, `orient`, `map`, `agent`, `session`, AST, blast-radius). Keep it in sync whenever commands/flags change.
- **Working ON `tg` (build + release discipline)** — reusable global skills at `~/.claude/skills/`:
  - `dogfood-the-shipped-artifact` — after a release, install the published wheel in clean Docker and run the REAL `tg` binary across every feature; never trust CliRunner (it bypasses the bootstrap front door). Harness: `scripts/dogfood/`.
  - `verify-plan-against-code` — before building an AI/subagent-drafted plan, verify every seam claim (file paths, the command/flag registration sites above, routing) against the real code with `file:line` citations; bake corrections in first.
  - `supply-chain-hardening` — before writing any download / extract / install / self-upgrade / toolchain-bootstrap code, apply the 5 checks (zip-slip guard, byte-capped/time-bound downloads, fail-closed checksum incl. detached helpers, `--locked` pinned CI tools, fail-closed unverified toolchains). Shipped patterns: #283/#284/#285/#287.
  - `worktree-fanout-verification-gate` — before integrating agent branches from a worktree fan-out: remove worktrees before checkout (`git worktree remove --force <path>` — else checkout is blocked and tests silently run main's code); re-run pytest/ruff/mypy in the real venv (worktrees have no `.venv`; agents' "tests pass" claims are hypotheses until then); run `ruff format --preview` on ALL agent-touched files (not only hand-fixed ones); and treat scoped-local-green as a hypothesis, not a merge signal.
- **Carrying the project forward — the in-repo skill library** (`.claude/skills/tensor-grep-*` + `code-search-and-retrieval-reference`, **16 skills**): the onboarding handbook so a new engineer or a Sonnet-class session can debug, extend, validate, and advance `tg` without the original authors. Each auto-loads by its `description`; load the one matching your task. Index by intent:
  - **Change safely:** `tensor-grep-change-control` (the gates), `tensor-grep-debugging-playbook`, `tensor-grep-failure-archaeology` (don't re-fight settled battles), `tensor-grep-validation-and-qa`.
  - **Understand:** `tensor-grep-architecture-contract`, `code-search-and-retrieval-reference` (domain theory), `tensor-grep-config-and-flags`.
  - **Operate:** `tensor-grep-build-and-env`, `tensor-grep-run-and-operate`, `tensor-grep-diagnostics-and-tooling`, `tensor-grep-docs-and-writing`, `tensor-grep-release-and-positioning`, `tensor-grep-workspace-dogfood` (multi-repo stress dogfood), `tensor-grep-enterprise-agent` (enterprise readiness gaps + agent hard-stops).
  - **Advance (SOTA):** `tensor-grep-semantic-search-campaign`, `tensor-grep-benchmark-and-proof-toolkit`, `tensor-grep-research-frontier`, `tensor-grep-research-methodology`.
- When working ON tensor-grep, use `tg search`/`tg defs`/`tg callers` for code navigation rather than generic grep/find — this exercises the tool's own surfaces and catches routing regressions early (mind the scoped-path workaround above).

These encode the "Adding a Command or Flag", "Dogfood the Real Binary", and "Verify AI-Drafted Plans" sections above as reusable, project-independent skills.


## Dogfood follow-up workflow

When public dogfood identifies multiple independent fixes, preserve the process that has been working:

1. Turn each concrete failure or feature gap into PR-sized slices; do not collapse independent fixes into one broad PR.
2. Before implementation, use Exa research for current external contracts and tooling behavior that the fix depends on, especially `rg`, `ast-grep`, CUDA/GPU, packaging, GitHub Actions, and agent-evaluation surfaces.
3. Run a thinktank or equivalent independent planning review when the dogfood item changes product positioning, benchmark interpretation, GPU promotion criteria, or release workflow. The council must cite `file:line` for every seam claim; uncited claims are hypotheses, not facts.
4. Before fan-out: commit the corrected plan to the shared branch OR inline the full slice spec in every agent prompt. Worktrees branch off HEAD and will not contain uncommitted files — a plan written but not committed is invisible to fan-out agents. Decompose the corrected plan into worktree-isolated agent slices.
5. For each slice, write or update the contract test first, implement the smallest fix, run the targeted suite, then run lint and format before moving on.
6. ORCHESTRATOR VERIFICATION GATE — after every agent branch returns, the orchestrator must verify before integration: (a) remove each worktree (`git worktree remove --force <path>`) before checking out the branch in the main repo — an un-removed worktree blocks checkout and causes a main-repo test run to silently execute main's code, not the branch's; (b) re-run pytest/ruff/mypy in the real venv, since worktrees have no `.venv` and agents' "tests pass" / "N tests green" claims are hypotheses until re-run there; (c) run `ruff format --preview` on EVERY file in `git diff main --name-only`, not only hand-fixed files — agents couldn't run ruff, so their files come back un-`--preview`-formatted; (d) treat scoped-local-green as a hypothesis, not a merge signal — lint/format run repo-wide, one unrelated failing test reddens the whole test-python job, and corpus side-effects are outside scoped test scope. See the global skill `worktree-fanout-verification-gate`.
7. Integrate the verified slices onto one branch, resolving any overlaps.
8. ADVERSARIAL AUDIT (3 lenses + chairman) — run a citation-enforced adversarial audit of the integrated diff; this is a mandatory stage distinct from the pre-build planning council (the post-build audit caught a HIGH CUDA-fork hazard that 203 passing tests missed). A finding with no `file:line` citation is discarded. Re-audit → fix-wave → re-audit until ZERO must-fix findings remain. The endpoint is a DRAFT PR; never auto-merge.
9. Ask Gemini for a bounded read-only review of each PR diff before merge; treat its findings as hypotheses until checked against local files and tests.
10. Push each branch, wait for PR CI, squash-merge intentionally, then watch main CI. Release-bearing work is not complete until semantic-release, assets, PyPI, and public release dogfood pass.

Maintain a per-slice evidence ledger in `docs/SESSION_HANDOFF.md`, `SKILL.md`, and this file when operating practice changes. Each slice entry must record PR order, slice scope, Exa research anchors, thinktank or planning consensus, subagent ownership, Gemini review result, validation commands, PR CI, and main CI. Optional or triggered items may be marked `not applicable` only with a rationale. For release-bearing slices, additionally require semantic-release, release assets, PyPI, and public release dogfood evidence.

Current dogfood slice ledger:

- PR order: 13; scope: close the `v1.13.20` dogfood daemon-upgrade and LSP-diagnostic follow-up by snapshotting pre-upgrade session daemon state, restarting the daemon after direct or scheduled Windows upgrade handoff loss, stripping inherited Python runtime variables from managed LSP provider launch environments, and suppressing stale Pyright SRE mismatch stderr tails once a current provider request proves healthy while preserving failed-proof stderr; Exa anchors: CPython/uv SRE mismatch reports connecting the error to mismatched Python runtime/stdlib environment; thinktank/planning consensus: read-only subagent reviews required using the pre-upgrade daemon root and preserving failed-proof stderr; subagent ownership: Popper and Copernicus read-only plan review, implementation local; Claude Opus review: PASS with low findings, addressed by preserving non-SRE suppressed stderr as `provider_recent_stderr` and carrying daemon restart roots into the scheduled Windows helper; validation: targeted upgrade/LSP tests, focused LSP suites, ruff, preview format, mypy, and diff whitespace passed locally; PR CI: PR #233 passed; main CI: semantic-release published `v1.13.21` at `1b62da7`, main CI run `26450640497` passed, CodeQL/dynamic run `26450639894` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.21 tg --version` proof passed.
- PR order: 12; scope: harden the `v1.13.19` built-in dogfood timeout gap by giving `tg dogfood` a wrapper timeout, passing an incremental child `--output` to `scripts/agent_readiness.py`, preserving partial running reports, and cleaning up the launched child process tree by PID only; Exa anchors: Python subprocess timeout semantics and psutil process-tree termination guidance; thinktank/planning consensus: not applicable because this is an internal harness lifecycle fix, with Zeno read-only subagent review confirming the timeout and descendant-cleanup root cause; subagent ownership: Zeno read-only call-path review, implementation local; Claude Opus review: no blocker/high findings (`OPUS_REVIEW: PASS`); validation: targeted dogfood/readiness/docs tests, ruff, preview format, mypy, and diff whitespace passed locally; PR CI/main CI: PR #231 passed, squash merge produced `6525853`, semantic-release published `v1.13.20` at `c41d475`, main CI run `26437847778` passed, CodeQL/dynamic run `26437847528` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.20 tg --version` proof passed.
- PR order: 11; scope: harden the `v1.13.18` daemon-cache dogfood gap by letting capped or truncated implicit session snapshots bypass added-file stale detection for daemon-routed top-level `context-render` / `edit-plan` cache writes while preserving explicit added-file refresh for complete sessions; Exa anchors: not applicable because this is internal daemon/session cache behavior; thinktank/planning consensus: systematic-debugging trace plus read-only subagent review isolated the stale-detection failure before `response_cache.put()` and required an added-file refresh regression test; subagent ownership: Wegener read-only plan/diff review, implementation local; Claude Opus review: no blocking findings, optional capped-modification stale-refresh test added; validation: targeted docs/session tests pass (`47 passed`), `uv run --no-sync ruff check .`, `uv run --no-sync ruff format --check --preview . --exclude .tmp --exclude .tensor-grep --exclude src/.tensor-grep`, `uv run --no-sync mypy src/tensor_grep`, and `git diff --check` pass locally; full pytest/Rust matrices and benchmark suites intentionally deferred to PR/main CI unless the user approves heavy desktop validation; PR CI/main CI: PR #230 passed, squash merge produced `0c9155f`, semantic-release published `v1.13.19` at `b9197a6`, main CI run `26431129535` passed, CodeQL/dynamic run `26431129155` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.19 tg --version` proof passed.
- PR order: 10; scope: harden `v1.13.17` dogfood regressions by making non-JSON rg-shaped explicit no-ignore searches prefer ripgrep passthrough when `rg` is available while preserving the native fallback when it is not, preserving tensor-grep aggregate JSON semantics, resolving top-level `context-render` / `edit-plan` daemon requests to absolute directory roots so repeated relative invocations can populate and hit the daemon response cache, and documenting desktop memory-safety operating rules for local validation; Exa anchors: official ripgrep guide/manpage behavior for `--no-ignore` and `-u` disabling ignore filtering; thinktank/planning consensus: read-only subagent review agreed the no-ignore fast path should stay in the rg-shaped non-JSON lane and the daemon cache fix should normalize request paths at the top-level caller boundary; subagent ownership: McClintock read-only plan/diff review, implementation local; Claude Opus review: accepted findings for direct JSON/NDJSON passthrough tests, no-ignore-vcs coverage, guarded daemon path normalization, daemon-start assertions, and absolute cleanup; validation: targeted daemon path/cache tests, targeted Rust routing test, ruff, preview format check, cargo fmt check, and diff whitespace check passed locally; full pytest/Rust matrices and benchmark suites intentionally deferred to PR/main CI unless the user approves heavy desktop validation; PR CI/main CI: PR #229 passed, squash merge produced `77a73b2`, semantic-release published `v1.13.18` at `4a0dad0`, main CI run `26425383595` passed, CodeQL/dynamic run `26425914836` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.18 tg --version` proof passed.
- PR order: 7; scope: close concrete `v1.13.11` dogfood regressions by deduplicating `defs --provider hybrid` native/LSP definition rows while preserving LSP proof, bounding checkpoint discovery cache priming at the user-home boundary so Windows standalone `checkpoint create` does not write `C:\Users\.tensor-grep`, separating MCP protocol/CLI version fields in capabilities, sharpening the PowerShell `Start-Process`/`tg.ps1` MCP stdio warning, suppressing stale LSP stderr tails once a provider request proves healthy, routing `tg audit --help` to audit help instead of search, and broadening `secrets-basic` fake API key detection; Exa anchors: official MCP lifecycle/version negotiation docs and LSP 3.17 `Location`/range semantics for merge identity; thinktank/planning consensus: compressed read-only review through subagents because the separate thinktank spawn hit the agent thread limit; Aquinas recommended explicit MCP protocol versus CLI fields, Cicero recommended post-merge LSP/native dedupe with LSP proof preservation and quiet successful provider status, and Ohm recommended home-bounded checkpoint discovery plus explicit native-`tg.exe` MCP stdio warning; subagent ownership: Aquinas (MCP), Cicero (hybrid/LSP), Ohm (checkpoint/doctor/audit); Gemini review: unavailable because `gemini-3-flash-preview --approval-mode plan` stalled after startup/tool noise and was killed without a report; validation: targeted checkpoint, semantic-provider, LSP-provider, trust/audit, MCP, doctor, scan, docs, and integration tests pass locally; `uv run pytest -q` passes (`2451 passed, 16 skipped`); `uv run ruff check .`; `uv run ruff format --check --preview .`; `uv run mypy src/tensor_grep`; full Rust crate tests; cargo fmt check; `uv run python scripts/agent_readiness.py --no-shell-probes --no-wsl-probe --json` passes (`13 passed, 0 failed`); direct Windows checkpoint-create smoke, direct agent-studio hybrid-defs smoke, audit-help smoke, MCP-capabilities smoke, public-command contract smoke, and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 1; scope: accept and forward remaining rg config-override flags (`--pcre2-unicode`, `--ignore`, `--messages`, `--require-git`, `--no-hidden`) in native/Python search and add installed-public sweep coverage; Exa anchors: ripgrep manpage option inversion/config behavior plus ripgrep guide automatic-filtering defaults; thinktank/planning consensus: local planning review, external council not applicable for this parser/forwarding contract slice; subagent ownership: not applicable; Gemini review: unavailable because Gemini CLI 0.42.0 hung on a one-token read-only model probe and was killed; validation: Rust crate tests, full pytest, lint, format, mypy, and diff whitespace checks pass locally; PR CI/main CI: pending.
- PR order: 1; scope: make `run_agent_success_harness.py` refuse stale in-tree native `tg` binaries by default and mark `--allow-claim-unsafe-launcher` runs as exploratory; Exa anchors: not applicable beyond existing benchmark-governance policy; thinktank/planning consensus: local planning review aligned with `run_benchmarks.py` stale-binary refusal; subagent ownership: not applicable; Gemini review: unavailable because Gemini CLI 0.42.0 hung on a one-token read-only model probe and was killed; validation: Rust crate tests, full pytest, lint, format, mypy, and diff whitespace checks pass locally; PR CI/main CI: pending.
- PR order: 1; scope: accept and forward the 25 remaining ripgrep inverse/config-override flags found by `parser_sweep_1_12_31_codex.json`, including `--no-auto-hybrid-regex`, `--no-pcre2-unicode`, `--no-text`, `--no-binary`, `--no-follow`, `--ignore-dot`, `--ignore-vcs`, `--no-json`, and `--no-stats`, and batch those 25 installed-public sweep probes into one command to avoid adding dogfood latency; Exa anchors: current ripgrep guide/manpage behavior for config override flags plus local `rg 15.1.0` acceptance sweep; thinktank/planning consensus: local planning review only, external council not applicable because this is parser/forwarding contract work and does not alter GPU/LSP/product positioning; subagent ownership: not applicable, no subagents requested for this turn; Gemini review: unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted parser/backend/readiness tests, full `test_public_native_cli_parity`, direct built-native acceptance of all 25 flags, full Python/Rust suites, lint, format, mypy, diff whitespace, and fast readiness pass locally; PR CI/main CI: pending.
- PR order: 1; scope: add `world_class_readiness.status = "not_claimed"` plus `agent_target_selection_metrics` to `tg dogfood` reports so a PASS cannot be mistaken for full rg replacement, full ast-grep replacement, public GPU promotion, production LSP proof, or enterprise target-selection accuracy; Exa anchors: ripgrep JSON/config-override docs, ast-grep CLI docs, Cursor/Sourcegraph agentic context docs, and NVIDIA CUDA profiling/transfer guidance; thinktank/planning consensus: Gemini plan-mode read-only review rejected a separate `next_pr_slices` planning array as source-of-truth duplication and recommended adding the missing target-selection surface to the existing limitations contract; subagent ownership: not applicable, no Codex subagents requested for this turn; Gemini review: completed for planning, final diff-review retry unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted dogfood/docs tests, full Python/Rust suites, lint, format, mypy, diff whitespace, and fast readiness pass locally; PR CI/main CI: pending.
- PR order: 2; scope: make GPU promotion workload-scoped in benchmark artifacts and public dogfood/docs, including `promotion_scope = "declared_workload_class_only"`, fair many-pattern baseline `rg -F -e ... -e ...`, and candidate classes for `many_fixed_patterns_single_dispatch` / `resident_repeated_query`; Exa anchors: CUDA-grep final/checkpoint reports on transfer amortization and many-regex workloads, NVIDIA CUDA Graphs and pinned-memory async transfer docs, and ripgrep `-F`/`-e` multiple-pattern docs; thinktank/planning consensus: read-only GPU proof and release-governance seats both recommended an artifact/schema hardening PR rather than CUDA kernel work; subagent ownership: Jason reviewed GPU performance/proof, Lovelace reviewed release/governance; Gemini review: unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted GPU benchmark contract, dogfood, public docs, benchmark-script, and readiness tests; `uv run ruff check .`; `uv run ruff format --check --preview .`; `uv run mypy src/tensor_grep`; `cargo fmt --manifest-path rust_core/Cargo.toml --check`; `cargo test --manifest-path rust_core/Cargo.toml`; `uv run pytest -q` (`2248 passed, 16 skipped`); `uv run python scripts/agent_readiness.py --no-shell-probes --no-wsl-probe --json` (`12 passed, 0 failed`); and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 3; scope: add public managed GPU proof plumbing with `tg-native-metadata.json`, Python upgrade/install script metadata writers, `--public-managed-proof`, and artifact fields `public_managed_promotion_ready` / `public_gpu_proof`; Exa anchors: NVIDIA Blackwell compatibility guidance, cudarc 0.19 CUDA 13/dynamic-loading docs, and GitHub Actions GPU runner docs; thinktank/planning consensus: Gemini plan-mode review rejected path-shape-only proof and recommended explicit managed front-door provenance; subagent ownership: attempted read-only Codex explorer, but the agent thread limit was reached, so implementation stayed local; Gemini review: planning review completed with file-read limitation, final diff review not run yet; validation: targeted runtime/installer/GPU benchmark/docs tests (`91 passed`), `uv run pytest -q` (`2261 passed, 16 skipped`), `uv run ruff check .`, `uv run ruff format --check --preview .`, `uv run mypy src/tensor_grep`, and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 4; scope: add a dispatch-only public managed GPU proof workflow and strengthen the native GPU proof gate so public promotion requires fixed GPU runner labels, managed NVIDIA asset verification, direct `rg --json` 1GB/5GB correctness, `NativeGpuBackend`, `sidecar_used = false`, and speed wins over both `rg` and `tg_cpu`; Exa anchors: GitHub Actions self-hosted/GPU runner docs, NVIDIA Blackwell/CUDA compatibility docs, CUDA compute-capability docs, and ripgrep JSON output semantics; thinktank/planning consensus: Mill/Mencius/Descartes agreed to separate public proof workflow/governance from local CUDA implementation evidence and to reject weak `promotion_ready` summaries; subagent ownership: Mill reviewed workflow scope, Mencius reviewed release/security workflow requirements, Descartes reviewed benchmark proof semantics; Gemini review: unavailable; `gemini-3.1-pro-preview` stalled after startup with no report and was stopped; validation: targeted GPU benchmark contract, benchmark-script, release-workflow validator, and release asset validator tests pass locally; PR CI/main CI: pending.
- PR order: 1; scope: close the `v1.12.33` rg column-override edge by accepting and forwarding `--column --no-column` through both `tg search --format rg ...` and root-level `tg --format rg ...`, add installed-native sweep coverage, improve stale repo-local `uv run tg` warmup diagnostics, and pin the `ripgrep binary resolution` capsule hardcase; Exa anchors: ripgrep inverse/config-override docs where last flag wins, ripgrep JSON/output docs for preserving rg-vs-tg schema boundaries, Sourcegraph/Cody context docs for agent target-selection evidence, LSP initialize-timeout evidence for keeping LSP experimental, and CUDA-grep transfer-amortization notes for keeping GPU unpromoted; thinktank/planning consensus: two read-only seats recommended this narrow contract/readiness/capsule regression slice and explicitly rejected raw-speed, GPU, LSP, or ast-grep claim changes; subagent ownership: thinktank seats Lagrange and Hegel reviewed the plan, implementation stayed local due tight parser/readiness coupling; Gemini review: attempted with gemini CLI 0.42.0 / gemini-2.5-flash in read-only plan mode; unavailable because the model returned an invalid empty stream / malformed tool call; validation: targeted rg contract/parity tests, readiness stale-entrypoint and flag-sweep tests, agent hardcase test, Rust parser unit test, Rust public-native parity test, full Rust crate tests, full pytest, lint, format, mypy, fast readiness, and diff whitespace passed locally; PR CI/main CI: PR #163 passed, squash merge produced `c0cb613`, main CI run `26094452260` passed semantic-release, GitHub release assets, PyPI publish, and `publish-success-gate`; release/public proof: `v1.12.34` tag/release assets exist and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.34 tg --version` reports `tensor-grep 1.12.34`.

## Required Local Validation

Run these before push for normal code changes:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

CI runs `ruff format --check --preview .`. Running only `uv run ruff check .` is not enough to prove formatter parity, and running `ruff format` WITHOUT `--preview` actively REVERTS preview-style formatting on disk — a "clean" bare `ruff format` will undo CI-mandated style and red the next `ruff format --check --preview` run even when local lint passes. Always pass `--preview` to `ruff format` locally; never pass it to `ruff check`. The trailing `.` (whole repo) is load-bearing too: under `--preview`, ruff formats Python code fences INSIDE Markdown, so a scoped run (`ruff format --check --preview src/tensor_grep tests`) passes locally yet MISSES an unformatted `docs/**/*.md` snippet — which reds CI's release-gating `static-analysis` job and blocked v1.67.0. Always run the whole-repo `.` form; never a `src`/`tests` subset.

`uv run pytest -q` can take substantially longer than 70-90 seconds on this Windows machine when the full JS/TS and e2e surface is hot; use a timeout of at least 120 seconds for narrow suites and a much larger timeout for the full suite when running it through automation.

For focused changes, run the relevant narrow suite first, then the full suite if the change is intended to land:

```powershell
uv run pytest tests/unit/test_cpu_backend.py -q
uv run pytest tests/unit/test_cli_bootstrap.py -q
uv run pytest tests/unit/test_release_assets_validation.py -q
```

For fast pre-push dogfood on agent-critical surfaces, run the agent-readiness dogfood gate:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

This 3-5 minute gate checks public shell version resolution, `public-version-python-subprocess`, `public-windows-launcher-quoted-patterns`, installed-public advertised search flag acceptance via `public-search-advertised-flag-sweep`, repo doctor sanity, `context_consistency`, `agent-capsule`, `agent-capsule-mixed-language`, `agent-capsule-hardcases`, deterministic rg edge parity, broad generated-root scan guardrails, AST smoke, MCP context-render smoke, and docs claim hygiene. `tg dogfood` wraps the same readiness gate with a one-page verdict and JSON envelope. It complements, not replaces, the full local validation gate.

For release dogfood, include this compact public path checklist:

```powershell
gh release view <tag>
pip index versions tensor-grep
uvx --refresh-package tensor-grep --from tensor-grep==<tag> tg --version
tg upgrade
cmd /c tg --version
pwsh -NoProfile -Command "tg --version"
tg doctor --json
```

`tg doctor --json` must show matching sidecar/native versions and should expose any current-shell, fresh-shell, or Python-subprocess foreign launcher route.

## Benchmark Rules

Never claim a speedup without measured numbers.

Use the right benchmark for the area you changed:

### End-to-end CLI text search

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
```

This is the main `tg` vs `rg` comparison. Use this for:

- plain search routing
- startup / launcher changes
- text-search control-plane changes

### Repeated-query / hot cache paths

```powershell
python benchmarks/run_hot_query_benchmarks.py --output artifacts/bench_hot_query_benchmarks.json
```

Use this for:

- StringZilla index changes
- CPU regex prefilter changes
- persisted cache / decode / posting-list changes

`repeated_regex_native` must stay on native/Rust routing such as `cpu_rust_regex`; do not force a Python fallback in hot-query probes. For sub-10ms benchmark rows, use an absolute jitter tolerance in addition to ratio checks.

### AST single-query benchmark

```powershell
python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
```

### AST workflow startup benchmark

```powershell
python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
```

Use this for:

- `run`
- `scan`
- `test`
- AST workflow startup / batching / wrapper orchestration

### Agent capsule / edit-loop workflow benchmark

```powershell
python benchmarks/run_agent_workflow_benchmarks.py --output artifacts/bench_agent_workflow.json
python benchmarks/run_agent_success_harness.py --output artifacts/bench_agent_success_harness.json
```

Use this for:

- `tg agent` capsule routing
- confidence / alternative target surfacing
- validation alignment and filtering
- rollback, edit order, and whole-loop edit latency
- end-to-end query intent -> context -> edit seed -> apply -> verify -> rollback success

This is workflow evidence, not a cold exact-text search speed claim.

### GPU / NLP backend benchmark

```powershell
python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_run_gpu_benchmarks.json
```

Notes:

- `cyBERT` may skip if Triton is unavailable.
- Treat `SKIP` as expected infrastructure state, not a fake failure.

## Performance Discipline

Use these rules consistently:

1. Compare against the current accepted baseline, not memory.
2. Reject candidates that are slower or only “faster” in a microprofile while slower end-to-end.
3. Keep both cold-start and repeated-query measurements in mind.
4. Do not update docs or the paper with speed claims until the benchmark line is accepted.
5. If a candidate is correct but slower, revert it and record the attempt.

## CI / Release Rules

CI is not just a test runner. It enforces:

- formatting
- linting
- typing
- cross-platform behavior
- release workflow contracts
- package-manager workflow contracts
- artifact/version parity

Any new download / extract / install / self-upgrade helper must apply the v1.17.2–v1.17.5 supply-chain patterns (see the `supply-chain-hardening` skill): (a) zip-slip guard — validate every member path against the resolved dest before `extractall` (reuse the production `_safe_extract_zip`); (b) time-bound + byte-capped downloads — `urlopen(timeout=...)` / socket timeout + a byte cap (256 MiB for native assets); (c) checksum-gated fail-closed installs — embed the expected SHA from `CHECKSUMS.txt` and verify before `os.replace`, INCLUDING in the detached Windows self-upgrade helpers; (d) `--locked` + exact version pins for CI tools (e.g. `cargo-audit==0.22.2 --locked`, `cargo-deny --locked`) — an unpinned `cargo install` can pull a breaking upstream release mid-CI.
(e) uv's `.ps1` installer LACKS binary checksum verification (uv issue #13074) while the `.sh` self-verifies (uv >=0.11.0, pinned 0.11.25); Windows fix = download the pinned uv RELEASE BINARY + verify a COMMITTED dual-arch (x86_64 + aarch64) SHA-256 fail-closed before use (implemented in `scripts/install.ps1` + a new `scripts/uv_checksums.json`, landing with PR #302 — not yet on `main`); discipline: ALWAYS download + `Get-FileHash` to CONFIRM a committed SHA — never trust an agent's "fetched from the sidecar" value.
(f) ACCEPTED BOOTSTRAP TRUST BOUNDARY (documented, not a gap): the toolchain bootstrappers are trusted-over-HTTPS + version-pinned, NOT checksum-gated like the release artifacts WE download — uv's `.sh` self-verifies its binary (uv >=0.11.0, pinned 0.11.25), and rustup is fetched via `curl https://sh.rustup.rs | sh` in the semantic-release `build_command` (pyproject.toml) then pinned with `rustup default 1.96.0` (rustup self-verifies the toolchain). This is a deliberately different posture from (a)-(e), which checksum-gate artifacts WE fetch/extract. De-piping rustup to a pinned-binary + committed-checksum download is a tracked follow-up — it touches the release `build_command`, so it is ATTENDED (do not change it autonomously).

Do not casually edit:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `scripts/validate_release_assets.py`

If you change workflow, docs, or release behavior, expect to update validator-backed tests too.

Read `docs/CI_PIPELINE.md` before editing CI, release, Dependabot, or audit automation. That file is the canonical contract for how the pipeline is supposed to behave and what follow-up validators must change with it.

Important test surface:

- `tests/unit/test_release_assets_validation.py`
- workflow/package-manager/release validator suites

## Routing / Architecture Guidance

Be honest about workload classes.

- Cold generic text search:
  - `rg` is still the baseline.
  - control-plane overhead matters more than backend cleverness.
- Repeated text search:
  - indexing can beat cold grep-style tools.
- AST workflows:
  - batching and orchestration matter as much as backend logic.
- GPU:
  - only wins when workload size and arithmetic intensity amortize transfer and startup cost.

Do not assume:

- more caching is always faster
- compiled onefile binaries are always faster
- GPU is always faster
- a micro-optimization is worth landing without end-to-end proof

## Native vs Python Reality

The repo has proven:

- Python-side startup cuts help
- repeated-query indexing helps
- AST batching helps
- onefile Nuitka binaries are not currently the speed path on Windows for plain passthrough

If the goal is to close the remaining gap to raw `rg`, the likely next step is a more native launcher/control-plane path, not more Python micro-tuning.

## Push Discipline

Do not push from a dirty worktree if `origin/main` moved and the local tree has unrelated changes.

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state. Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

Merge one release-bearing PR at a time and wait for main CI + semantic-release to finish before merging the next. Concurrent squash-merges to `main` can race at the semantic-release step and produce a skipped release or a wrong version bump. `chore:` / `docs:` / `test:` titles do not bump the version — but that is NOT a licence to merge them while a prior release is in flight (see the push-race note directly below). "Safe to interleave" means *after the prior release has fully published* (its `chore(release): vX` commit is on `main` and PyPI shows the new version), not merely after its PR CI is green.

### Release publish is not instant — the push-race (hard-won, re-confirmed 2026-07-02)

The real publish is the **`Semantic Release` job inside `.github/workflows/ci.yml`** (gated `github.ref == 'refs/heads/main' && github.event_name == 'push'`), NOT `release.yml` (which is `workflow_dispatch`-only, so a manually-pushed `v*` tag can no longer bypass semantic-release). That job **compiles the native assets before it publishes, so it runs for ~6 minutes** — and that whole window is a race window.

If ANY other merge lands on `main` during that window — *including a no-release `docs:`/`chore:` PR* — the merge advances `main`, and the in-flight release job's final `git push origin main` (the `chore(release)` version-bump commit) is **rejected non-fast-forward** (`! [rejected]  main -> main`), so **that version never publishes**. The CI concurrency group is necessary but INSUFFICIENT: it serializes runs, not the human/agent act of clicking merge. Receipt: `v1.17.23` (a security batch, #318) failed to publish because the GPU-pause `docs:` PR (#319) was merged while #318's release job was still compiling assets.

Recovery — **do NOT panic-rerun**: the failure self-heals. The next push-to-`main` CI run re-runs `Semantic Release`, and because the version is **derived from the git tags** (not the failed run's state), it recomputes the correct next version and covers the orphaned `fix:`/`feat:` commit. Just confirm that next run's `Semantic Release` job succeeds and the tag/PyPI version appears; the fix's *code* was already on `main` regardless — only the publish step was behind.

Diagnosing a "didn't publish": decode the structured job result FIRST (`gh run view <id> --json jobs` → find `Semantic Release` → read `--log-failed`). Do not theorize from tracebacks. A `! [rejected]  main -> main` line is the push-race signature; a genuinely different failure is a different problem.

Preferred approach:

1. use a clean replay worktree
2. rebase/reset to current `origin/main`
3. rerun narrow checks and relevant benchmarks
4. push only the accepted change
5. open a PR with the correct conventional title and wait for PR CI/CodeQL to pass
6. if the change is release-bearing and intended to ship now, squash-merge the PR to `main`
7. wait for main CI and semantic-release complete successfully, plus CodeQL, `publish-github-release-assets`, PyPI/package artifact validation, `publish-pypi`, and `publish-success-gate`
8. verify the GitHub release assets, PyPI latest version, and any affected public installer/update path. PyPI/public installer availability is verified before final release status is reported
9. after semantic-release completes, `git fetch origin main --tags` and fast-forward local `main` to the release commit before reporting the final version state

Do not report a release-bearing fix as complete after only a branch push, open PR, or green PR checks. The final report must name the PR, merge commit, main CI run, CodeQL run, released tag/version, PyPI/package publish status, and any local/public installer dogfood result.

For docs/test/chore-only work, use a non-release PR title, wait for PR CI, and merge only when requested or clearly required. After merge, main CI should pass but semantic-release should skip release publishing.

## PR Title And Release Intent

AI-generated PRs must use conventional titles so CI can infer semantic-release intent.

Use this schema:

- `feat: ...` => minor release
- `fix: ...` or `perf: ...` => patch release
- `feat!: ...` or `fix!: ...` => major release
- `docs: ...`, `test: ...`, `chore: ...`, `ci: ...`, `build: ...` => no release

Release-bearing PRs must use `Squash and merge` so the validated PR title becomes the commit subject on `main`.

Do not manually create release tags when semantic-release is active.

## Local Dev Gotchas (Windows, hard-won)

Small, non-obvious traps that have each cost a real cycle on this desktop. None are version-specific.

- **`git commit -m "..."` with backticks runs command substitution.** A message containing `` `...` `` (e.g. a fenced identifier) is interpreted by the shell and mangles the commit. Use `git commit -F <file>` or a single-quoted `<<'EOF'` heredoc for any message with backticks, `$`, or `!`.
- **cargo/rustc are off `PATH` here — and a "hanging" Rust build is almost always a false alarm.** Use `C:/Users/oimir/.cargo/bin/cargo.exe` (or prepend `~/.cargo/bin` to `PATH`). What looks like a hang is slow LTO that *completes*: `maturin develop` is ~15 s, a `--release` build is minutes. Do not kill it as hung; let it finish. (The build command for stale in-tree binaries is under the doctor note above.)
- **Verify FFI / PyO3 bridge changes against the REAL compiled extension, not mocks.** This is the "Dogfood the Real Binary" trap one layer down: mock-based tests passed green while the *real* bridge was dead (it dropped every forwarded flag and silently fell back to the Python engine). Prove a bridge change with a live runtime call into the built extension, then confirm the flag actually reached `rg`.
- **After a squash-merge, apply follow-up fixes by SYMBOL, not by line number.** Merges shift every line below the change; a plan that says "fix `main.py:8468`" is stale the moment anything above it lands. Re-anchor on the function/const name (grep or `tg defs`) before editing.
- **A dependency UPPER-cap can silently downgrade the whole install on a newer Python.** If an upper bound (e.g. `typer<0.25`) has no release compatible with a new Python, `pip`/`uv` resolve the *entire package* DOWN to a stale version with NO error — `requires-python>=X` has no upper bound to catch it. When a fresh Python yields a stale `tg`, suspect a transitive cap (typer/click/pydantic), not `requires-python`.
- **Windows symlink creation needs privilege.** Tests that create symlinks must `pytest.skip` on `OSError` / `NotImplementedError`, or they false-fail on an unprivileged run.
- **A stray `nul` file in the tree is a Windows `2>nul` redirect artifact.** Use `2>$null` (PowerShell) or `2>/dev/null` (bash); clean up with `rm -f ./nul`.
- **CRLF makes a local bare `ruff format --check` false-alarm** over LF-committed blobs. Run `ruff format --preview <files>` (which normalizes) before commit — see "Required Local Validation" for why `--preview` is mandatory and must never be passed to `ruff check`.
- **The full local gate is four steps, not two — and re-run them after your LAST edit.** `ruff check` + `pytest` passing is NOT green: the CI "Formatting & Linting" job also runs `ruff format --check --preview .` (a *formatter*, distinct from the `ruff check` *linter* — a post-edit line-wrap or over-long comment passes `ruff check` but fails `ruff format --check`) AND `mypy src/tensor_grep` (catches type errors nothing else flags, e.g. assigning to a `Final` attribute like click's `UsageError.message` — mutate it and mypy errors; raise a fresh `UsageError(...)` instead). Running only `ruff check` + `pytest` — or running the gate before an *intermediate* edit that a later edit then invalidates — cost two drain-blocking CI failures in a single session (a mypy `Final`-assign and a `ruff format` line-wrap). Run all four (`ruff check` · `ruff format --check --preview` · `mypy src/tensor_grep` · `pytest`) on the touched files AFTER the final edit.

## Documentation Discipline

When a candidate is accepted or explicitly rejected, update:

- `docs/PAPER.md` if it changes the optimization history or benchmark story
- `README.md` / `docs/benchmarks.md` only after accepted benchmark changes

The paper should preserve failed attempts too, so future agents do not retry the same losing ideas.

## Bottom Line

Work like this:

1. test first
2. smallest change
3. local lint/type/test
4. benchmark
5. reject regressions
6. push only measured wins or required correctness/CI fixes

Do not use code-intelligence budget flags as `tg search` options; scope `tg search` with paths, globs, file types, and depth.

