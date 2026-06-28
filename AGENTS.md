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

## Current Handoff

release_docs_current_tag: v1.17.6

As of 2026-06-26, the current tagged release state is `v1.17.6`, and the latest complete public PyPI/release-asset distribution is also `v1.17.6`. The stable installer, release-native asset publication, managed-native `tg upgrade` refresh path, stale tensor-grep-owned `tg.com` bridge refresh after upgrade, native-front-door CLI parity fixes, Windows `.cmd` quoted-pattern launcher fix, native-first Windows PATH ordering, top-level validation-command contract, local default `classify`, classify provider provenance, fixed multi-pattern native CPU search, GPU scale benchmark correctness gates, launcher-route observability, benchmark launcher attribution, scoped GPU device probing, benchmark launcher warnings, opt-in `tg agent` Actionable Context Capsule, mixed-language capsule confidence/validation alignment, GPU benchmark recommendation hygiene, edit JSON/rollback safety, explicit language/file-name agent ranking, Windows validation-command quoting, docs/version governance, `$file` / `{file}` validation placeholder substitution, native CUDA correctness gates, ambiguous capsule alternative-target surfacing, root help-menu diagnostics, foreign launcher diagnostics, benchmark promotion-gate taxonomy, agent workflow benchmark governance, capsule alternative-confidence capping, generic provider-token `secrets-basic` regex rules, release-docs synchronization, release wheel Cargo prefetch retries, native GPU/search accuracy hardening, explicit Windows Python subprocess launcher repair, agent capsule hardcase routing, Windows subprocess bridge ranking hardening, and long-lived agent-loop memory/cache caps are released through `v1.17.6` GitHub assets and PyPI. Follow-up work should focus on context/session latency, GPU production viability, token economy, call-site evidence, AST parity roadmap, classify provider/cache UX, and keeping docs synchronized with release proof.

- Latest verified release proof PR: #285 `fix: supply-chain hardening batch 1 (zip-slip, download timeouts/cap, dead surface)`
- Latest verified release proof merge commit: `e186aa4 fix: supply-chain hardening batch 1 (zip-slip, download timeouts/cap, dead surface) (#285)`
- Latest verified release proof commit: `2bf4211 chore(release): v1.17.4 [skip ci]`
- Latest verified proof public release PR: #285 `fix: supply-chain hardening batch 1 (zip-slip, download timeouts/cap, dead surface)`
- Latest verified proof public release commit: `2bf4211 chore(release): v1.17.4 [skip ci]`
- Latest merged fix commit: `e186aa4 fix: supply-chain hardening batch 1 (zip-slip, download timeouts/cap, dead surface) (#285)`
- Latest merged feature commit: `3a022ec feat: agent-contract completeness signals + Windows LSP / routing / BM25 fixes (#281)`
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
- PyPI pinned install: `uvx --refresh-package tensor-grep --from tensor-grep==1.17.6 tg --version` reports `tensor-grep 1.17.6`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.17.6>
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
- Running `tg search PATTERN` with no path (or `tg search --glob X -l` without a scoped path) against this repo hangs ~600 s then errors: tg's own index dirs (`.tensor-grep/`, `_tg_refs/`, `.tg_semantic_index/`) and the vendored `benchmarks/external_repos/` tree are not auto-excluded and hit the default `TG_RG_TIMEOUT_SECONDS=600`. Scoped search runs in ~0.4 s. Workaround: always scope `tg search` to an explicit path (e.g. `tg search PATTERN src/`). Planned fix: own-dir excludes + fail-fast timeout + trigram-hybrid index.

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

## Skills

Two kinds of skills apply to this repo; load the relevant one before non-trivial work.

- **Using `tg` itself** — `.claude/skills/tensor-grep/SKILL.md` (+ `REFERENCE.md`): the agent-usage skill for the command surface (`search`, `search --rank`, `orient`, `map`, `agent`, `session`, AST, blast-radius). Keep it in sync whenever commands/flags change.
- **Working ON `tg` (build + release discipline)** — reusable global skills at `~/.claude/skills/`:
  - `dogfood-the-shipped-artifact` — after a release, install the published wheel in clean Docker and run the REAL `tg` binary across every feature; never trust CliRunner (it bypasses the bootstrap front door). Harness: `scripts/dogfood/`.
  - `verify-plan-against-code` — before building an AI/subagent-drafted plan, verify every seam claim (file paths, the command/flag registration sites above, routing) against the real code with `file:line` citations; bake corrections in first.
  - `supply-chain-hardening` — before writing any download / extract / install / self-upgrade / toolchain-bootstrap code, apply the 5 checks (zip-slip guard, byte-capped/time-bound downloads, fail-closed checksum incl. detached helpers, `--locked` pinned CI tools, fail-closed unverified toolchains). Shipped patterns: #283/#284/#285/#287.
- When working ON tensor-grep, use `tg search`/`tg defs`/`tg callers` for code navigation rather than generic grep/find — this exercises the tool's own surfaces and catches routing regressions early (mind the scoped-path workaround above).

These encode the "Adding a Command or Flag", "Dogfood the Real Binary", and "Verify AI-Drafted Plans" sections above as reusable, project-independent skills.


## Dogfood follow-up workflow

When public dogfood identifies multiple independent fixes, preserve the process that has been working:

1. Turn each concrete failure or feature gap into PR-sized slices; do not collapse independent fixes into one broad PR.
2. Before implementation, use Exa research for current external contracts and tooling behavior that the fix depends on, especially `rg`, `ast-grep`, CUDA/GPU, packaging, GitHub Actions, and agent-evaluation surfaces.
3. Run a thinktank or equivalent independent planning review when the dogfood item changes product positioning, benchmark interpretation, GPU promotion criteria, or release workflow.
4. Ask Gemini for a bounded read-only review of each PR diff before merge; treat its findings as hypotheses until checked against local files and tests.
5. For each slice, write or update the contract test first, implement the smallest fix, run the targeted suite, then run lint and format before moving on.
6. Push each branch, wait for PR CI, squash-merge intentionally, then watch main CI. Release-bearing work is not complete until semantic-release, assets, PyPI, and public release dogfood pass.

Maintain a per-slice evidence ledger in `docs/SESSION_HANDOFF.md`, `SKILL.md`, and this file when operating practice changes. Each slice entry must record PR order, slice scope, Exa research anchors, thinktank or planning consensus, subagent ownership, Gemini review result, validation commands, PR CI, and main CI. Optional or triggered items may be marked `not applicable` only with a rationale. For release-bearing slices, additionally require semantic-release, release assets, PyPI, and public release dogfood evidence.

Current post-`v1.17.6` dogfood slice ledger:

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

CI runs `ruff format --check --preview .`. Running only `uv run ruff check .` is not enough to prove formatter parity, and running `ruff format` WITHOUT `--preview` actively REVERTS preview-style formatting on disk — a "clean" bare `ruff format` will undo CI-mandated style and red the next `ruff format --check --preview` run even when local lint passes. Always pass `--preview` to `ruff format` locally; never pass it to `ruff check`.

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

Merge one release-bearing PR at a time and wait for main CI + semantic-release to finish before merging the next. Concurrent squash-merges to `main` can race at the semantic-release step and produce a skipped release or a wrong version bump. `chore:` / `docs:` / `test:` titles do not bump the version, so capture PRs can interleave safely.

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

