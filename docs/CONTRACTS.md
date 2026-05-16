# API and Data Contracts

This document defines the backward-compatibility guarantees for data structures and CLI outputs used by enterprise integrations, IDEs, and editor-plane agents.

release_docs_current_tag: v1.12.14

## 1. Configuration (`sgconfig.yml`)
The root-level keys and structure of `sgconfig.yml` are guaranteed to be stable within a major version. Unrecognized keys will be ignored rather than causing fatal errors to allow progressive rollout of new configurations.

## 2. AST Cache (`project_data_v6.json`)
The schema of the `.tg_cache/ast/project_data_v6.json` cache is versioned within its filename.
- Backward compatibility is NOT guaranteed across major/minor versions.
- If the schema changes (for example to `v7`), `tensor-grep` will automatically invalidate older cache files and rebuild them transparently.

## 3. Text-search compatibility
The stable text-search compatibility contract is the validated compatibility set covered by the parity suite and contract benchmark runner. `tg search --help` may expose more rg-style flags, but only the rows below and explicitly named deterministic edges are part of the public rg-compatibility claim.

Current validated rows:
- `-i/--ignore-case`
- `-v/--invert-match`
- `-C/--context`
- `-A/--after-context`
- `-B/--before-context`
- `-g/--glob`
- `-l/--files-with-matches`
- `--files-without-match`
- tensor-grep aggregate `--json`
- ripgrep JSON Lines via explicit `--format rg --json`
- `--ndjson`
- `-F/--fixed-strings`
- `-w/--word-regexp`
- `-m/--max-count`
- `--files`
- `-t/--type`
- `-./--hidden`
- `-L/--follow`
- `-S/--smart-case`
- `-n/--line-number`
- `--column`
- `-c/--count`
- `--count-matches`
- `-a/--text`
- `-0/--null`
- `-U/--multiline`
- `--multiline-dotall`

Character-for-character identity is not required for help formatting, but command presence, supported rows, accepted normalization, and the deterministic parity corpus are part of the public contract. Additional rg-style flags may be exposed in `tg search --help`, but they are not covered by the stable compatibility claim until they are added to the contract suite and benchmark runner.

