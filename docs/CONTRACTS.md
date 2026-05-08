# API and Data Contracts

This document defines the backward-compatibility guarantees for data structures and CLI outputs used by enterprise integrations, IDEs, and editor-plane agents.

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
- `--json`
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
- `tg search --json` emits a valid aggregate JSON object even when there are zero matches.
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
- Stable managed install scripts should prefer the matching release-native CPU `tg` binary as the public front door when the GitHub release asset is available, set `TG_SIDECAR_PYTHON` for Python-backed commands, set `TG_NATIVE_TG_BINARY` for the sidecar, and fall back to `python -m tensor_grep` when no native asset exists. On Windows, the managed native front-door directory must be placed on User PATH ahead of compatibility shim directories so `cmd`, unprofiled PowerShell, and `subprocess.run(["tg", ...])` resolve `~/.tensor-grep/bin/tg.exe` before the slower argv-safe `tg.cmd` bridge.
- Stable installers must build replacement managed environments and front-door files in a staging directory and only swap them into `~/.tensor-grep` after package installation and front-door generation succeed. PowerShell installer native commands must check `$LASTEXITCODE` before the staged swap. A failed package resolve/install must preserve the previous managed install and shims.
- `tg upgrade` must not infer "latest PyPI version" solely from unchanged local metadata. It should refresh package metadata, skip yanked PyPI releases when selecting an exact version, pin the latest same-or-newer PyPI version when known, verify the target Python can import `tensor_grep`, and report a verification error instead of success if the sidecar is corrupted. The scheduled Windows self-upgrade helper must run the same import/version verification before writing a success log. Managed installs must also refresh the managed release-native front door to the verified sidecar package version, or schedule a Windows native-front-door retry helper when the running native `tg.exe` is locked.
- `tg classify --format json` is a public sidecar command even when invoked through the native front door. Default `classify` should use deterministic local heuristics without probing tokenizer/model providers. Operators must opt into the CyBERT/Triton provider with `TENSOR_GREP_CLASSIFY_PROVIDER=cybert`; provider failures still fall back before tokenization or Hugging Face model loading so agent calls fail fast and quietly.
- GPU benchmark correctness compares result sets, not only successful match exits. `rg` exit code `1` is a valid no-match outcome, so no-match patterns must be accepted as correctness passes when `tg` also returns no matches. The GPU scale gates should include 1GB and 5GB rows and exact match/file-set correctness for every >=1GB GPU corpus before any GPU promotion claim.
- The fast agent-readiness dogfood gate is `python scripts/agent_readiness.py --output artifacts/agent_readiness.json`. For the current `v1.8.30` release line it checks public shell version probes, `public-windows-launcher-quoted-patterns`, repo doctor sanity, `context_consistency`, deterministic rg parity edges, broad generated-root scan guardrails, AST smoke, MCP context-render smoke, docs claim hygiene, the managed native-upgrade contract, and the public positioning that `rg` remains the cold exact-text baseline while `ast-grep` remains the structural-search feature/performance baseline.

Context and edit-planning contracts:
- `context-render` and MCP context output must not let `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, rendered source sections, and follow-up reads contradict each other.
- `context_consistency` reports whether the primary file is included, whether rendered context matches the primary target, whether confidence was downgraded, and why a primary file was omitted from rendered budget.
- The default JSON/LLM render profile includes executable body lines for selected functions. Compact rendering can remove comments, docstrings, blank lines, type-only imports, and boilerplate, but it must preserve matched behavior unless the caller requests a summary-only profile in a future contract.
- Validation command hints use `validation_plan[].detection` with values of `detected`, `heuristic`, or `generic`. JavaScript package-manager commands require `package.json` evidence; Python commands require tests, project markers, or Python layout evidence; omit commands entirely when no runner evidence exists instead of inventing `npm test` or `uv run pytest`.
- `edit-plan` and `context-render` JSON both expose top-level `validation_commands` copied from `navigation_pack` or `edit_plan_seed` for quick agent access. Agents should not need command-specific parsing just to find the recommended validation command list.
- Repo-map and context-ranking defaults exclude generated/cache/dependency directories, binary files, logs, and hidden non-code files from normal code context.
- Future token-efficiency profiles must be opt-in and recoverable. Do not mutate raw `--format rg`, `--json`, or `--ndjson` contracts to save tokens; add explicit agent profiles with hard budgets, grouped excerpts, omission counts, and refetch commands.
- Compact output should cap breadth before cutting semantic payload. Selected functions must preserve matched lines and executable body slices, and omissions should be reported in metadata or clearly non-code delimiters instead of comment-shaped placeholders inside source blocks.
- Future `tg agent` or Actionable Context Capsule output must be a separate agent contract, not a changed search contract. A capsule should include primary file/function, route rationale, bounded source snippets with line maps, related call sites, validation evidence with provenance, risk, edit order, checkpoint or rollback metadata, omission counts, confidence, follow-up read commands, and an "ask user before editing" recommendation when uncertainty or risk is high.
- Capsule and search-intent routing evidence should label claims as `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, or `stale/uncertain`. If these signals disagree, confidence should be downgraded and the contradiction should be explicit instead of hidden behind a single ranked file.

Known current limitations:
- Explicitly opted-in broad generated-root walks can still be expensive. The Python path-list output path and managed Windows launchers force UTF-8, but scope file-list commands to the smallest useful root whenever possible.
- Broad generated roots remain agent-hostile when callers opt in to them. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in; `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- `impact --symbol` is a broader planning signal and can be noisier than `blast-radius`; use `blast-radius` for direct symbol impact checks.

## 4. Machine-readable CLI output (`--json` and `--ndjson`)
The JSON schemas emitted by `tensor-grep search --json`, `tensor-grep search --ndjson`, and the documented harness/editor-plane flows are considered public APIs.
- Existing fields (for example `file`, `line`, `match`, `context`) will not be renamed or removed without a major version bump.
- New fields may be added in minor versions.
- Consumers should ignore unrecognized JSON fields.

## 5. Operational diagnostics (`tg doctor --json`)
`tg doctor --json` is intended for operational automation and support workflows.
- Existing top-level sections remain additive-only within a major version.
- Individual diagnostic fields may grow as new probes are added.
- Consumers should treat missing optional fields as a valid state and ignore unknown fields.
- Implicit native-binary resolution must not select stale in-tree `rust_core/target/*/tg(.exe)` binaries. Stale in-tree candidates should be reported under `skipped_native_tg_binaries` with `rust_binary_version_status = stale-skipped`; `TG_NATIVE_TG_BINARY` is the explicit opt-in for using a specific standalone binary anyway.

## 6. Python Library API
Classes and functions exposed in `tensor_grep.api` are stable within a major version. Internal modules (prefixed with `_` or deep inside `tensor_grep.core`, `tensor_grep.cli`, or backend-specific packages) are subject to change without notice.

## 7. Explicitly unstable / experimental surface
The items documented in [docs/EXPERIMENTAL.md](EXPERIMENTAL.md) are not covered by the stability guarantees in this file.
- Hidden commands such as `tg worker`
- Opt-in runtime flags such as `TG_RESIDENT_AST`
- Temporary backend override environment variables used during migration or benchmarking

These surfaces may change, move, or be removed in minor releases.
