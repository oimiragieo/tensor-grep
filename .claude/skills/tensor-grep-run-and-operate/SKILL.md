---
name: tensor-grep-run-and-operate
description: Use when running the `tg` CLI day-to-day — exact syntax for orient, search --rank, defs/refs/callers/blast-radius, agent, session open/refresh/serve/daemon, checkpoint, scan --ruleset, run, mcp, doctor, dogfood, upgrade; where JSON artifacts and cache state land on disk; starting the MCP server; or a whole-repo `tg search` that hangs. The OPERATOR runbook (how to invoke), not theory or audit workflow.
---

# tensor-grep run & operate

An imperative, copy-pasteable runbook for **running** `tg` (the tensor-grep CLI). Ground-truthed
against `src/tensor_grep/cli/main.py` as of **2026-07-02, v1.17.25** (`pyproject.toml:322`). Every
command below is a real `@app.command` in that file — re-verify with the commands in
[Provenance and maintenance](#provenance-and-maintenance) before trusting a flag on a newer version.

## Scope — and when to use a sibling instead

| You need | Use |
| --- | --- |
| **How to type the command / where does output go** | **this skill** |
| Search theory (BM25, trigram index, ripgrep internals, AST/tree-sitter concepts) | `code-search-and-retrieval-reference` |
| The blast-radius-before-editing audit workflow, `callers` truncation caveats | `tensor-grep-code-audit` |
| WHY the front door / registration / backend contract is shaped this way | `tensor-grep-architecture-contract` |
| Adding a `tg` command or search flag; gates before merging any tg change | `tensor-grep-change-control` |
| A live bug you just hit while running `tg` | `tensor-grep-debugging-playbook` |
| "Has this already been tried and lost?" before re-attempting a fix | `tensor-grep-failure-archaeology` |
| Env-var/flag reference tables (the full axis list) | `tensor-grep-config-and-flags` |
| Building from source, Rust/Python toolchain setup | `tensor-grep-build-and-env` |
| Interpreting `doctor`/`dogfood` field-by-field, deep diagnostics | `tensor-grep-diagnostics-and-tooling` |
| Running the pytest/ruff/mypy validation gates | `tensor-grep-validation-and-qa` |
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
| `tg defs PATH SYMBOL` | `PATH SYMBOL` | Exact definition locations |
| `tg source PATH SYMBOL` | `PATH SYMBOL` | Full source block for a symbol |
| `tg refs PATH SYMBOL` | `PATH SYMBOL` | References to a symbol |
| `tg callers PATH SYMBOL` | `PATH SYMBOL` | Call sites + likely impacted tests |
| `tg blast-radius PATH SYMBOL` | `PATH SYMBOL` | Callers + transitive file/test impact |
| `tg agent PATH "query"` | `PATH QUERY` | Actionable Context Capsule: primary file/span, validation commands, edit order, confidence |
| `tg session open PATH` | `PATH` | Create a cached repo-map session (returns `session_id`) |
| `tg scan --ruleset NAME` | (flag-driven) | Run a built-in security/compliance AST rule pack |
| `tg run PATTERN PATH` | `PATTERN [PATH]` | Bounded AST structural search / guarded rewrite |
| `tg mcp` | — | Start the MCP stdio server |
| `tg doctor` | `PATH` | System/GPU/cache/AST/daemon/shell diagnostics |
| `tg dogfood` | (flag-driven) | Wraps `agent_readiness.py` into one verdict + JSON |
| `tg upgrade` | — | Upgrade the installed `tensor-grep` package |

## 2. Orientation and content search

```powershell
tg orient C:\repo --json
tg orient C:\repo --max-tokens 6000 --max-central-files 15   # widen the capsule
```
`orient` (`main.py:6607`) takes `path` (default `.`), `--max-tokens` (default 3000), `--max-central-files`
(default 10), `--json`.

```powershell
tg search "invoice tax" C:\repo --rank --json
```
`--rank` (alias `--bm25`, `main.py:5680-5687`) re-ranks ripgrep hits by BM25 lexical relevance —
pure CPU, no API key, no model download. Default `--format` for plain search is `rg` (exact
ripgrep-style text); use `tg search PATTERN PATH --format rg --json` for ripgrep JSON Lines, or
`--json` alone for tensor-grep's own aggregate JSON object, or `--ndjson` for tensor-grep's
flattened streaming rows. These three JSON shapes are **not interchangeable** — `--json` is NOT
`rg`'s JSON Lines schema (`main.py:5672-5696`).

```powershell
tg agent C:\repo "change invoice tax rounding" --json
```
`agent` (`main.py:6930`) is opt-in and takes `path` then positional `query` (not `--query`, which
is a hidden deprecated alias, `main.py:6932-6937`). Key flags: `--max-files` (3), `--max-sources`
(5), `--max-tokens` (1200), `--max-repo-files` (512), `--provider native|lsp|hybrid`,
`--gpu-device-ids` (opt-in native GPU evidence only — sidecar-routed GPU is reported unsupported).
Before editing from a capsule, check top-level `ambiguity.status`: `"tie_requires_confirmation"` is
a hard stop for autonomous edits.

## 3. Symbol navigation (`defs` / `source` / `refs` / `callers` / `blast-radius`)

```powershell
tg defs C:\repo open_file --json
tg source C:\repo open_file --json
tg refs C:\repo open_file --json
tg callers C:\repo open_file --json
tg blast-radius C:\repo open_file --json
```

All five share the same positional contract: `path` then `symbol_arg`, both optional Typer
arguments (`main.py:7484-7822`). If you type them reversed (`tg defs SYMBOL PATH`), the CLI
auto-detects it — `path` that fails `Path(path).exists()` and a present `symbol_arg` get swapped,
with a warning on stderr — but **write path-first** to avoid the extra hint round-trip
(`_maybe_swap_reversed_positionals`, `main.py:7355`, called from `_resolve_path_and_symbol`,
`main.py:7422`). A bare `tg defs SYMBOL` (single arg) resolves against the current directory.

A hidden `--symbol` / `--query` flag still works and prints a deprecation warning to stderr
(`main.py:7429-7440`) — treat it as legacy, not the contract; the positional form is canonical.

Common flags: `--provider native|lsp|hybrid` (default `native`), `--max-repo-files` (512),
`--json`. `blast-radius` additionally takes `--max-depth` (3), `--max-callers` (25), `--max-files`
(25) (`main.py:7797-7820`). `defs` additionally takes `--class TEXT` to disambiguate a common
method name by its enclosing class (`main.py:7502-7509`).

**Truncation contract:** when a `callers`/`blast-radius` JSON payload has `"result_incomplete":
true`, the scan hit a cap — treat the list as partial, never as proof of zero callers. This is the
audit-workflow contract; see `tensor-grep-code-audit` for the full decision procedure (P2, P7) —
this skill only covers that the flag exists and what invoking the command looks like.

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

`session open` (`main.py:8049`) takes `path` (default `.`) and `--max-repo-files` (default 512,
the agent-safe cap). `session refresh` (`main.py:8242`) and every `session <subcmd> SESSION_ID
[PATH]` command require `session_id` as the **first** positional argument — it is not implicit.
`session serve` (`main.py:8839`) additionally accepts `--refresh-on-stale` to refresh once and
retry a request when file changes are detected mid-stream; passing `--no-jsonl` errors (JSONL is
currently the only serve mode, `main.py:8856-8858`).

`session daemon start/status/stop` (`session_daemon_app`, `main.py:8085-8156`) each take only
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

`checkpoint undo` (`main.py:9046`) takes `checkpoint_id` as an optional positional, or `--last` to
restore the newest checkpoint for `path` without naming an ID — do not pass both
(`main.py:9063-9066`). If `checkpoint_id` resolves to an existing filesystem path, the error
message suggests `--last` explicitly (`main.py:9083-9090`), which is a strong signal the two
positionals (`checkpoint_id`, `path`) got confused.

## 6. AST scan (built-in rule packs) and structural run/rewrite

```powershell
tg rulesets --json                                          # list built-in packs
tg scan --ruleset RULESET_NAME --path C:\repo\api --json    # narrowest useful root first
tg scan --config sgconfig.yml --json                        # custom ast-grep project config
tg scan --rule my-rule.yml --json                            # single custom rule, no sgconfig
```

`scan` (`main.py:9211`) accepts positional `PATHS`, or `--path` (default `.`) when using a
built-in ruleset — the two are mutually exclusive (`main.py:9336-9338`), as are `--rule`,
`--ruleset`, and `--inline-rules` with each other (`main.py:9328-9331`). Useful narrowing flags:
`--glob`/`-g`, `--type`/`-t`, `--max-depth`, `--filter`/`-f` (regex over loaded rule IDs). Baseline
workflow: `--baseline FILE` / `--write-baseline FILE` compare or snapshot matched-finding
fingerprints; `--suppressions FILE` / `--write-suppressions FILE` mark or record accepted findings
(writing suppressions requires `--justification TEXT`). `--allow-broad-generated-scan` opts into an
otherwise-refused scan of a generated/cache/dependency/multi-project root — prefer scoping first.

```powershell
tg run "function_definition" C:\repo\src --lang python --json
tg run --pattern 'def $NAME($$$ARGS): $$$BODY' --rewrite 'def $NAME($$$ARGS) -> None: $$$BODY' C:\repo --apply --verify
```

`run` (`main.py:11160`) takes the AST pattern positionally (or via `--pattern`/`-p`) and an
optional `PATH`; supplying only a path that exists with no pattern is a hard error
(`main.py:11256-11263`), not a silent zero-match. `--rewrite`/`-r` sets the replacement,
`--apply` writes it, `--verify` runs tests after applying, `--checkpoint` wraps the apply in a
checkpoint, `-U`/`--update-all` is an ast-grep-compatible alias for apply-all (requires
`--rewrite`). Read-only structural-search extras: `--selector`, `--strictness`, `--stdin`,
`--globs` (repeatable, prefix `!` to exclude), `--filter` (text regex over matched nodes),
`--files-with-matches`. PowerShell users must single-quote patterns containing `$` captures (e.g.
`'def $NAME($$$ARGS): $$$BODY'`) or PowerShell expands `$NAME` before `tg` sees it.

## 7. MCP server

```powershell
tg mcp
```

Starts a **stdio** MCP server (`FastMCP("tensor-grep")`, `mcp_server.py:64`, `anyio.run` over
`_run_mcp_stdio_async`, `mcp_server.py:3736-3748`) — it is meant to be launched by an MCP client
(Claude Desktop, an agent harness), not run interactively and left open in a terminal.

Call `tg_mcp_capabilities` **first** in any new client/sandbox — it reports which tools work
without a standalone native `tg` binary versus which require one (`mcp_server.py:1341-1349`).

Representative tool names (there are ~35+; grep for the current authoritative list — see
Provenance below): `tg_search`, `tg_ast_search`, `tg_symbol_defs`, `tg_symbol_source`,
`tg_symbol_refs`, `tg_symbol_callers`, `tg_symbol_impact`, `tg_symbol_blast_radius`,
`tg_symbol_blast_radius_render`, `tg_symbol_blast_radius_plan`, `tg_context_pack`,
`tg_context_render`, `tg_edit_plan`, `tg_agent_capsule`, `tg_ruleset_scan`, `tg_rewrite_plan`,
`tg_rewrite_apply`, `tg_classify_logs`, `tg_devices`, `tg_index_search`, `tg_repo_map`,
`tg_checkpoint_create` / `_list` / `_undo`, `tg_session_open` / `_list` / `_show` / `_refresh` /
`_edit_plan` / `_context_render` / `_blast_radius*`, `tg_audit_manifest_verify`,
`tg_audit_history`, `tg_audit_diff`, `tg_review_bundle_create` / `_verify`.

`tg_rewrite_apply` refuses free-form `lint_cmd`/`test_cmd` (they shell-execute on the host) unless
the operator opts in with `TG_MCP_ALLOW_VALIDATION_COMMANDS=1`, returning
`code="unsupported_option"` otherwise. MCP tool argv builders that shell out to a native `tg`/`rg`
binary must insert a `--` end-of-options sentinel before user/LLM-controlled positionals (CWE-88 /
the MCP-276 CVE class) — this is a live hardening item, not a solved-forever property; see
`AGENTS.md` "Security Hardening Patterns" and `tensor-grep-debugging-playbook` /
`tensor-grep-change-control` before adding a new MCP tool that shells out.

## 8. Diagnostics and operational health — doctor, dogfood, upgrade, repair-launcher

```powershell
tg doctor --json                       # full diagnostics, LSP included by default
tg doctor --no-lsp --json              # skip external LSP provider probes
tg doctor C:\repo --config sgconfig.yml --json
```
`doctor` (`main.py:9904`) takes `path` (default `.`), `--config` (default `sgconfig.yml`),
`--with-lsp/--no-lsp` (default **on**), `--json`. Inspect `path_tg_first_launcher_kind`,
`fresh_shell_path_tg_first_launcher_kind`, `python_subprocess_path_tg_first_launcher_kind`,
`shell_escaping_guidance`, and any `*_is_foreign` field before trusting a Windows timing or
routing claim — see `.claude/skills/tensor-grep/SKILL.md` "Start Here" for the full field list and
`tensor-grep-diagnostics-and-tooling` for interpreting them in depth.

```powershell
tg dogfood --output artifacts/dogfood_readiness.json
tg dogfood --json --root C:\repo --timeout-s 170
```
`dogfood` (`main.py:9652`) runs the agent-readiness gate and prints a one-page verdict; it "writes
only explicit `--output`" plus a sibling child readiness report next to it — it does not write
anywhere by default with no `--output` given (`dogfood.py` docstring, `main.py:9679`). Flags:
`--root` (default `.`), `--output PATH`, `--expected-version` (defaults to `pyproject.toml`),
`--json`, `--progress auto|always|never` (stderr only), `--progress-interval-s` (30.0),
`--timeout-s` (170.0, the nested `agent_readiness.py` child budget), `--no-shell-probes`,
`--no-wsl-probe`. A non-zero exit means at least one readiness check failed — read
`failed_checks` in the verdict before treating a release or a change as safe.

```powershell
tg upgrade
```
`upgrade` (`main.py:9939`) upgrades the installed `tensor-grep` package to the latest PyPI
release. It tries, in order: `uv tool install --force` first **only** when the running Python is a
`uv tool`-managed venv (`_is_uv_tool_managed_python`, `main.py:9930-9935`, detects `.../uv/tools/`
in `sys.executable`), then `uv pip install --upgrade --refresh-package tensor-grep`, then `pip
install --upgrade --no-cache-dir`. This is the source-aware upgrade path shipped to fix a WSL
uv-tool install getting stranded at a stale version — see `tensor-grep-failure-archaeology` for
the incident. `tg upgrade` also verifies the sidecar import/version post-upgrade and schedules a
managed native front-door refresh when the sidecar version moved ahead of the native binary.

```powershell
tg repair-launcher --json
tg repair-launcher --allow-foreign-rename --json     # only for a foreign tg.exe you own
```
`repair-launcher` (`main.py:9868`, Windows-relevant) removes a verified or self-identifying stale
`tensor-grep` Python `Scripts\tg.exe` launcher that shadows the managed native front door on PATH.
`--allow-foreign-rename` additionally moves aside a **foreign** (non-tensor-grep) `tg.exe` — use it
only when you own that binary.

## 9. Artifact conventions — where state and JSON reports actually land

| Location | What lives there | Created by | Tracked in git? |
| --- | --- | --- | --- |
| `.tensor-grep/sessions/` | `index.json` + per-session repo-map payloads | `tg session open` | No — `/.tensor-grep/` is gitignored (`.gitignore:51`) |
| `.tensor-grep/checkpoints/` | `index.json`, per-checkpoint `metadata.json` + `snapshot/` tree | `tg checkpoint create` | No — same `.tensor-grep/` ignore rule |
| `.tg_semantic_index/` (or `$TG_SEMANTIC_INDEX_DIR`) | Experimental semantic (dense) index shards | opt-in semantic-search paths | Not committed; experimental subsystem |
| `artifacts/` | `--output` JSON from `tg dogfood`, `scripts/agent_readiness.py`, `benchmarks/run_*.py` | explicit `--output PATH` only — nothing is written here by default | No — `/artifacts/` (via `artifacts/` in `.gitignore:61`) is gitignored |

None of these directories are portable outputs to hand to another agent/process by default —
treat them as local cache/scratch. If you need a durable artifact, pass an explicit `--output` and
copy it somewhere tracked (or attach it to a PR) yourself; `tg dogfood`/benchmark scripts will not
do that for you.

`sgconfig.yml` (used by `tg scan --config`) is a project-level ast-grep config file, not a
tensor-grep cache directory — it lives wherever you point `--config`, typically the repo root.

## 10. The scope-a-path workaround (whole-repo `tg search` hangs)

`tg search PATTERN` with **no path** argument (or `--glob X -l` without a scoped path) walks from
the current directory and can hit the ripgrep subprocess timeout before returning any result.
`TG_RG_TIMEOUT_SECONDS` defaults to **60.0 seconds** (`subprocess_policy.py:44`, lowered from 600s
in #288) — after that it fails fast with an actionable stderr hint to scope the search or raise
the env var, rather than hanging indefinitely. It is still slow up to that point because tg's own
index/cache dirs (`.tensor-grep/`, `.tg_semantic_index/`) and any vendored external-repo trees in
scope are not excluded from the walk.

**Workaround — always scope to a path:**

```powershell
tg search "pattern" C:\repo             # ~0.4s
tg search "pattern" --glob "*.py" C:\repo
# Avoid: tg search "pattern" --glob "*.py" -l     (no path — can walk the whole tree)
```

Same rule for file listing — prefer a scoped root over `tg search --files . --hidden --no-ignore`
across a large workspace; see `.claude/skills/tensor-grep/SKILL.md` for the broad-generated-scan
guardrail (`--allow-broad-generated-scan`) that now refuses unbounded scans of build/cache/
multi-project roots outright. This whole-repo-hang behavior is a known, currently-open issue — see
`tensor-grep-debugging-playbook` if you are actively chasing it rather than just working around it.

## 11. Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Matches found, or the command succeeded |
| 1 | No matches found (search-family commands), or a handled command error (`typer.Exit(1)`) |
| 2 | Usage/argument error (`typer.Exit(code=2)`) or an unhandled error |

Search-family exit codes mirror ripgrep's convention (0 = match, 1 = clean no-match, 2 = error) —
do not treat exit 1 from `tg search` as a failure in a script; check the actual output/JSON.

## 12. Common mistakes

| Mistake | Correction |
| --- | --- |
| Running `tg search PATTERN` with no path in a large repo | Always scope to a path (§10); the timeout default is 60s, not unlimited |
| Reading `tg search --json` as ripgrep JSON Lines | It is tensor-grep's own aggregate JSON object; use `--format rg --json` for rg's JSON Lines schema |
| Passing `SYMBOL PATH` (reversed) to `defs`/`refs`/`callers`/`blast-radius` | Auto-corrected with a stderr warning, but write `PATH SYMBOL` — the canonical, documented order |
| Treating `callers=0` / `result_incomplete: true` as "no callers" | Truncated ≠ dead; widen scope or raise the cap — see `tensor-grep-code-audit` |
| Calling `tg session refresh`/`edit-plan`/etc. without the `session_id` first arg | Every session subcommand except `open`/`list`/`daemon` requires `SESSION_ID` as arg 1 |
| Expecting `tg dogfood`/benchmark scripts to persist a report without `--output` | Nothing is written by default; pass `--output PATH` explicitly (§9) |
| Assuming `CliRunner`-style invocation proves a routing/flag fix works | Dogfood the real published binary — `CliRunner` bypasses the `bootstrap` front door entirely (`AGENTS.md` "Dogfood the Real Binary") |
| Running `tg upgrade` inside a `uv tool`-managed install expecting `pip`/`uv pip` to work | `tg upgrade` already detects this and uses `uv tool install --force` first; do not hand-roll a different upgrade command |
| Starting `tg mcp` in an interactive terminal expecting text prompts | It is a stdio server for an MCP client, not an interactive REPL |

## Provenance and maintenance

Facts here were pinned by reading `src/tensor_grep/cli/main.py` and
`src/tensor_grep/cli/mcp_server.py` directly at v1.17.25 (`pyproject.toml:322`, commit history
current through `02a4aaa`, 2026-07-02). Re-verify before trusting this skill on a newer version:

```powershell
# Version currently installed / current tag
tg --version --verbose
grep -n "^version" pyproject.toml

# Full, current @app.command surface (compare against §1-§8 above)
grep -n "@app.command\|@session_app.command\|@session_daemon_app.command\|@checkpoint_app.command\|@review_bundle_app.command" src/tensor_grep/cli/main.py

# Full, current MCP tool surface (compare against §7)
grep -n "@mcp.tool" -A1 src/tensor_grep/cli/mcp_server.py | grep "^def "

# TG_RG_TIMEOUT_SECONDS default (compare against §10)
grep -n "_configured_positive_float(\"TG_RG_TIMEOUT_SECONDS\"" src/tensor_grep/cli/subprocess_policy.py

# Artifact directory names (compare against §9)
grep -n "_TG_DIRNAME\|_SESSIONS_SUBDIR\|_CHECKPOINTS_SUBDIR" src/tensor_grep/cli/session_store.py src/tensor_grep/cli/checkpoint_store.py
grep -n "^artifacts/\|^/\.tensor-grep/" .gitignore

# Registration-completeness gate (if a command/flag was added/removed since this was written)
grep -n "KNOWN_COMMANDS\|PUBLIC_TOP_LEVEL_COMMANDS" src/tensor_grep/cli/commands.py tests/e2e/test_routing_parity.py
```

Open uncertainties this skill does not resolve: the exact current MCP tool count (stated here as
"~35+", enumerate with the grep above for an exact figure); whether `--symbol`/`--query` hidden
flags have since been removed (they were still present and working, with a deprecation warning, at
v1.17.25 — `main.py:7429-7440`); and the exact set of `SEARCH_PYTHON_PASSTHROUGH_FLAGS` /
`_TG_ONLY_SEARCH_FLAGS` (that pairing is `tensor-grep-config-and-flags`' territory, not
re-enumerated here).