Agent automation contracts:
- `tg search PATTERN` defaults to the current directory when no path is provided.
- Invalid regex syntax exits as an error distinct from no-match and emits a diagnostic that recommends `--fixed-strings` for literal searches.
- `tg search --json` emits a valid tensor-grep aggregate JSON object even when there are zero matches.
- `tg search --format rg --json` forwards to ripgrep and emits rg JSON Lines events without a tensor-grep envelope for tools that require rg's event schema.
- `tg search --files-with-matches` stays root-based on the ripgrep path instead of expanding large candidate-file lists into the Windows process argument vector, and plain path-list output emits one trailing line separator only.
- `tg search` with `--format rg` is a public exact ripgrep-style text formatter for automation that needs rg-shaped stdout. Pair it with `--sort path` when deterministic path ordering matters across backends.
- The native front door treats `--format rg` as a no-op for rg-compatible text output and preserves `--sort path` when forwarding to ripgrep. Non-rg `--format` search output remains a Python CLI formatting surface.
- The public native front door must not reject flags advertised by the public Python CLI. It may execute the request natively, forward to ripgrep, or route to the Python sidecar, but sidecar routing must be intentional and covered by a public-native regression test.
- `tg search --files` remains a generated-root guardrail surface. The native front door may route it to the Python sidecar so broad generated-root refusal, `--glob`, `--type`, `--max-depth`, and `--allow-broad-generated-scan` keep the same public semantics.
- `tg search --multiline` / `-U`, `--multiline-dotall`, and `-0/--null` must be accepted by the native front door and forwarded to the backend that preserves rg-compatible behavior for the requested output mode.
- `--sort path` is the deterministic contract for `--files-with-matches`, `--files-without-match`, `--replace`, and path-list automation. The parity suite also checks match, no-match, parse-error, and binary-skip exit-code behavior.
- Default output ordering for root-scale `--files-with-matches`, `--count`, and `--force-cpu` is a semantic result parity contract, not a golden stdout ordering contract. Use `--sort path` when automation needs deterministic path ordering across backends.
- Broad generated roots such as `.claude` are routed through Python guardrails instead of rust-first/native passthrough so generated `.claude/context` snapshots can be pruned by default.
- Unbounded broad generated-root scan requests are refused with exit code `2` when hidden file-list scans or no-ignore/unrestricted fallback search paths contain generated, cache, or dependency directories. Scope the path, add `--glob`, `--type`, or `--max-depth`, or pass `--allow-broad-generated-scan` to make the large generated-tree walk explicit.
- `tg search --type-list` prints a built-in fallback list when no ripgrep or standalone native binary is available. `--pcre2-version` follows ripgrep semantics and exits with an error when no PCRE2-capable backend is available.
- `tg ast-info --json` exposes AST language identifiers via `{"languages": [...]}` for agent discovery without help-text scraping.
- On Windows, PowerShell automation should invoke `tg` or `tg.ps1` for regex metacharacters. Direct `.cmd` invocation from PowerShell is not a safe argv-preserving contract for unescaped metacharacters such as `|` because `cmd.exe` parses the line before the batch wrapper receives arguments; `tg.cmd` is for `cmd.exe`.
- The quoted multi-word no-match pattern case from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` is a public launcher contract. The launcher must preserve the phrase as one argv item and must not turn it into a shorter false-positive search plus bogus paths.
- Stable managed install scripts should prefer the matching release-native CPU `tg` binary as the public front door when the GitHub release asset is available, set `TG_SIDECAR_PYTHON` for Python-backed commands, set `TG_NATIVE_TG_BINARY` for the sidecar, and fall back to `python -m tensor_grep` when no native asset exists. On Windows, the managed native front-door directory must be placed on User PATH ahead of compatibility shim directories so `cmd`, unprofiled PowerShell, and `subprocess.run(["tg", ...])` resolve `~/.tensor-grep/bin/tg.exe` before the slower argv-safe `tg.cmd` bridge. If a tensor-grep-owned `tg.com` bridge is copied into another PATH directory to outrank a foreign same-directory `tg.exe`, the bridge must still find `~/.tensor-grep/.venv` for sidecar-backed commands and must point `TG_NATIVE_TG_BINARY` back at the managed front door rather than the bridge copy.
- `tg doctor --json` must make launcher routing observable. It should report `path_tg_first_launcher_kind` for the current process PATH, `fresh_shell_path_tg_first_launcher_kind` for Windows fresh-shell PATH when available, `python_subprocess_path_tg_first_launcher_kind` for Python `subprocess.run(["tg", ...])` style resolution, and `path_tg_launcher_warning` when the current process still resolves a compatibility shim even though a fresh shell would resolve the managed native front door. This warning is diagnostic: it means restart or refresh PATH before timing subprocess-heavy workflows. Python subprocess resolution is its own contract because Windows `CreateProcess` can choose a foreign same-directory `tg.exe` even when shells prefer a tensor-grep `tg.com` bridge through `PATHEXT`.
- `tg doctor --json` must also make GPU search routing observable. `gpu.search_runtime_probe` should run a small fixed-string `tg search --gpu-device-ids ... --json` probe through the resolved native front door and report `requested_gpu_device_ids`, `routing_backend`, `routing_reason`, `sidecar_used`, `routing_gpu_device_ids`, and a `status` that is `supported` only for `NativeGpuBackend` with `sidecar_used = false`.
- If the first PATH or Python-subprocess `tg` reports a version string that is not tensor-grep-shaped (`tg ...` or `tensor-grep ...`), `tg doctor --json` should classify it as `foreign`, set `path_tg_first_is_foreign`, `fresh_shell_path_tg_first_is_foreign`, or `python_subprocess_path_tg_first_is_foreign`, and emit a warning plus remediation field such as `path_tg_foreign_warning`, `path_tg_foreign_remediation`, `fresh_shell_path_tg_foreign_warning`, `fresh_shell_path_tg_foreign_remediation`, `python_subprocess_path_tg_foreign_warning`, and `python_subprocess_path_tg_foreign_remediation`. Foreign launchers are not tensor-grep-owned stale launchers: readiness should fail with explicit remediation, and installer/doctor code must not delete or overwrite unrelated tools. The explicit repair path is `tg repair-launcher --allow-foreign-rename`: it is Windows-only, backs up the first foreign Python-subprocess `tg.exe` to a `.bak` file, installs the verified managed native front door into that same PATH slot, and must refuse to act without the allow flag.
- Stable installers must build replacement managed environments and front-door files in a staging directory and only swap them into `~/.tensor-grep` after package installation and front-door generation succeed. PowerShell installer native commands must check `$LASTEXITCODE` before the staged swap. A failed package resolve/install must preserve the previous managed install and shims.
- `tg upgrade` must not infer "latest PyPI version" solely from unchanged local metadata. It should refresh package metadata, skip yanked PyPI releases when selecting an exact version, pin the latest same-or-newer PyPI version when known, verify the target Python can import `tensor_grep`, and report a verification error instead of success if the sidecar is corrupted. The scheduled Windows self-upgrade helper must run the same import/version verification before writing a success log. Managed installs must also refresh the managed release-native front door to the verified sidecar package version, refresh stale tensor-grep-owned `tg.com` PATH bridges when present, or schedule a Windows native-front-door retry helper when the running native `tg.exe` is locked.
- `tg classify --format json` is a public sidecar command even when invoked through the native front door. Default `classify` should use deterministic local heuristics without probing tokenizer/model providers. JSON output includes additive top-level `classification_backend` provenance with `provider_requested`, `provider_used`, `provider_status`, `fallback_reason`, and `cache.status` so agents can distinguish local, provider, and fallback results without scraping stderr. JSON classification rows include `label`, `confidence`, `file`, `path`, 1-based `line`, and `snippet` so agents can map labels back to source evidence. Operators must opt into the CyBERT/Triton provider with `TENSOR_GREP_CLASSIFY_PROVIDER=cybert`; provider failures still fall back before tokenization or Hugging Face model loading so agent calls fail fast and quietly.
- GPU benchmark correctness compares result sets, not only successful match exits. `rg` exit code `1` is a valid no-match outcome, so no-match patterns must be accepted as correctness passes when `tg` also returns no matches. The GPU scale gates should include 1GB and 5GB rows and exact match/file-set correctness for every >=1GB GPU corpus before any GPU promotion claim. GPU auto-recommendation must remain false unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at the required scale. Explicit `--gpu-device-ids` routing must not initialize or warn about unselected GPUs, unsupported-device inventory warnings must not be attached to unrelated selected-GPU timing rows, and scale benchmarks must record the runtime backend actually handling `--gpu-device-ids`. Sidecar-routed GPU requests are not valid native CUDA scale-gate timings, and the artifact contract must include `fallback_or_sidecar_counts_as_gpu_proof = false`.
- GPU benchmark artifacts must make proof status machine-readable. Top-level `gpu_evidence_status`, `gpu_proof`, `native_gpu_unavailable`, and `not_gpu_proof_reason` distinguish promotion-ready native CUDA evidence from CPU fallback or sidecar compatibility output. Per-row `promotion_evidence = false` and `not_gpu_proof_reason` are required for unsupported GPU rows.
- Cold-path benchmark artifacts must distinguish the configured launcher experiment from the command kind actually being timed. `run_benchmarks.py` records `environment.tg_launcher_mode` and `environment.tg_launcher_command_kind` so native-exe, `.cmd` shim, `uv`, and Python-module timings are not mixed into one speed claim. It must also record `tg_binary_version_status` and warn on a stale in-tree native tg binary before the artifact is used for benchmark claims. Benchmark artifacts should emit top-level warnings when timings include shim or interpreter overhead.
- Agent workflow benchmark artifacts must keep capsule/edit-loop evidence separate from raw search-speed evidence. `run_agent_workflow_benchmarks.py` records the literal positioning `agent-native workflow benchmark; not a cold exact-text speed claim`, then reports `agent_capsule` contract metrics and `edit_loop` search/plan/apply/verify timings. Use it to evaluate confidence, alternatives, validation alignment, snippets, rollback, and edit order, not to imply `tg` beats `rg` for cold exact-text search.
- Agent success harness artifacts must keep end-to-end agent success evidence separate from raw search-speed evidence. `run_agent_success_harness.py` writes `artifacts/bench_agent_success_harness.json`, records the literal positioning `agent-native end-to-end success harness; not a raw search speed claim`, then reports `workflow_surfaces` for `intent`, `context`, `edit_seed`, `apply`, `verify`, and `rollback`. Use it to evaluate whether a task can move from query intent to checkpointed edit and rollback, not to imply `tg` beats `rg` for cold exact-text search.
- The fast agent-readiness dogfood gate is `python scripts/agent_readiness.py --output artifacts/agent_readiness.json`; `tg dogfood --output artifacts/dogfood_readiness.json` wraps the same gate with a verdict envelope for agent and CI logs. For the current `v1.12.14` release line it checks public shell version probes, sidecar-backed public launcher probes, `public-windows-launcher-quoted-patterns`, repo doctor sanity, launcher route diagnostics including foreign launcher ownership, `context_consistency`, deterministic rg parity edges, broad generated-root scan guardrails, AST smoke, MCP context-render smoke, the `agent-capsule` CLI/MCP smoke, the `agent-capsule-mixed-language` trust regression, the `agent-capsule-hardcases` polyglot monorepo/noisy generated-root regression, docs claim hygiene, the managed native-upgrade contract, and the public positioning that `rg` remains the cold exact-text baseline while `ast-grep` remains the structural-search feature/performance baseline.

Context and edit-planning contracts:
- `context-render` and MCP context output must not let `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, rendered source sections, and follow-up reads contradict each other.
- `context_consistency` reports whether the primary file is included, whether rendered context matches the primary target, whether confidence was downgraded, and why a primary file was omitted from rendered budget.
- The default JSON/LLM render profile includes executable body lines for selected functions. Compact rendering can remove comments, docstrings, blank lines, type-only imports, and boilerplate, but it must preserve matched behavior unless the caller requests a summary-only profile in a future contract.
- Validation command hints use `validation_plan[].detection` with values of `detected`, `heuristic`, or `generic`. JavaScript package-manager commands require `package.json` evidence; Python commands require tests, project markers, or Python layout evidence; omit commands entirely when no runner evidence exists instead of inventing `npm test` or `uv run pytest`.
- Validation hints must align with the selected primary target language unless verified cross-language dependency evidence exists. `validation_alignment` records whether validation remained aligned or whether incompatible commands were filtered; a TypeScript primary target must not silently receive pytest-only validation, and a Python primary target must not silently receive JS-only validation.
- Edit validation command templates support `$file` and `{file}` placeholders. For applied rewrites, placeholders are replaced with each edited file path and the command runs once per edited file; commands without a file placeholder run once against the original target working directory. Examples should quote the placeholder, such as `python -m py_compile "$file"`, so Windows paths with spaces remain one argv item.
- `edit-plan` and `context-render` JSON both expose top-level `validation_commands` copied from `navigation_pack` or `edit_plan_seed` for quick agent access. Agents should not need command-specific parsing just to find the recommended validation command list.
- Repo-map and context-ranking defaults exclude generated/cache/dependency directories, binary files, logs, and hidden non-code files from normal code context.
- Future token-efficiency profiles must be opt-in and recoverable. Do not mutate raw `--format rg`, `--json`, or `--ndjson` contracts to save tokens; add explicit agent profiles with hard budgets, grouped excerpts, omission counts, and refetch commands.
- Compact output should cap breadth before cutting semantic payload. Selected functions must preserve matched lines and executable body slices, and omissions should be reported in metadata or clearly non-code delimiters instead of comment-shaped placeholders inside source blocks.
- `tg agent` / Actionable Context Capsule output is a separate agent contract, not a changed search contract. `capsule_version = 1` payloads include primary file/function, alternative targets for plausible non-primary candidates, route rationale, bounded source snippets with line maps, validation evidence with provenance, risk/edit order, checkpoint or rollback metadata, omission counts, confidence, follow-up read commands, call-site evidence status, optional `gpu_acceleration` route evidence, and an "ask user before editing" recommendation when uncertainty or risk is high. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected. Future capsule changes should be additive.
- Agent GPU evidence is opt-in through explicit selected device IDs. It may batch query terms through `tg search --gpu-device-ids ... -e ...` to collect extra evidence, but it must report `NativeGpuBackend` with `sidecar_used = false` before the capsule treats that evidence as used. Sidecar-routed GPU output is compatibility evidence only and must be reported as unsupported, not as GPU acceleration or a speed claim.
- Search JSON and NDJSON should distinguish requested GPU IDs from routed GPU IDs. `requested_gpu_device_ids` records the user-selected IDs; `routing_gpu_device_ids` records the devices actually used by the runtime route. A request that falls back to `NativeCpuBackend` with `gpu-auto-fallback-cpu` must emit `routing_gpu_device_ids = []`; that is CPU fallback, not GPU acceleration proof. JSON envelopes for explicit GPU requests should add `gpu_evidence_status`, `gpu_proof`, `native_gpu_unavailable`, and `not_gpu_proof_reason` so harnesses do not infer GPU proof from the presence of `--gpu-device-ids`.
- Capsule confidence must be honest when query language hints, exact symbol intent, primary target language, selected snippets, and validation commands disagree. In mismatch cases, both `confidence.overall` and `primary_target.confidence` should be capped, `ask_user_before_editing.required` should become true, and `context_consistency` should expose `query_language_hints`, `primary_target_language`, `validation_alignment`, and `validation_filtered_count`. Unresolved equal-confidence alternatives should expose `alternative_confidence_tie`, `alternative_confidence_tie_count`, and `tied_alternative_targets`, cap confidence below the normal edit threshold, require confirmation, and mirror the decision in top-level `ambiguity.status = "tie_requires_confirmation"`; explicit language hints or aligned validation may record the tie as resolved with `ambiguity.status = "tie_resolved"` instead.
- Capsule and search-intent routing evidence should label claims as `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, or `stale/uncertain`. If these signals disagree, confidence should be downgraded and the contradiction should be explicit instead of hidden behind a single ranked file.

Known current limitations:
- Explicitly opted-in broad generated-root walks can still be expensive. The Python path-list output path and managed Windows launchers force UTF-8, but scope file-list commands to the smallest useful root whenever possible.
- Broad generated roots remain agent-hostile when callers opt in to them. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in; `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- `impact --symbol` is a broader planning signal and can be noisier than `blast-radius`; use `blast-radius` for direct symbol impact checks.

