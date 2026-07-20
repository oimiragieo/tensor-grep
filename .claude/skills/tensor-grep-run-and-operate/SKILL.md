---
name: tensor-grep-run-and-operate
description: Use when running the `tg` CLI day-to-day — exact syntax for orient, search --rank, defs/refs/callers/blast-radius, agent, docs-coverage, context, session open/refresh/serve/daemon, checkpoint, scan --ruleset, run, mcp, doctor, dogfood, upgrade; the symbol-command 0/1/2 exit-code contract and what an agent should branch on; bounding a scan with `--deadline` and reading `partial`/`result_incomplete`/`deadline_limit` truncation flags; excluding vendor/skill trees with `--ignore` on orient/agent/docs-coverage; `tg context --max-tokens` budgets (default 16000, 0=opt-out); where JSON artifacts and cache state land on disk; starting the MCP server; or a whole-repo `tg search` that hangs. The OPERATOR runbook (how to invoke), not theory or audit workflow.
---

# tensor-grep run & operate

An imperative, copy-pasteable runbook for **running** `tg` (the tensor-grep CLI). Ground-truthed
against `src/tensor_grep/cli/main.py` at **released v1.78.1**, re-verified
**2026-07-16**. Every command below is a real `@app.command` in that file — re-verify with the
commands in [Provenance and maintenance](#provenance-and-maintenance) before trusting a flag on a
newer version. `main.py` churns ~100+ lines per release, so treat every `main.py:NNNN` cite as an
approximate anchor: `grep` the symbol, don't trust the raw line.

## Scope — and when to use a sibling instead

| You need | Use |
| --- | --- |
| **How to type the command / where does output go** | **this skill** |
| Search theory (BM25, trigram index, ripgrep internals, AST/tree-sitter concepts) | `code-search-and-retrieval-reference` |
| The blast-radius-before-editing audit workflow, `callers` truncation caveats | `tensor-grep-code-audit` |
| WHY the front door / registration / backend contract is shaped this way | `tensor-grep-architecture-contract` |
| Adding a `tg` command or search flag; gates before merging any tg change | `tensor-grep-change-control` |
| A live bug you just hit while running `tg` | `tensor-grep-debugging-playbook` |
| A hang/`--deadline` overrun on a large repo, or the unscoped-search-refusal campaign history | `tensor-grep-large-repo-scale-campaign` |
| "Has this already been tried and lost?" before re-attempting a fix | `tensor-grep-failure-archaeology` |
| Which commands accept `--deadline`/`--ignore`/`--max-tokens` (NOT the same set); env-var/flag axis list | `tensor-grep-config-and-flags` |
| Building from source, Rust/Python toolchain setup | `tensor-grep-build-and-env` |
| **Interpreting** `doctor`/`dogfood` fields or a `result_incomplete`/`partial`/`deadline_limit`/exit-`2` payload — what it PROVES | `tensor-grep-diagnostics-and-tooling` |
| Running the pytest/ruff/mypy gates; the exit-code-contract test pattern (`tests/unit/test_cli_deadline_flag.py`) | `tensor-grep-validation-and-qa` |
| Claiming/reading a speed number | `tensor-grep-benchmark-and-proof-toolkit` |
| Release mechanics, publish gates, external positioning claims | `tensor-grep-release-and-positioning` |

If you are about to *change* `tg` code rather than *run* it, stop and load
`tensor-grep-change-control` first — this skill assumes the CLI surface as shipped.

## 0. Confirm you are running the real thing first

Every command below assumes a working, correctly-resolved `tg` on PATH. Windows in particular can
silently resolve a stale or foreign `tg` (see `.claude/skills/tensor-grep/SKILL.md` "Start Here").
Before trusting any output:

```powershell
tg --version
tg doctor --json
```

`tg doctor --json` (§8) is the single first check for launcher drift, version mismatch, and
Windows shell escaping pitfalls — run it, not `tg --version` alone, when something looks wrong.

## 1. Command-anatomy quick table (the moat commands)

All symbol/orientation commands are **path-first**: `tg <command> PATH [SYMBOL|QUERY]`. `PATH`
defaults to `.` (current directory) everywhere below unless noted. Add `--json` to any of them for
machine-readable output (`--json` is a real per-command flag, not a global one — pass it after the
command name).

| Command | Positional form | Purpose |
| --- | --- | --- |
| `tg orient PATH` | `PATH` | One-call codebase orientation: central files by import in-degree, entry points, symbol map, AST snippets |
| `tg search PATTERN PATH --rank` | `PATTERN PATH` | Text search with a real hit re-ranked by BM25 relevance instead of grep order |
| `tg find PATH "query"` | `PATH QUERY` | Whole-repo natural-language hybrid search (BM25 [+ CPU dense [+ MaxSim]] -> RRF -> budget-fitted `file:line`); v1.77.0, #189 |
| `tg defs PATH SYMBOL` | `PATH SYMBOL` | Exact definition locations |
| `tg source PATH SYMBOL` | `PATH SYMBOL` | Full source block for a symbol |
| `tg refs PATH SYMBOL` | `PATH SYMBOL` | References to a symbol |
| `tg callers PATH SYMBOL` | `PATH SYMBOL` | Call sites + likely impacted tests |
| `tg blast-radius PATH SYMBOL` | `PATH SYMBOL` | Callers + transitive file/test impact |
| `tg imports FILE` | `FILE` | Forward file-dependency edges (#74; O(1), no repo scan) |
| `tg importers FILE [ROOT]` | `FILE [ROOT]` | Reverse file-dependency edges (bounded scan; `--deadline` on large roots) |
| `tg evidence emit PATH` | `PATH` + `--capsule`/`--manifest` | Aggregate prior outputs into an EvidenceReceipt |
| `tg codemap PATH` | `PATH` | Browsable folder→file→symbol map (`--out`; slow — prefer `/tmp`) |
| `tg agent PATH "query"` | `PATH QUERY` | Actionable Context Capsule — prefer `PATH/src` for speed |
| `tg session open PATH` | `PATH` | Create a cached repo-map session (returns `session_id`) |
| `tg scan --ruleset NAME` | (flag-driven) | Run a built-in security/compliance AST rule pack |
| `tg run PATTERN PATH` | `PATTERN [PATH]` | Bounded AST structural search / guarded rewrite |
| `tg mcp` | — | Start the MCP stdio server |
| `tg doctor` | `PATH` | System/GPU/cache/AST/daemon/shell diagnostics |
| `tg route-test PATH "query"` | `PATH QUERY` | Diagnose routing agreement between `context-render` and `edit-plan` for one query -- reports `agreement`/`warnings` |
| `tg dogfood` | (flag-driven) | Wraps `agent_readiness.py` into one verdict + JSON |
| `tg upgrade` | — | Upgrade the installed `tensor-grep` package |
| `tg calibrate` | -- (no positional; delegates to the native binary) | Measure CPU-vs-GPU crossover thresholds; exit 1 with a remediation pointer if no CUDA-enabled native binary is installed (#596). See `docs/gpu_crossover.md`. |
| `tg devices [--json] [--format text\|json]` | (flag-driven) | Print routable GPU device IDs + VRAM inventory (`collect_device_inventory`); the CLI counterpart of the `tg_devices` MCP tool (S7). See `docs/gpu_crossover.md`. |

## 2. Orientation and content search

```powershell
tg orient C:\repo --json
tg orient C:\repo --max-tokens 6000 --max-central-files 15          # widen the capsule
tg orient C:\repo --ignore "vendor/**" --ignore "core/skills/**"    # drop vendor/skill trees from ranking
```
`orient` (`main.py:6922`) takes `path` (default `.`), `--max-tokens` (default 3000, `orient`'s
snippet-token budget — **not** the same axis as `context --max-tokens`, §14), `--max-central-files`
(default 10), `--ignore` (repeatable glob), `--json`.

`--ignore GLOB` (repeatable) excludes a subtree from the **centrality ranking** so vendor/skill CODE
trees do not outrank real hubs on a harness or monorepo. The glob matches the file basename **or** the
repo-relative path (`--ignore 'seo/**' --ignore 'core/skills/**'`). Receipt: shipped for `orient` in
`#392` after a ~1900-file TS repo dogfood showed a central-skills tree crowding out the real code
hubs; the twin flag on `tg agent` (`#397`) is below. This is a *ranking* exclusion, not a scan
exclusion — the files are still walked, just kept out of the "central files" / "primary target" list.
(Note: `tg search --ignore` is a **different**, boolean flag — "respect ignore files" — not this glob;
`tg docs-coverage --ignore` (§13) is a coverage exclusion. See `tensor-grep-config-and-flags`.)

`orient`'s JSON also carries a **`suggested_ignore`** field (`orient_capsule.py:872`,
`_suggested_ignore_from_deweighted_trees`) -- ready-to-paste `--ignore` globs for whatever
auto-de-weighted vendor/skill trees it found (de-weight, never hard-exclude, by default). **v1.75.0
(#593, "M1+M2") broadened this from narrow nested-manifest islands to whole vendor/skill trees**: a
new STRONG-0 promotion fires on 5 unambiguous vendor-dir basenames alone (`node_modules`, `vendor`,
`third_party`, `_vendored`, `external_repos` -- no manifest needed), and a new STRONG-3 shape
heuristic detects a whole `skills/`-named tree whose children look like independent leaf skills with
no imports crossing out of the tree (a genuine imported `skills/` package stays un-deweighted). M2 in
the same PR also added `suggested_ignore` parity to **`tg agent --json`**, which previously never
surfaced it at all even though `tg agent` runs the identical de-weight during ranking -- additive-only,
mirroring `suggested_scope`'s convention (present only when non-empty).

```powershell
tg search "invoice tax" C:\repo --rank --json
```
`--rank` (alias `--bm25`, `main.py:5839`) re-ranks ripgrep hits by BM25 lexical relevance —
pure CPU, no API key, no model download. Default `--format` for plain search is `rg` (exact
ripgrep-style text); use `tg search PATTERN PATH --format rg --json` for ripgrep JSON Lines, or
`--json` alone for tensor-grep's own aggregate JSON object, or `--ndjson` for tensor-grep's
flattened streaming rows. These three JSON shapes are **not interchangeable** — `--json` is NOT
`rg`'s JSON Lines schema (`--json`/`--rank`/`--ndjson`/`--format` at `main.py:5831-5897`).

```powershell
tg agent C:\repo "change invoice tax rounding" --json
```
`agent` (`main.py:7276`) is opt-in and takes `path` then positional `query` (not `--query`, which
is a hidden deprecated alias, `main.py:7279-7284`). Key flags: `--max-files` (3), `--max-sources`
(5), `--max-tokens` (1200), `--max-repo-files` (512), `--provider native|lsp|hybrid`,
`--gpu-device-ids` (opt-in native GPU evidence only — sidecar-routed GPU is reported unsupported),
and `--ignore GLOB` (repeatable, `main.py:7324`) — the same vendor/skill-tree ranking exclusion as
`orient` above, here keeping a vendor/skill tree from being picked as the capsule's **primary target**
on a harness repo (`#397`). Before editing from a capsule, check top-level `ambiguity.status`:
`"tie_requires_confirmation"` is a hard stop for autonomous edits.

## 3. Symbol navigation (`defs` / `source` / `refs` / `callers` / `blast-radius`)

```powershell
tg defs C:\repo open_file --json
tg source C:\repo open_file --json
tg refs C:\repo open_file --json
tg callers C:\repo open_file --json
tg blast-radius C:\repo open_file --json
```

All five share the same positional contract: `path` then `symbol_arg`, both optional Typer
arguments (`defs` `main.py:7852` … `blast-radius` `main.py:8251`). If you type them reversed
(`tg defs SYMBOL PATH`), the CLI auto-detects it — `path` that fails `Path(path).exists()` and a
present `symbol_arg` get swapped, with a warning on stderr — but **write path-first** to avoid the
extra hint round-trip (`_maybe_swap_reversed_positionals`, `main.py:7723`, called from
`_resolve_path_and_symbol`, `main.py:7790`). A bare `tg defs SYMBOL` (single arg) resolves against
the current directory.

A hidden `--symbol` / `--query` flag still works and prints a deprecation warning to stderr
(`main.py:7801`) — treat it as legacy, not the contract; the positional form is canonical.

Common flags: `--provider native|lsp|hybrid` (default `native`), `--max-repo-files` (512),
`--json`. `callers`/`refs`/`impact`/`blast-radius` also take `--deadline SECONDS` to wall-clock-bound
the scan (§12); `defs`/`source` do **not**. `blast-radius` additionally takes `--max-depth` (3),
`--max-callers` (25), `--max-files` (25) (in the `blast_radius` def, `main.py:8251`+). `defs`
additionally takes `--class TEXT` to disambiguate a common method name by its enclosing class
(`main.py:7872`).

**Truncation contract (read §11 before scripting an exit code):** when a `callers`/`refs`/`impact`/
`blast-radius` JSON payload carries `"result_incomplete": true` (a scan cap) or `"partial": true`
(a `--deadline` cutoff), the scan did **not** finish — treat the list as a floor, never as proof of
zero callers. The exit code encodes this too: an **empty** truncated result exits `2`, a found result
exits `0` even when flagged incomplete (§11). The full audit decision procedure (P2 = truncation,
P7 = "zero callers != dead code") lives in `tensor-grep-code-audit`; this skill covers how to invoke
the command and how to branch on its exit/flags.

## 4. Session lifecycle — open, refresh, serve, daemon

Sessions cache the repo-map so repeated context/edit-plan/blast-radius calls skip re-indexing.

```powershell
tg session open C:\repo --json                    # returns session_id; capture it
tg session list                                    # sessions for the current root (no ID)
tg session show SESSION_ID
tg session refresh SESSION_ID C:\repo              # after file changes
tg session context-render SESSION_ID C:\repo "query"
tg session edit-plan SESSION_ID C:\repo "query"
tg session blast-radius SESSION_ID C:\repo SYMBOL
tg session serve SESSION_ID C:\repo                # reads JSONL requests from stdin, --jsonl is default-on
tg session daemon start C:\repo --json             # start/reuse the warm localhost daemon
tg session daemon status C:\repo --json
tg session daemon stop C:\repo --json
```

`session open` (`main.py:8563`) takes `path` (default `.`) and `--max-repo-files` (default 512,
the agent-safe cap). `session refresh` (`main.py:8756`) and every `session <subcmd> SESSION_ID
[PATH]` command require `session_id` as the **first** positional argument — it is not implicit.
`session serve` (`main.py:9375`) additionally accepts `--refresh-on-stale` to refresh once and
retry a request when file changes are detected mid-stream; passing `--no-jsonl` errors (JSONL is
currently the only serve mode, `main.py:9393`). `session context-render` / `session context` accept
`--max-tokens` (default 16000, `0` = unbounded) — see §14.

`session daemon start/status/stop` (`session_daemon_app`, `main.py:214`) each take only
`PATH` — there is no CLI flag for the daemon's idle/uptime limits; those are environment-only:
`TG_SESSION_DAEMON_IDLE_SECONDS` and `TG_SESSION_DAEMON_MAX_UPTIME_SECONDS`. `daemon start` prints
`host:port` and `pid`; `daemon status`/`stop` report whether a daemon is currently `running` for
that root. `tg session list` and `tg session daemon status` will discover nearby session scopes
when the current directory has no direct session metadata of its own.

## 5. Checkpoints (rewind before a risky rewrite)

```powershell
tg checkpoint create C:\repo --json
tg checkpoint list C:\repo --json                  # one detected scope
tg checkpoint list C:\repo --discover --json        # bounded child-scope discovery
tg checkpoint list C:\repo --discover-full --json   # exhaustive, can be slow on broad roots
tg checkpoint undo CHECKPOINT_ID C:\repo --json
tg checkpoint undo --last C:\repo --json            # restore the newest checkpoint in scope
```

`checkpoint undo` (`main.py:9582`) takes `checkpoint_id` as an optional positional, or `--last` to
restore the newest checkpoint for `path` without naming an ID — do not pass both
(`main.py:9601`/`9607`). If `checkpoint_id` resolves to an existing filesystem path, the error
message suggests `--last` explicitly (`main.py:9624`), which is a strong signal the two
positionals (`checkpoint_id`, `path`) got confused.

## 6. AST scan (built-in rule packs) and structural run/rewrite

```powershell
tg rulesets --json                                          # list built-in packs
tg scan --ruleset RULESET_NAME --path C:\repo\api --json    # narrowest useful root first
tg scan --config sgconfig.yml --json                        # custom ast-grep project config
tg scan --rule my-rule.yml --json                            # single custom rule, no sgconfig
```

`scan` (`main.py:9747`) accepts positional `PATHS`, or `--path` (default `.`) when using a
built-in ruleset — the two are mutually exclusive (`main.py:9873`), as are `--rule`,
`--ruleset`, and `--inline-rules` with each other (`main.py:9866`). Useful narrowing flags:
`--glob`/`-g`, `--type`/`-t`, `--max-depth`, `--filter`/`-f` (regex over loaded rule IDs). Baseline
workflow: `--baseline FILE` / `--write-baseline FILE` compare or snapshot matched-finding
fingerprints; `--suppressions FILE` / `--write-suppressions FILE` mark or record accepted findings
(writing suppressions requires `--justification TEXT`). `--allow-broad-generated-scan` opts into an
otherwise-refused scan of a generated/cache/dependency/multi-project root — prefer scoping first.

`--ruleset` also accepts a handful of RESOLVE-ONLY 1:1 mental-model aliases on top of the 6
canonical pack names (`rule_packs.py`'s `_RULE_PACK_ALIASES`): `auth`->`auth-safe`,
`secrets`->`secrets-basic`, `crypto`->`crypto-safe`, `tls`/`ssl`->`tls-safe`,
`subprocess`->`subprocess-safe`, `deserialize`/`deserialization`->`deserialization-safe`. A real
pack name always wins over an alias; aliases never appear in `tg rulesets`/`list_rule_packs()`.
`security` names the shared category all 6 packs belong to, not one pack, so it raises an
actionable error listing the 6 packs instead of guessing.

```powershell
tg run "function_definition" C:\repo\src --lang python --json
tg run --pattern 'def $NAME($$$ARGS): $$$BODY' --rewrite 'def $NAME($$$ARGS) -> None: $$$BODY' C:\repo --apply --verify
```

`run` (`main.py:11701`) takes the AST pattern positionally (or via `--pattern`/`-p`) and an
optional `PATH`; supplying only a path that exists with no pattern is a hard error
(`main.py:11799`, `typer.Exit(2)`), not a silent zero-match. `--rewrite`/`-r` sets the replacement,
`--apply` writes it, `--verify` runs tests after applying, `--checkpoint` wraps the apply in a
checkpoint, `-U`/`--update-all` is an ast-grep-compatible alias for apply-all (requires
`--rewrite`). Read-only structural-search extras: `--selector`, `--strictness`, `--stdin`,
`--globs` (repeatable, prefix `!` to exclude), `--filter` (text regex over matched nodes),
`--files-with-matches`. PowerShell users must single-quote patterns containing `$` captures (e.g.
`'def $NAME($$$ARGS): $$$BODY'`) or PowerShell expands `$NAME` before `tg` sees it.

KEY FACT: `tg run` already IS ast-grep when the `sg` binary is on PATH -- `AstGrepWrapperBackend`
delegates the pattern to `sg run -p <pattern>` verbatim (`ast_wrapper_backend.py:146`), so
`$NAME`/`$$$ARGS`/`--selector`/`--strictness` are already 100% ast-grep-compatible with no
translation layer. When `sg` is absent, a native-shaped pattern (no `$`) still runs through tg's
own tree-sitter `AstBackend`, but that backend speaks a DIFFERENT query DSL than ast-grep
(task #141) -- a `$`-metavariable pattern is never silently rerouted there (that would silently
mistranslate and return wrong matches, worse than an honest empty result). Instead
`_select_ast_backend_for_pattern` raises a fail-closed `ConfigurationError`, which `run_command`
now catches and reports as a clean `Error: ...` message + exit `2` (mirroring the Task #166
`ConfigurationError` handling in `main.py`'s search path), never a raw Python traceback. A
zero-match `tg run` (exit `1`, not an error) additionally emits static/heuristic remediation via
`_emit_ast_run_remediation` -- idiom shapes (`def $NAME($$$ARGS): $$$BODY`,
`function $NAME($$$) { $$$ }`), a `tg ast-info` pointer, and cheap "no `$`" / "no `--lang`" hints
-- on stderr for text modes and as an additive `"remediation"` `--json` key; this is `tg run`-only
and never fires on `tg scan` (a 0-finding scan is a clean pass, exit `0`).

## 7. MCP server

```powershell
tg mcp
```

Starts a **stdio** MCP server (`FastMCP("tensor-grep")`, `mcp_server.py:64`, `anyio.run` over
`_run_mcp_stdio_async`, `mcp_server.py:3881-3893`) — it is meant to be launched by an MCP client
(Claude Desktop, an agent harness), not run interactively and left open in a terminal.

Call `tg_mcp_capabilities` **first** in any new client/sandbox — it reports which tools work
without a standalone native `tg` binary versus which require one (`mcp_server.py:1433`).

Representative tool names (**48** as of v1.78.1 — `grep -n "^def tg_\|^async def tg_" mcp_server.py
| wc -l`; re-run this before trusting the count on a later version, see Provenance below):
`tg_search`, `tg_find` (whole-repo hybrid NL search, agent-callable form of `tg find`, v1.78.0/#189/#627 —
see `docs/harness_api.md`), `tg_ast_search`, `tg_symbol_defs`, `tg_symbol_source`,
`tg_symbol_refs`, `tg_symbol_callers`, `tg_symbol_impact`, `tg_symbol_blast_radius`,
`tg_symbol_blast_radius_render`, `tg_symbol_blast_radius_plan`, `tg_context_pack`,
`tg_context_render`, `tg_edit_plan`, `tg_agent_capsule`, `tg_ruleset_scan`, `tg_rewrite_plan`,
`tg_rewrite_apply`, `tg_rewrite_diff`, `tg_classify_logs`, `tg_devices`, `tg_index_search`, `tg_repo_map`,
`tg_file_imports`, `tg_file_importers`, `tg_session_file_importers`,
`tg_checkpoint_create` / `_list` / `_undo`, `tg_session_open` / `_list` / `_show` / `_refresh` /
`_edit_plan` / `_context_render` / `_blast_radius*`, `tg_audit_manifest_verify`,
`tg_audit_history`, `tg_audit_diff`, `tg_review_bundle_create` / `_verify`.

`tg_rewrite_apply` refuses free-form `lint_cmd`/`test_cmd` (they shell-execute on the host) unless
the operator opts in with `TG_MCP_ALLOW_VALIDATION_COMMANDS=1`, returning
`code="unsupported_option"` otherwise. The primary native `rg` passthrough now inserts a `--`
end-of-options sentinel before user/LLM-controlled paths (CWE-88 / the MCP-276 CVE class) — shipped
and unit-tested in `#370` (`rust_core/src/rg_passthrough.rs:397-398`). It is **not** solved
forever: a second native rg-invocation path is the open follow-up (`#49`), so any **new** MCP tool
or argv builder that shells out to `tg`/`rg` must still insert `--` before positionals itself. See
`AGENTS.md` "Security Hardening Patterns" and `tensor-grep-debugging-playbook` /
`tensor-grep-change-control` before adding a new MCP tool that shells out.

## 8. Diagnostics and operational health — doctor, dogfood, upgrade, repair-launcher

```powershell
tg doctor --json                       # full diagnostics, LSP included by default
tg doctor --no-lsp --json              # skip external LSP provider probes
tg doctor C:\repo --config sgconfig.yml --json
```
`doctor` (`main.py:10440`) takes `path` (default `.`), `--config` (default `sgconfig.yml`),
`--with-lsp/--no-lsp` (default **on**), `--json`. Inspect `path_tg_first_launcher_kind`,
`fresh_shell_path_tg_first_launcher_kind`, `python_subprocess_path_tg_first_launcher_kind`,
`shell_escaping_guidance`, and any `*_is_foreign` field before trusting a Windows timing or
routing claim — see `.claude/skills/tensor-grep/SKILL.md` "Start Here" for the full field list and
`tensor-grep-diagnostics-and-tooling` for interpreting them in depth.

```powershell
tg dogfood --output artifacts/dogfood_readiness.json
tg dogfood --json --root C:\repo --timeout-s 170
```
`dogfood` (`main.py:10188`) runs the agent-readiness gate and prints a one-page verdict; it "writes
only explicit `--output` and a sibling readiness report" next to it — it does not write anywhere by
default with no `--output` given (docstring at `main.py:10188`+). Flags:
`--root` (default `.`), `--output PATH`, `--expected-version` (defaults to `pyproject.toml`),
`--json`, `--progress auto|always|never` (stderr only), `--progress-interval-s` (30.0),
`--timeout-s` (170.0, the nested `agent_readiness.py` child budget), `--no-shell-probes`,
`--no-wsl-probe`. A non-zero exit means at least one readiness check failed — read
`failed_checks` in the verdict before treating a release or a change as safe.

```powershell
tg upgrade
```
`upgrade` (`main.py:10475`) upgrades the installed `tensor-grep` package to the latest PyPI
release. It tries, in order: `uv tool install --force` first **only** when the running Python is a
`uv tool`-managed venv (`_is_uv_tool_managed_python`, `main.py:10466`, detects `.../uv/tools/`
in `sys.executable`), then `uv pip install --upgrade --refresh-package tensor-grep`, then `pip
install --upgrade --no-cache-dir`. This is the source-aware upgrade path shipped to fix a WSL
uv-tool install getting stranded at a stale version — see `tensor-grep-failure-archaeology` for
the incident. `tg upgrade` also verifies the sidecar import/version post-upgrade and schedules a
managed native front-door refresh when the sidecar version moved ahead of the native binary.

```powershell
tg repair-launcher --json
tg repair-launcher --allow-foreign-rename --json     # only for a foreign tg.exe you own
```
`repair-launcher` (`main.py:10405`, Windows-relevant) removes a verified or self-identifying stale
`tensor-grep` Python `Scripts\tg.exe` launcher that shadows the managed native front door on PATH.
`--allow-foreign-rename` additionally moves aside a **foreign** (non-tensor-grep) `tg.exe` — use it
only when you own that binary.

## 9. Artifact conventions — where state and JSON reports actually land

| Location | What lives there | Created by | Tracked in git? |
| --- | --- | --- | --- |
| `.tensor-grep/sessions/` | `index.json` + per-session repo-map payloads | `tg session open` | No — `/.tensor-grep/` is gitignored (`.gitignore:51`) |
| `.tensor-grep/checkpoints/` | `index.json`, per-checkpoint `metadata.json` + `snapshot/` tree | `tg checkpoint create` | No — same `.tensor-grep/` ignore rule |
| `.tg_semantic_index/` (or `$TG_SEMANTIC_INDEX_DIR`) | Experimental semantic (dense) index shards | opt-in semantic-search paths | Not committed; experimental subsystem |
| `artifacts/` | `--output` JSON from `tg dogfood`, `scripts/agent_readiness.py`, `benchmarks/run_*.py` | explicit `--output PATH` only — nothing is written here by default | No — `artifacts/` in `.gitignore:61` is gitignored |

None of these directories are portable outputs to hand to another agent/process by default —
treat them as local cache/scratch. If you need a durable artifact, pass an explicit `--output` and
copy it somewhere tracked (or attach it to a PR) yourself; `tg dogfood`/benchmark scripts will not
do that for you.

`sgconfig.yml` (used by `tg scan --config`) is a project-level ast-grep config file, not a
tensor-grep cache directory — it lives wherever you point `--config`, typically the repo root.

## 10. Unscoped `tg search` — fail-fast/refuse, not a hang (shipped: #400/v1.40.3, #413/v1.42.0)

`tg search PATTERN` with **no path** argument (or `--glob X -l` without a scoped path) used to walk
the whole tree and could burn the full ripgrep-subprocess timeout before returning. That is now
**shipped, released** fail-fast/refuse behavior, not an open hang — three layered guards catch the
unscoped case before it reaches a slow walk, plus a wall-clock backstop if all three miss:

1. **Vendored-root refusal** (`_should_refuse_unbounded_vendored_root_scan`, `main.py:3925`) — a
   root with a top-level `node_modules`/`vendor`/`external_repos`/`third_party` dir **exits 2
   instantly** (no scan at all) unless `--allow-broad-generated-scan` opts in.
2. **Workspace-root refusal** (`_should_refuse_unbounded_workspace_root_scan`, `main.py:3869`) — a
   root with >=3 sibling project directories (a monorepo/workspace parent) is refused the same way.
3. **Large single-project-root refusal** (`_should_refuse_unbounded_large_root_scan`, `main.py:4022`,
   `#413`, dogfood v1.42.0) — closes the remaining gap: a large but non-vendored, non-workspace
   single-project root (matches neither guard above) refuses instantly via a **bounded scandir
   probe** — it checks the already-collected candidate-file count against a 1500-file ceiling
   (gated identically on `--allow-broad-generated-scan`/glob-type-depth scope) rather than falling
   through to the slow per-file Python match loop.
4. **Native-walk wall-clock deadline** (`compute_native_walk_deadline` /
   `native_walk_deadline_exceeded`, `src/tensor_grep/backends/cpu_backend.py:22-37`, checked in the
   walk around `main.py:6727-6734`) — the last-resort backstop: if none of the three refusals above
   fire, the native per-file search walk still self-bounds and **breaks to a flagged partial**
   (`result_incomplete` + a stderr warning) instead of running unbounded.

`TG_RG_TIMEOUT_SECONDS` (default **60.0 seconds**, `subprocess_policy.py:44`, lowered from 600s in
#288) remains the ripgrep-subprocess-level backstop for the plain rg-passthrough path, but the four
guards above mean an unscoped *vendored* or *large* root now fails in well under a second — you
should rarely see the 60s subprocess timeout actually fire on a repo shaped like this one anymore.

**Best practice — still scope to a path** (cheaper than even the refusal-probe cost, and the only
way to get real results instead of an instant refusal):

```powershell
tg search "pattern" C:\repo             # ~0.4s
tg search "pattern" --glob "*.py" C:\repo
# Avoid: tg search "pattern" --glob "*.py" -l     (no path -- triggers a refusal or a bounded walk)
```

Same rule for file listing — prefer a scoped root over `tg search --files . --hidden --no-ignore`
across a large workspace; see `.claude/skills/tensor-grep/SKILL.md` for the broad-generated-scan
guardrail (`--allow-broad-generated-scan`). For the campaign history and what is genuinely still
open on this front (task `#52` end-to-end `--deadline` honesty on a *legitimately large, in-scope*
repo, and `#390`'s daemon-path deadline gap — neither is the unscoped-hang bug this section covers),
see `tensor-grep-large-repo-scale-campaign`.

## 11. Exit codes — the layered contract (READ THIS before scripting `tg`)

There is **no single 0/1/2 table** any more. Exit codes are layered by command family. The old
"1 = no match, 2 = error" table is materially **misleading for symbol commands** — those now use a
three-state agent contract where `2` means "incomplete", not "usage error".

### 11a. Symbol commands — `callers` / `refs` / `impact` / `blast-radius` / `defs` / `source`

A **three-state** contract (authoritative source: `docs/CONTRACTS.md:108`; implemented in
`_emit_symbol_command_result`, `main.py:7678`, and `blast-radius`'s own copy):

| Exit | Meaning | What an agent may conclude |
| :--: | --- | --- |
| **0** | **Complete result you can trust.** The scan covered the repo — not truncated. | Use the result as the full answer. |
| **1** | **Genuine not-found on a COMPLETE scan.** The symbol/result truly is absent. | Safe to treat as "absent". |
| **2** | **INCOMPLETE.** The scan was truncated (by `--deadline` → `partial:true`, or a `--max-repo-files` cap → `result_incomplete:true`), so the result — whether it found things or not — is NOT the full answer. | Do **not** treat the (possibly partial, possibly empty) list as complete. Read the JSON; retry with a larger `--deadline`/`--max-repo-files`, or a narrower `PATH`, for the full set. |

**The #398 → #399 → revert history (do not get this wrong):** `#398` (v1.40.0) shipped
exit-`2`-on-**any**-truncated result. `#399` (v1.40.2) briefly narrowed it to "exit `2` only when the
truncated result is ALSO empty" (a found-but-capped result exited `0`). A **unanimous design council
overturned #399** (2026-07-05): truncation **trumps** found, because a truncated caller-set silently
trusted as exhaustive is a wrong-blast-radius/refactor risk, and forking the symbol commands away from
`tg search`'s "exit 2 on `result_incomplete`" convention creates two contradictory contracts. The
"every big-repo query exits 2" friction is a **default-cap miscalibration** (512), to fix separately —
not a reason to fork the contract. The **restored, current** rule: any `partial`/`result_incomplete`
→ exit `2`, regardless of whether results were found:

```python
if payload.get("partial") or payload.get("result_incomplete"):
    raise typer.Exit(2)  # truncated (found OR empty) -> INCOMPLETE, never reads as complete
if not_found:
    raise typer.Exit(1)  # complete + empty -> real not-found
# complete + found -> exit 0
```

`blast-radius` follows the same rule with one extra distinction: an **output** cap
(`--max-callers`/`--max-files`, which trims a *complete* analysis for display) stays exit `0` and is
flagged `callers_truncated`/`files_truncated`; only a **scan** cap (`partial` or
`scan_limit.possibly_truncated`) exits `2`.

### 11b. Search family — `tg search` / `tg run`

Mirrors ripgrep's convention: **0** = match, **1** = clean no-match, **2** = usage/argument error
(`typer.Exit(code=2)`) or unhandled error. Do **not** treat exit `1` from `tg search` as a failure
in a script — check the output/JSON. Scan truncation surfaces as `result_incomplete` **in the JSON
payload** (this is the "`2 = result_incomplete` convention" `docs/CONTRACTS.md:108` says the symbol
contract mirrors), not via a special exit code on a found search.

### 11c. Other commands

`tg docs-coverage --check` exits **1** on doc drift (uncovered files, or `--stale` references) — a CI
gate (§13). Plain command/usage/argument errors across the CLI exit **2** (`typer.Exit(code=2)`);
handled runtime errors exit **1**.

`tg find` (v1.77.0, #189) has its own hybrid contract, closer to the symbol-command shape than to
plain `tg search` (`main.py:4340-4440`): a `BackendExecutionError` (e.g. a corrupt dense model) is
caught at the command boundary and exits **2** (JSON error envelope with `code="find_backend_error"`
under `--json`, else a `tg: ...` stderr line) — never a raw traceback. An empty result exits **2** if
`result_incomplete` else **1**. A **found** result that is ALSO `result_incomplete` (a
`--deadline`/`--max-repo-files`/internal chunk-cap truncation) prints the ranked partial results
**then** exits **2** — truncation trumps found, same rule as §11a's symbol commands, not §11b's plain
search-family convention.

### 11d. What an agent/script should branch on

```powershell
$json = tg callers C:\repo QueryEngine --deadline 8 --json
$rc = $LASTEXITCODE
switch ($rc) {
  0 { <# trust it; if $json has result_incomplete/partial, it is a FLOOR -- raise the budget for MORE #> }
  1 { <# genuine not-found on a COMPLETE scan -> safe to treat the symbol as absent #> }
  2 { <# INCOMPLETE + EMPTY -> do NOT conclude "absent"; retry: bigger --deadline / --max-repo-files, or narrower PATH #> }
}
```

Rule of thumb: **branch on the exit code first, then parse the JSON for `result_incomplete` /
`partial` / `deadline_limit` when completeness actually matters** (e.g. before deciding a symbol has
no callers). Interpreting those fields in depth is `tensor-grep-diagnostics-and-tooling`'s territory;
the exit-code contract test pattern lives in `tensor-grep-validation-and-qa`
(`tests/unit/test_cli_deadline_flag.py`).

## 12. Bounding a scan with `--deadline`

`--deadline SECONDS` (float, `min=0.1`) wall-clock-bounds the underlying repo scan and returns
whatever was found so far instead of running unbounded. It is on **these commands only** (verify the
set with `grep -n '"--deadline"' src/tensor_grep/cli/main.py` — `tensor-grep-config-and-flags` owns
the authoritative list):

| Command | `--deadline` line | Notes |
| --- | --- | --- |
| `tg callers` | `main.py:8150` | bounds the caller-scan traversal (`#393`) |
| `tg refs` | `main.py:8086` | bounds the reference-file scan |
| `tg impact` | `main.py:7990` | bounds both the impact pass and its caller sub-pass |
| `tg blast-radius` | `main.py:8289` | bounds the graph traversal |
| `tg inventory` | `main.py:6819` | bounds the single-pass walk |

`defs`/`source`/`orient`/`agent`/`context`/`docs-coverage` do **not** take `--deadline` — they bound
by token/file caps (`--max-tokens`, `--max-repo-files`) instead.

```powershell
tg callers C:\big-repo QueryEngine --deadline 8 --json
tg inventory C:\big-repo --deadline 5 --json
```

**What a partial result looks like in `--json`** (the truncation is always flagged; it is never a
silent short answer — `#394` stamps the honesty flags at payload-assembly time so MCP/`*_json`
consumers see them too, via `_mark_result_incomplete`, `repo_map.py`):

- **Graph commands** (`callers`/`refs`/`impact`/`blast-radius`) on a deadline cutoff add
  `"partial": true` (the one field an agent's parser must check) and a `"deadline_limit"` object
  whose stable key is `"deadline_exceeded": true`; its counter keys are **named per command** —
  `caller_files_scanned`/`caller_files_total` for `callers`, `reference_files_*` for `refs`,
  `files_*` for the context/parse path — telling you how far it got. `callers`/`blast-radius` also
  downgrade `"graph_completeness": "partial"`, and the payload additionally carries
  `"result_incomplete": true` + `"scan_remediation"` (a human-readable "raise the budget" hint).
- **`tg inventory`** on a deadline cutoff labels `scan_limit.truncation_cause = "deadline"`; the
  file/byte/language counts are a **floor**, not a total.

**Exit interaction (§11):** any deadline/cap-truncated symbol result exits `2` — **whether or not it
found something** (council-verified B, 2026-07-05; the found→`0` narrowing in #399 was reverted). A
script keying on the exit code will correctly treat a truncated result as incomplete; parse
`partial`/`result_incomplete` in the JSON to decide whether to raise the budget or narrow the `PATH`.

> **OPEN caveat (do not oversell `--deadline`).** Each stage honors the deadline **in isolation**,
> but the pipeline end-to-end is **not** reliably bounded on a very large repo yet (task `#52`,
> receipt 2026-07-05: `tg callers QueryEngine --deadline 10` took ~25s on a 1884-file TS repo because
> the caller-scan re-parses ~1941 files through the slow regex TS parser; `#396` added a re-parse +
> `Path.resolve()` cache for a 7.9x win on central symbols but did not fully close it). Separately,
> **daemon-served** graph queries (`tg session … --daemon`, run against the cached session repo-map)
> are **not** bounded by the scan deadline at all (`#390`). Treat `--deadline` as a best-effort
> upper-ish bound, not a hard SLA, until those close.

## 13. `tg docs-coverage` — find source files no governing doc references

New command (postdates most of the skill library; `#358`, v1.21.0). It lists source files that **no
governing doc** (`CLAUDE.md` / `README*` / `AGENTS.md`) references — by **path or basename**. It is
deliberately **reference-existence only** (does a doc mention the file at all?), which is far cheaper
and less noisy than the deferred semantic `diff-docs` (`#38`, deferred after a real-corpus dogfood
produced 20,060 findings / 2,727 false "high" — reference-existence avoids that trap).

`docs_coverage` (`main.py:6847`) takes `path` (default `.`) plus:

| Flag | Effect |
| --- | --- |
| `--max-repo-files` | walk cap (default 50000) |
| `--ignore GLOB` (repeatable, `#366`) | exclude a stub/vendor group so it stops dragging `coverage_pct` (matches basename or repo-relative path) |
| `--json` | machine-readable payload |
| `--fix` (`#365`) | emit a paste-ready Markdown table (path / size / first line) of undocumented files |
| `--stale` (`#367`) | inverse mode: report governing-doc references to files that **no longer exist** (with a fictional-path guard) |
| `--check` (`#368`) | exit **1** on drift (uncovered files, or with `--stale` any stale reference) — the CI doc-drift gate; respects `--ignore` |

**Worked example** — text output then a CI gate:

```powershell
tg docs-coverage C:\repo\src
# Docs coverage for C:\repo\src
# source_files=214  covered=190  uncovered=24  coverage=88.8%  docs=3
# Undocumented source files (24):
#   cli/docs_coverage.py
#   ...

tg docs-coverage C:\repo\src --ignore "**/_generated/*" --json   # drop generated stubs from the count
```

CI doc-drift gate (fails the job when any source file is undocumented):

```yaml
# .github/workflows/ci.yml — doc-drift gate
- name: docs coverage
  run: tg docs-coverage src --ignore "**/migrations/*" --check
```

The `--json` payload carries `totals.{source_files, covered, uncovered, coverage_pct, doc_files}`,
`uncovered_files[]`, `applied_ignore[]`, and `scan_limit.{max_files, possibly_truncated,
truncation_cause}`.

> **Trap fixed in `#371` (round-6 HIGH):** the excluded-dir check once matched **absolute** ancestor
> path parts, so a checkout living under a directory literally named `build/`, `venv/`, or `target/`
> excluded *every* file → `source_files=0` → `coverage_pct=100.0`, a silent false-green. It now matches
> the **repo-relative** path. If an old `tg` reports 100% coverage with 0 source files, suspect this,
> not a clean repo. The doc-authoring side of `docs-coverage` (which doc owns which contract) lives in
> `tensor-grep-docs-and-writing`; the flag catalog in `tensor-grep-config-and-flags`.

## 14. Context budgets — `tg context --max-tokens` (and its mirrors)

`tg context PATH "query" --max-tokens N` returns a ranked context pack for edit planning, **bounded
by default** so it is safe to inject into a prompt. Default **16000**, `min=0`, and **`0` = explicit
unbounded opt-out** (`main.py:6987`, mirrors `repo_map._DEFAULT_CONTEXT_MAX_TOKENS`, `repo_map.py:5944`).
The bound exists because an unbounded pack ballooned past 1MB (dogfood v1.19.9).

```powershell
tg context C:\repo "invoice tax rounding" --json                 # ~16k-token pack (default)
tg context C:\repo "invoice tax rounding" --max-tokens 0 --json  # unbounded (opt-out; can be huge)
```

The same 16000-default / `0`=opt-out budget is enforced on **every** context surface, so a large
pack cannot sneak in through a side door:

| Surface | Cap | Receipt |
| --- | --- | --- |
| `tg context` (standalone) | 16000, `0`=off | `main.py:6987` |
| `tg context-render` / `tg session context-render` / `tg session context` (incl. `--daemon`) | 16000, `0`=off | mirrored `#364`; daemon path capped `#373` (dogfood 1.27.0: `session context --daemon` was UNBOUNDED at ~557KB / 384 files) |
| MCP context tools (`tg_context_pack` / `tg_context_render`) | `_DEFAULT_MCP_CONTEXT_MAX_TOKENS = 16000`, `0`/`None`=off | `mcp_server.py:82`; added `#372` (round-6 HIGH) after `#359`'s CLI cap never reached the MCP surface |

**Do not conflate this axis with the other `--max-tokens` flags.** `orient --max-tokens` (default
**3000**) is a *snippet* budget for the orientation capsule; `agent --max-tokens` (default **1200**)
is the capsule snippet-token budget. Only `context`/`context-render`/`session context*`/MCP context
tools use the 16000 pack budget. What the budget *proves* (vs. what it just bounds) is
`tensor-grep-diagnostics-and-tooling`'s territory.

## 15. Common mistakes

| Mistake | Correction |
| --- | --- |
| Running `tg search PATTERN` with no path in a large repo | Always scope to a path (§10) — a vendored/large/workspace root now refuses in <1s (shipped), and the 60s ripgrep-subprocess timeout is only the last-resort backstop, not the primary behavior |
| Reading `tg search --json` as ripgrep JSON Lines | It is tensor-grep's own aggregate JSON object; use `--format rg --json` for rg's JSON Lines schema |
| Passing `SYMBOL PATH` (reversed) to `defs`/`refs`/`callers`/`blast-radius` | Auto-corrected with a stderr warning, but write `PATH SYMBOL` — the canonical, documented order |
| Reading a symbol-command **exit `2`** as "usage/argument error" | On `callers`/`refs`/`impact`/`blast-radius` it means **INCOMPLETE + EMPTY** (§11a) — retry with a bigger `--deadline`/`--max-repo-files` or narrower `PATH`, don't abort |
| Treating a symbol-command **exit `0`** as "the complete set" | If the JSON carries `result_incomplete`/`partial`, it is a **floor** — raise the budget for MORE (§11a, §12) |
| Treating `callers=0` / `result_incomplete: true` as "no callers" | Truncated ≠ dead; widen scope or raise the cap — see `tensor-grep-code-audit` and §11 |
| Trusting `--deadline` as a hard end-to-end wall-clock SLA on a huge repo | It bounds each stage in isolation; the pipeline (and the `--daemon` graph path) is **not** fully bounded yet (`#52`/`#390`, §12) |
| Assuming `tg search --ignore` and `tg orient/agent --ignore` are the same flag | `search --ignore` is a **boolean** "respect ignore files"; `orient`/`agent`/`docs-coverage --ignore` are **repeatable globs** (ranking / coverage exclusion) — §2, §13 |
| Calling `tg session refresh`/`edit-plan`/etc. without the `session_id` first arg | Every session subcommand except `open`/`list`/`daemon` requires `SESSION_ID` as arg 1 |
| Expecting `tg dogfood`/benchmark scripts to persist a report without `--output` | Nothing is written by default; pass `--output PATH` explicitly (§9) |
| Assuming `CliRunner`-style invocation proves a routing/flag fix works | Dogfood the real published binary — `CliRunner` bypasses the `bootstrap` front door entirely (`AGENTS.md` "Dogfood the Real Binary") |
| Running `tg upgrade` inside a `uv tool`-managed install expecting `pip`/`uv pip` to work | `tg upgrade` already detects this and uses `uv tool install --force` first; do not hand-roll a different upgrade command |
| Starting `tg mcp` in an interactive terminal expecting text prompts | It is a stdio server for an MCP client, not an interactive REPL |

## Provenance and maintenance

Facts here were re-verified **2026-07-16** against **released v1.78.1** by reading
`src/tensor_grep/cli/main.py`, `mcp_server.py`, `repo_map.py`, `docs_coverage.py`,
`subprocess_policy.py`, `cpu_backend.py`, and `docs/CONTRACTS.md` (`pyproject.toml` = `1.78.1`). The
`tg find` (§1, §7, §11c) and `tg route-test` (§1) additions were re-verified directly against
`main.py:4340-4440` and `main.py:9833-9925` in this same pass.
The unscoped-`tg search` hang fix (§10) is **shipped and released** (`#400` in v1.40.3, `#413` in
v1.42.0) — it is no longer an in-flight branch. `main.py` moves ~100+ lines per release, so re-grep
the symbol before trusting a cite:

```powershell
# Version currently installed / current tag
tg --version --verbose
grep -n "^version" pyproject.toml

# Full, current @app.command surface (compare against SS 1-8 above)
grep -n "@app.command\|@session_app.command\|@session_daemon_app.command\|@checkpoint_app.command\|@review_bundle_app.command" src/tensor_grep/cli/main.py

# Full, current MCP tool surface (compare against SS 7)
# NOTE: `grep -A1 "@mcp.tool" | grep "^def "` is BROKEN -- ripgrep/grep's `-A1` context lines are
# prefixed "NNNN-", not "NNNN:", so `^def ` never matches and this silently returns 0. Count the
# decorated function definitions directly instead (verified == the @mcp.tool decorator count, 48
# as of v1.78.1):
grep -n "^def tg_\|^async def tg_" src/tensor_grep/cli/mcp_server.py | wc -l

# Symbol-command exit contract (SS 11) -- narrative + implementation
grep -n "three-state agent contract\|Symbol-command exit codes" docs/CONTRACTS.md
grep -n "def _emit_symbol_command_result\|Exit(2)\|Exit(1)" src/tensor_grep/cli/main.py

# Which commands accept --deadline (SS 12) and the partial-payload flags
grep -n '"--deadline"' src/tensor_grep/cli/main.py
grep -n "_mark_result_incomplete\|\"deadline_limit\"\|\"partial\"\|truncation_cause" src/tensor_grep/cli/repo_map.py

# docs-coverage flags (SS 13) and context --max-tokens defaults (SS 14)
grep -n "def docs_coverage\|\"--fix\"\|\"--stale\"\|\"--check\"\|\"--ignore\"" src/tensor_grep/cli/main.py
grep -n "_DEFAULT_CONTEXT_MAX_TOKENS\|_DEFAULT_MCP_CONTEXT_MAX_TOKENS\|16000" src/tensor_grep/cli/main.py src/tensor_grep/cli/mcp_server.py src/tensor_grep/cli/repo_map.py

# --ignore on orient/agent (SS 2) -- repeatable ranking glob, NOT the search boolean
grep -n "def orient\|def agent" src/tensor_grep/cli/main.py

# TG_RG_TIMEOUT_SECONDS default + the 3 unscoped-search refusal guards + the native-walk deadline (SS 10)
grep -n "_configured_positive_float(\"TG_RG_TIMEOUT_SECONDS\"" src/tensor_grep/cli/subprocess_policy.py
grep -n "_should_refuse_unbounded_vendored_root_scan\|_should_refuse_unbounded_workspace_root_scan\|_should_refuse_unbounded_large_root_scan" src/tensor_grep/cli/main.py
grep -n "compute_native_walk_deadline\|native_walk_deadline_exceeded" src/tensor_grep/backends/cpu_backend.py

# Artifact directory names (compare against SS 9)
grep -n "_TG_DIRNAME\|_SESSIONS_SUBDIR\|_CHECKPOINTS_SUBDIR" src/tensor_grep/cli/session_store.py src/tensor_grep/cli/checkpoint_store.py
grep -n "^artifacts/\|^/\.tensor-grep/" .gitignore

# Registration-completeness gate (if a command/flag was added/removed since this was written)
grep -n "KNOWN_COMMANDS\|PUBLIC_TOP_LEVEL_COMMANDS" src/tensor_grep/cli/commands.py tests/e2e/test_routing_parity.py
```

Open uncertainties this skill does not resolve: the exact current MCP tool count drifts every
release (48 as of v1.78.1 — re-run the grep above, don't trust the stamped number); whether
`--symbol`/`--query` hidden flags have since been removed (still present and working, with a
deprecation warning, at v1.78.1 — re-grep `main.py` for the deprecation-warning call site, the line
number drifts); and the exact set of `SEARCH_PYTHON_PASSTHROUGH_FLAGS` / `_TG_ONLY_SEARCH_FLAGS`
(that pairing is `tensor-grep-config-and-flags`' territory, not re-enumerated here).