## 4. Machine-readable CLI output (`--json` and `--ndjson`)
The JSON schemas emitted by `tensor-grep search --json`, `tensor-grep search --ndjson`, and the documented harness/editor-plane flows are considered public APIs. `tensor-grep search --format rg --json` is a compatibility route for ripgrep's JSON Lines event schema, so consumers should use rg's event contract rather than the tensor-grep envelope for that mode.
- Existing fields (for example `file`, `line`, `match`, `context`) will not be renamed or removed without a major version bump.
- New fields may be added in minor versions.
- Consumers should ignore unrecognized JSON fields.

## 5. Operational diagnostics (`tg doctor --json`)
`tg doctor --json` is intended for operational automation and support workflows.
- Existing top-level sections remain additive-only within a major version.
- Individual diagnostic fields may grow as new probes are added.
- Consumers should treat missing optional fields as a valid state and ignore unknown fields.
- Implicit native-binary resolution must not select stale in-tree `rust_core/target/*/tg(.exe)` binaries. Stale in-tree candidates should be reported under `skipped_native_tg_binaries` with `rust_binary_version_status = stale-skipped`; `TG_NATIVE_TG_BINARY` is the explicit opt-in for using a specific standalone binary anyway.
- PATH candidate probes should distinguish tensor-grep launchers from foreign `tg` commands owned by other software. A foreign command ahead of the managed native front door is an environment blocker for public readiness and benchmark interpretation, not permission to remove the other command automatically.

## 6. Python Library API
Classes and functions exposed in `tensor_grep.api` are stable within a major version. Internal modules (prefixed with `_` or deep inside `tensor_grep.core`, `tensor_grep.cli`, or backend-specific packages) are subject to change without notice.

## 7. Explicitly unstable / experimental surface
The items documented in [docs/EXPERIMENTAL.md](EXPERIMENTAL.md) are not covered by the stability guarantees in this file.
- Hidden commands such as `tg worker`
- Opt-in runtime flags such as `TG_RESIDENT_AST`
- Temporary backend override environment variables used during migration or benchmarking

These surfaces may change, move, or be removed in minor releases.
