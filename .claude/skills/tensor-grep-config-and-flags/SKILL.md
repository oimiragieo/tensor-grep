---
name: tensor-grep-config-and-flags
description: Use when adding, changing, or auditing a tg environment variable, CLI flag, or provider mode (native/lsp/hybrid); when a search flag silently leaks to ripgrep or a command misroutes; when deciding whether a config axis (GPU, LSP, classify, semantic) is production or EXPERIMENTAL default-OFF; when adding a new `SearchConfig` field and needing to know whether it must be forwarded/refused/KNOWN_GAP'd for native delegation; or before registering a new `tg search --flag` or `tg COMMAND` (including `tg inventory`'s `--max-repo-files`). Catalogs the load-bearing TG_*/TENSOR_GREP_* env vars (routing, timeouts, GPU, classify, session, MCP security, LSP) with their default and guard, the 2-front-door / 4-site registration checklist, and the native-delegation field-coverage ratchet.
---

# tensor-grep config and flags

A ground-truthed catalog of every tg config axis — env vars, CLI flags, provider modes — plus the
registration checklist for adding a new one. Verified against source as of 2026-07-16, **v1.78.1**
(`pyproject.toml`). Re-verify commands are in [Provenance and maintenance](#provenance-and-maintenance)
because these drift with every release.

## When to use this skill

- You are adding, renaming, or removing an env var or CLI flag.
- A search flag is reaching ripgrep raw (`rg: unrecognized flag` at runtime) or a command 404s.
- You need to know whether a knob (`--gpu-device-ids`, `--provider lsp`, `TENSOR_GREP_CLASSIFY_PROVIDER=cybert`)
  is production-safe to recommend to a user, or still experimental/default-off.
- You need the authoritative default value or guard condition for a `TG_*` / `TENSOR_GREP_*` variable
  before writing docs, a benchmark harness, or an agent prompt that references it.

## When NOT to use this skill (use the sibling instead)

| If you need... | Use instead |
|---|---|
| The *why* behind the front-door/routing architecture, not just the flag list | `tensor-grep-architecture-contract` |
| The process gates for *shipping* a flag/command change (PR, CI, one-merge-per-tick) | `tensor-grep-change-control` |
| Reproducing/debugging a routing bug once you already know which flag is involved | `tensor-grep-debugging-playbook` |
| `tg doctor` output fields, dogfood harness, benchmark scripts | `tensor-grep-diagnostics-and-tooling` / `tensor-grep-benchmark-and-proof-toolkit` |
| How to actually *use* `tg` commands day to day (not configure them) | `.claude/skills/tensor-grep/SKILL.md` |
| Build/toolchain setup (uv, maturin, cargo) | `tensor-grep-build-and-env` |
| Release mechanics / positioning claims | `tensor-grep-release-and-positioning` |

## The two front doors, and why config is split across them

tg has **two CLI entry points that both parse flags**, and a config change that only lands in one
is a silent bug, not a crash:

1. **Python bootstrap** (`src/tensor_grep/cli/bootstrap.py`) — `tensor_grep.cli.bootstrap:main_entry`
   intercepts plain-text searches *before* the Typer app loads and forwards them straight to `rg`.
2. **Rust native front door** (`rust_core/src/main.rs`) — the standalone `tg` binary, which re-implements
   flag parsing with `clap`.

The canonical, always-current documentation of every env var and the full `search` flag surface lives
in two places that are meant to stay in sync — read these first when you need ground truth fast:

- `tg --help` epilog: `src/tensor_grep/cli/main.py:187-200` (the `app = typer.Typer(help="""...""")` block).
- Native `tg --help` epilog: `ENVIRONMENT_OVERRIDES_HELP` const, `rust_core/src/main.rs:51`.

If those two drift from each other or from this file, trust the source, not this document — see
[Provenance and maintenance](#provenance-and-maintenance).

## Environment variable catalog

Boolean env vars in tg follow one convention everywhere (`env_flag_enabled`,
`src/tensor_grep/cli/runtime_paths.py:13-15`): the raw value is lower-cased and stripped, and it is
"on" only if it is exactly `1`, `true`, `yes`, or `on` — anything else (including unset) is "off".

### Routing / launcher

| Var | Default | Effect | Source |
|---|---|---|---|
| `TG_SIDECAR_PYTHON` | `sys.executable` | Python executable used for sidecar-backed commands (classify, GPU sidecar). | `main.py:188`, `main.py:533` |
| `TG_NATIVE_TG_BINARY` (alias `TG_MCP_TG_BINARY`) | auto-resolved | Path to the native `tg` binary front door used by Python-backed commands. Priority 1 override; stale in-tree dev builds (`rust_core/target/{debug,release}/tg.exe`) are otherwise skipped unless pinned here. | `main.py:189`, `runtime_paths.py:238-248` |
| `TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR` (alias `TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR`) | `cpu` | `nvidia`/`cuda` prefers the NVIDIA release-native front-door asset (`tg-*-nvidia.exe`), with CPU fallback; anything else normalizes to `cpu`. | `main.py:190`, `main.py:454-473` |
| `TG_RG_PATH` | auto-resolved | Path to the `rg` executable used for text-search passthrough. | `main.py:191`, `runtime_paths.py:281` |
| `TG_FORCE_CPU` | off | Force CPU routing for search commands (boolean convention). | `main.py:192`, `main.py:2758` |
| `TG_RUST_FIRST_SEARCH` | off | Opt-in: prefer the Rust native front door before Python bootstrap logic for search dispatch. | `bootstrap.py:242` |
| `TG_RUST_EARLY_RG`, `TG_RUST_EARLY_POSITIONAL_RG` | off | Internal early-dispatch toggles surfaced by `tg doctor --json`; not documented in the public `--help` epilogs. | `main.rs:53-54`, `main.py:2761-2762` |
| `TG_RESIDENT_AST` | off | Enables the resident AST worker path (see `docs/runbooks/resident-worker.md`); reported by `tg doctor --json`. | `main.py:2759`, `main.rs` (search `TG_RESIDENT_AST`) |
| `TG_DISABLE_NATIVE_TG` | off | Kill-switch: forces `resolve_native_tg_binary()` to return `None`, fully bypassing the native `tg` binary front door (Python-backed commands fall back to pure-Python routing even if a compatible native binary is resolvable). | `runtime_paths.py:234` |
| `TG_DISABLE_RG` | off | Kill-switch: forces the native binary's ripgrep resolver to return `None`, so the native front door treats `rg` as unavailable regardless of `TG_RG_PATH`/PATH. | `rust_core/src/rg_passthrough.rs:13,477` |

### Timeouts

| Var | Default | Effect | Source |
|---|---|---|---|
| `TG_RG_TIMEOUT_SECONDS` | **60.0s** (lowered from 600s in #288) | Ripgrep-passthrough search timeout. Fails fast with a stderr hint to scope the search or raise the timeout, instead of hanging. Overridden by `TG_SIDECAR_TIMEOUT_MS` when that is set to a positive value. | `subprocess_policy.py:32-44` |
| `TG_SIDECAR_TIMEOUT_MS` | unset | Milliseconds; if set and > 0, **takes precedence over `TG_RG_TIMEOUT_SECONDS`** for the ripgrep-passthrough timeout (`ms / 1000.0`). Also documented as the general sidecar-command timeout. | `subprocess_policy.py:32-40`, `main.py:193` |
| `TG_SUBPROCESS_TIMEOUT_SECONDS` | 600.0s | Default timeout for the generic `run_subprocess()` helper (git ops, MCP validation commands, etc.) unless a call site overrides `timeout_env_var`. | `subprocess_policy.py:20-25` |
| `TG_GIT_TIMEOUT_SECONDS` | 120.0s | Timeout for git subprocess calls (checkpoint/session git operations). | `subprocess_policy.py:28-29` |
| `TENSOR_GREP_TRITON_TIMEOUT_SECONDS` | 5.0s | Timeout for Triton-backed NLP (CyBERT) probes. | `cybert_backend.py:18-19`, `main.py:196` |
| `TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS` | 2.0s | Total per-command budget for optional external LSP provider requests before falling back to native evidence. | `repo_map.py:95-96`, `main.py:198` |
| `TENSOR_GREP_LSP_REQUEST_TIMEOUT_SECONDS`, `TENSOR_GREP_LSP_INITIALIZE_TIMEOUT_SECONDS` | implementation defaults | Per-request / per-initialize LSP timeouts; reported (not overridden) by `tg doctor --json`. | `main.py:2763-2764` |

Every non-positive or unparseable value for the float-typed timeout vars above silently falls back to
the compiled-in default (`_configured_positive_float`, `subprocess_policy.py:9-17`) — a bad value does
not crash, it just gets ignored. Do not assume "I set it, therefore it changed" without checking
`tg doctor --json`'s `env` block (see [Discovering effective config](#discovering-effective-config-tg-doctor---json)).

### GPU

| Var / flag | Default | Effect | Source |
|---|---|---|---|
| `--gpu-device-ids IDS` (CLI flag, e.g. `tg search --gpu-device-ids 0,1`) | unset (no GPU routing) | **Explicit, user-intent GPU pin** for search / `tg agent` / benchmark evidence probes. Comma-separated non-negative ints; parse errors raise `typer.BadParameter` immediately (`main.py:4047-4074`). | `main.py:5758-5762`, `main.py:6962-6969` |
| `TENSOR_GREP_DEVICE_IDS` | unset (all detected devices visible) | Lower-level env allow-list of GPU IDs available to tensor-grep at all (like `CUDA_VISIBLE_DEVICES`), consulted by device detection/memory-manager code, not just the CLI flag. | `device_detect.py:30-55`, `main.py:194` |
| `--gpu-timeout-s` (flag, `tg agent` only) | 5.0s | Max seconds for each opt-in agent GPU evidence subcommand. | `main.py:6970-6975` |

**Fail-loud contract**: an explicit `--gpu-device-ids` request that cannot be honored raises
`ConfigurationError` (a `RuntimeError` subclass, `pipeline.py:20-21`) — it never silently falls back to
CPU. Example message shape (`pipeline.py:26-39`):

```
GPU acceleration is experimental. Explicit GPU device selection [0, 1] could not initialize a
GPU backend: fixed-string (-F) search has no GPU backend
```

This is deliberate: `-F`/`--fixed-strings` GPU search has no kernel yet, so pairing it with an explicit
`--gpu-device-ids` must error, not silently drop to CPU and report a clean result (see the Backend
Fail-Closed Contract in `AGENTS.md:214-224`, and `tensor-grep-architecture-contract` for the general
principle). By contrast, the **heuristic** (non-explicit) auto-GPU path degrades to CPU with a visible
`warnings.warn(...)` + `fallback_reason`, not an exception — only *explicit* user intent gets fail-loud
treatment (`pipeline.py:174-176`, `_should_honor_explicit_gpu_ids`).

GPU remains **EXPERIMENTAL** end-to-end. GPU Phase-0 SHIPPED (v1.75.0-v1.75.4, PRs #593-#597):
NVIDIA native assets are built and locally correctness-proven (RTX 4070 `sm_89` / RTX 5070 `sm_120` --
`docs/gpu_crossover.md`), but gated OFF the public release by the CI Actions var
`TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; GPU asset
publishing needs the non-default `native-frontdoor-gpu`) -- Phase 1 is now a reversible flag-flip, not
a multi-week rebuild. That flip publishes assets only: no speed crossover is proven vs `rg`/`tg_cpu`,
GPU auto-recommendation stays `false`, and the reviewer-gated `public-gpu-proof.yml` speed-crossover
gate remains unmet (`docs/CONTRACTS.md:80-82`). Do not market or default-enable it.

### Classify provider

| Var | Default | Effect | Source |
|---|---|---|---|
| `TENSOR_GREP_CLASSIFY_PROVIDER` | `heuristic` (local, deterministic) | Set to `cybert` or `triton` to opt into the CyBERT/Triton NLP classifier for `tg classify FILE`. Any other value (including unset) uses the local regex-heuristic classifier. | `sidecar.py:16-17`, `sidecar.py:119-125` |

`tg classify` always reports provenance in its JSON output (`classification_backend` /
`provider_requested` / `provider_used` / `provider_status` / `fallback_reason` / `cache`,
`sidecar.py:57-71`) so a caller can distinguish "asked for cybert, got heuristic because it failed"
from "asked for heuristic". Never read a classify result as model-backed without checking
`provider_used`.

### Session / daemon

| Var | Default | Effect | Source |
|---|---|---|---|
| `TG_SESSION_MAX` | 64 | Max on-disk cached sessions retained per root; oldest are pruned past this. | `session_store.py:49-51`, `120-121` |
| `TG_SESSION_NEARBY_LOOKUP` | off | By default session discovery is confined to the explicit root; set to opt into parent/sibling-directory session discovery. | `session_store.py:52-54`, `124-130` |
| `TG_SESSION_DAEMON_IDLE_SECONDS` | 900.0s | Idle stretch (no requests) after which the warm `tg session daemon` self-shuts-down. Non-positive disables the idle limit. | `session_daemon.py:67-72` |
| `TG_SESSION_DAEMON_MAX_UPTIME_SECONDS` | 86400.0s (24h) | Hard max daemon lifetime regardless of activity. Non-positive disables the uptime limit. | `session_daemon.py:67-73` |
| `TG_SESSION_DAEMON_RESPONSE_TIMEOUT_SECONDS` | 60.0s | Client-side socket read timeout for a daemon response (#390, moat P0-6 step 5). Env-configurable so a large repo whose warm-daemon graph query legitimately needs >60s isn't killed by a hard cap that returns a bare "timed out"/exit 1/zero JSON. Does **not** by itself bound the daemon's own traversal — the served graph commands run on a cached map and are not covered by the scan-side `--deadline`; see the #390 daemon-path gap in `tensor-grep-large-repo-scale-campaign`. | `session_daemon.py:52,58` |
| `TENSOR_GREP_SESSION_RESPONSE_CACHE_MAX_BYTES` | 8 MiB (`8 * 1024 * 1024`) | Byte cap on the in-process session response cache. | `session_store.py:44-45`, `main.py:200` |

The daemon binds to `127.0.0.1` only (`session_daemon.py:46`) — it is not exposed off-host. Operational
detail (starting/stopping the daemon, `tg session daemon start|status|stop`) lives in
`.claude/skills/tensor-grep/REFERENCE.md`, not here.

### MCP security gate (default-OFF)

| Var | Default | Effect | Source |
|---|---|---|---|
| `TG_MCP_ALLOW_VALIDATION_COMMANDS` | off | Gates whether the `tg mcp` server's `tg_rewrite_apply` tool may accept and shell-execute `lint_cmd` / `test_cmd` (from either the direct call arguments **or** a loaded apply-policy JSON file — both paths are gated, not just the direct one). Off by default because these commands can be steered by untrusted repo content / prompt injection; the agent-safe edit loop does not require them. Rejected requests return `code="unsupported_option"`. | `mcp_server.py:249-254`, `apply_policy.py:41-45,226-230` |

This is an Enablement Discipline case: default-OFF, opt-in only, and it is the kind of knob you should
**never** flip on in a shared/CI MCP server config without an explicit operator decision — see
`AGENTS.md` "Enablement Discipline (autonomous behaviors)" (referenced from the workspace root
`CLAUDE.md`) and `tensor-grep-change-control` for the graduation gate (council-verify → dry-run →
conscious flag-flip).

### In-process caches (bound long-lived agent-loop state)

These exist so a long-lived `tg session daemon` / MCP server process doesn't grow unbounded caches.
All are documented together in `main.py:199-200`; defaults are implementation-internal (read the
cited module if you need the exact number) — this skill's job is to tell you *that* they exist and
*where*, not to duplicate the numeric defaults, which drift independently of flags/commands.

- `TENSOR_GREP_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES`
- `TENSOR_GREP_STRING_INDEX_CACHE_MAX_ENTRIES`
- `TENSOR_GREP_AST_QUERY_CACHE_MAX_ENTRIES`
- `TENSOR_GREP_AST_NODE_INDEX_CACHE_MAX_ENTRIES`
- `TENSOR_GREP_REPO_CONTEXT_CACHE_MAX_ROOTS`
- `TENSOR_GREP_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES`
- `TENSOR_GREP_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES`

### LSP provider

| Var | Default | Effect | Source |
|---|---|---|---|
| `TG_LSP_PROVIDER` | `native` | Overrides the LSP semantic-provider mode for editor/MCP clients; same value space as `--provider` (`native`/`lsp`/`hybrid`). Set by `tg lsp --provider ...` before calling `run_lsp()`. | `main.py:9772-9799`, `main.rs:51` |
| `TG_ALLOW_UNVERIFIED_TOOLCHAIN` | off | Security opt-out: skips checksum verification of downloaded LSP-toolchain archives/binaries (rust-analyzer, etc.) for air-gapped/offline installs — same default-secure/opt-out-to-weaken pattern as `TG_MCP_ALLOW_VALIDATION_COMMANDS` below. Off by default; fails closed (refuses the unverified binary) unless set. | `lsp_provider_setup.py:229-265,465-480` |

## Provider modes: `native` / `lsp` / `hybrid`

The `--provider` flag appears on every symbol/navigation command (`defs`, `refs`, `source`, `impact`,
`callers`, `blast-radius*`, `context-render`, `edit-plan`, `agent`, `lsp`) with the **same three-way
contract everywhere**, default `native`:

```
tg defs REPO_PATH SYMBOL --provider lsp
tg blast-radius REPO_PATH SYMBOL --provider hybrid
tg lsp --provider hybrid
```

- `native` — tg's own tree-sitter/AST-derived symbol graph. Production default.
- `lsp` — routes through an external language server (`ExternalLSPProviderManager`). **EXPERIMENTAL.**
- `hybrid` — combines native with LSP evidence when available.

`tg lsp` validates the value explicitly and exits 2 on anything else
(`{"native", "lsp", "hybrid"}` check, `main.py:9779-9785`):

```
Unsupported LSP provider mode; expected one of: native, lsp, hybrid
```

**LSP-availability is not LSP-proof** — this is a load-bearing distinction from `AGENTS.md:163`:
"Treat `tg lsp-setup` / `tg doctor --with-lsp` availability as install evidence only; provider-backed
navigation must report `health_status`, `health_check`, `lsp_proof`, `lsp_evidence_status`, and
`not_lsp_proof_reason` when it falls back to native evidence. A navigation row counts as LSP proof only
when it carries `lsp_provider_response = true` from a completed provider request." Do not tell a user
"LSP is working" because `tg doctor --with-lsp` found a binary on PATH.

## Production vs EXPERIMENTAL (default-OFF), and the guard that keeps it off

| Axis | Status | Guard | Why |
|---|---|---|---|
| Native CPU/rg search, AST search (`tg run`), symbol nav (`native` provider) | **Production** | none — default path | Backbone of the tool. |
| `tg agent` / Actionable Context Capsule | **Production, opt-in by design** | explicit `tg agent` invocation | Not a default search mode; it's a distinct command surface, but it is a shipped, supported feature. |
| `classify` local heuristic | **Production** | default | Deterministic, no model download. |
| `--gpu-device-ids` / GPU backends | **EXPERIMENTAL** | must be explicitly requested; heuristic auto-GPU only fires when `rg` is unavailable | Slower than CPU today; no promotion-ready path (`AGENTS.md:226-234`). |
| `--provider lsp` / `--provider hybrid`, `TG_LSP_PROVIDER` | **EXPERIMENTAL** | explicit `--provider` value or `TG_LSP_PROVIDER` env | Availability ≠ working navigation; see LSP-proof contract above. |
| `TENSOR_GREP_CLASSIFY_PROVIDER=cybert`/`triton` | **EXPERIMENTAL** | explicit env opt-in | Requires a Triton/CyBERT model deployment; falls back before expensive model load if unavailable. |
| `TG_MCP_ALLOW_VALIDATION_COMMANDS=1` | **Off by design (security), not "not ready yet"** | explicit env opt-in on the MCP server process | Shell-executes `lint_cmd`/`test_cmd`, a prompt-injection surface. |
| Local hybrid semantic search (BM25 + CPU dense embeddings + RRF) | **SHIPPED, EXPERIMENTAL default-OFF** — `tg search --semantic` (`main.py:6619`; `core/retrieval_dense.py` + `core/retrieval_fusion.py`) | explicit `--semantic` flag; requires the `semantic` extra (`model2vec`, `pyproject.toml:577`), fails closed with a `rank_fallback_reason` when unavailable | No API key, no GPU, pure local CPU dense leg fused with BM25 via RRF -- see `tensor-grep-semantic-search-campaign` for build history and promotion gates. A 2nd consumer of the same dense/fusion core is `tg find` (below). |
| `TG_FIND_DENSE_WEIGHT` (`tg find` only) | `"1.0"` (unset/empty/unparseable/non-finite all resolve to this — byte-identical no-op fusion weight) | Query-adaptive `dense_weight` override for `tg find`'s `rank_chunks` calls ONLY -- gates ONLY `tg find`, never `--semantic`. A valid finite override applies ONLY to genuinely multi-word queries (`len(query.split()) > 1`, a whitespace word-count gate, #191/#630); a single whitespace-free token (a literal identifier/symbol lookup) always stays pinned at `1.0` regardless of the env value. `math.isfinite` clamps `nan`/`inf`/`-inf` back to the default (flip-prep NIT 1, #630) before it can reach `reciprocal_rank_fusion`'s sort. | `main.py:4007-4008` (`_FIND_DENSE_WEIGHT_ENV`/`_FIND_DENSE_WEIGHT_DEFAULT`), `main.py:4021-4072` (`_find_dense_weight`) -- still **default-OFF**; the flip to a non-1.0 default is a separate CEO checkpoint (`tensor-grep-semantic-search-campaign`). |

## `tg inventory`: walk-only repo manifest (v1.19.0, #343)

`tg inventory PATH [--json] [--max-repo-files N]` (`src/tensor_grep/cli/inventory.py`,
registered `main.py:7090-7091`) emits a single-pass file/byte/language/category manifest by
reusing the same gitignore-aware walker (`repo_map._iter_repo_files`) that `orient`/`callers`/
`blast-radius` trust — so counts stay truth-consistent with every other `tg` command and inherit
its `.tensor-grep`/`.git`/vendor exclusions for free.

**`--max-repo-files` defaults to `50_000`, still well above the AST map limit** — this is a
deliberate, documented divergence, not an oversight. **The AST-side number changed underneath this
divergence** (backlog #1, 2026-07-06): `DEFAULT_AGENT_REPO_MAP_LIMIT` was raised from `512` to
**`2000`** (`repo_map.py:155`), and the CLI-side mirror `_DEFAULT_AGENT_REPO_SCAN_LIMIT` (`main.py:66`)
was raised to match — do not describe the AST cap as `512` anymore.

- `DEFAULT_MAX_INVENTORY_FILES = 50_000` (`inventory.py:36`), passed to the CLI option as a
  literal `50_000` (`main.py:7093-7095`) rather than importing the constant, so the (heavy)
  `repo_map` import stays lazy. A nearby code comment still says "matching `map`'s 512 pattern" —
  that comment is about the STYLE (keep-literal, don't import), not the current live number; `map`'s
  own limit is 2000 now, not 512. A guard test pins the `50_000` literals together; re-verify with
  `grep -rn "50_000" src/tensor_grep/cli/inventory.py src/tensor_grep/cli/main.py`.
- `DEFAULT_AGENT_REPO_MAP_LIMIT = 2000` (`repo_map.py:155`) budgets a **full AST parse per file**
  for `tg map`/`orient`/`context`/`edit-plan`/session repo-map defaults — reusing it for
  `inventory` would silently truncate any repo over ~2000 files and defeat the "whole-repo
  manifest" purpose (`inventory.py:31-34` states this explicitly in a code comment).
  `inventory` is walk-only (`stat()` + an 8KB read for binary-sniffing per file), orders of
  magnitude cheaper than an AST parse, so a much higher cap (`50_000`) is still safe even after the
  AST-side raise.
- **The trap: `CALLER_SCAN_FILE_CEILING = 512` (`repo_map.py:168`) is a DIFFERENT, deliberately
  separate constant that now holds the old `512` numeral** — do not confuse it with
  `DEFAULT_AGENT_REPO_MAP_LIMIT`. Per the code comment at `repo_map.py:161-164`: raising the AST map
  limit to 2000 is safe for caller-scan latency *only because* this ceiling independently bounds the
  slow per-file caller-scan hot loop (`callers`/`refs`/`blast-radius`/`impact`) at a single internal
  chokepoint, regardless of how large the map itself is — "a naive raise [of the caller-scan ceiling]
  to 2000 would make it worse" (reintroducing the task #52 ~100s-hang shape; see
  `tensor-grep-large-repo-scale-campaign`). If you see the bare number `512` anywhere in this
  subsystem going forward, check WHICH constant it is before assuming it's the map limit.
- Truncation is **never silent**: a repo over the cap is surfaced via
  `scan_limit.possibly_truncated` + `scan_limit.truncation_cause` in the JSON payload, and as an
  ASCII `[!] truncated at max_files=...` line in text output (fixed from a U+26A0 emoji that
  crashed `typer.echo` on Windows cp1252 consoles — `#346`, commit `6b7b518`; ASCII-only is now
  the rule for all `tg` CLI output, not just `inventory`).
- Fails closed: a nonexistent `path` raises `FileNotFoundError` -> CLI exits 1
  (`inventory.py:175-176`) — a missing path must never read as a valid empty repo.

Registration follows the standard 4-site table (`KNOWN_COMMANDS` in `commands.py`, native Rust
`Commands::Inventory` in `rust_core/src/main.rs`, `PUBLIC_TOP_LEVEL_COMMANDS` in
`tests/e2e/test_routing_parity.py`, the `@app.command()` in `main.py`) — see
`tensor-grep-architecture-contract` for why each site exists.

```bash
# Re-verify the two caps and why they differ
grep -n 'DEFAULT_MAX_INVENTORY_FILES\|max_repo_files' src/tensor_grep/cli/inventory.py src/tensor_grep/cli/main.py
grep -n 'DEFAULT_AGENT_REPO_MAP_LIMIT = ' src/tensor_grep/cli/repo_map.py

# Smoke-test the command against the real binary (not CliRunner)
tg inventory . --json | python -m json.tool | head -30
```

## Checklist: adding a flag or command

This is the single highest-value thing to get right in this repo — miss a registration site and the
new flag/command **misroutes silently**, passing CliRunner tests while breaking the real published
binary. **See `tensor-grep-architecture-contract` for the full 4-site command / 2-site search-flag
registration table and the rationale for why each site exists**, and `tensor-grep-change-control` for
the PR/merge gate around it (`AGENTS.md:178-196`) — this skill does not restate that table.

### The third checklist item: native-delegation field coverage (`SearchConfig`)

Adding a new field to `SearchConfig` (`src/tensor_grep/core/config.py`) is a **third** registration
concern, separate from the 4-site command table and the 2-site search-flag table above — and it is
easy to miss because it fails silently, not loudly.

**Why it exists**: `_can_delegate_to_native_tg_search` (`main.py:3263-3282`) hands an entire search
off to the native `tg` subprocess, which `sys.exit()`s **before** the Python-side BM25 rerank and
the in-backend file sort ever run. Any `SearchConfig` field that is output-affecting but neither
forwarded into the native argv (`_build_native_tg_search_command`) nor listed in the refuse-tuple
`_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS` (`main.py:1755` onward) gets **silently dropped** —
the search still runs and returns a result, just the wrong one (unranked/unsorted), which is worse
than a crash because suppression reads as absence. This is the same bug class as the `-u`/`-uu`
no-op fixed in `#336`; the receipt this time was `#342` (commit `5e6f780`, v1.18.6->v1.19.0 range):
`rank_bm25` and `sort_files` were parsed but forwarded nowhere, so `tg search --rank --cpu` /
`--sort-files --cpu` silently returned unranked/unsorted output on the delegated fast path.

**The gate is now a governance ratchet, not a convention** — `tests/unit/test_native_delegation_field_coverage.py`
AST-derives the forwarded-field set directly from `_build_native_tg_search_command`'s source (via
`ast.walk` over `config.<attr>` reads, so it can't drift from a hand-maintained list) and asserts
`test_every_field_classified`: every `dataclasses.fields(SearchConfig)` name must be one of:

1. **Forwarded** — read by `_build_native_tg_search_command` and passed into the native argv.
2. **Refused** — listed in `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`; a non-default value
   forces `_can_delegate_to_native_tg_search` to return `False` and fall through to the
   Python/backend path. `rank_bm25` and `sort_files` live here as of `#342` — native `tg` has no
   BM25 (it routes `--rank` back to the Python sidecar) and `sort_files` is applied in-backend
   (`ripgrep_backend.py`/`rust_backend.py`), so neither is reproducible on a delegated `sys.exit`
   path.
3. **Gate-handled** — `files_with_matches`/`files_without_match`; read off explicit keyword args
   at the call site rather than the config object, so the gate itself covers them.
4. **`KNOWN_GAP`** — `_NATIVE_TG_DELEGATION_KNOWN_GAP_FIELDS` in the test file: pre-existing
   fields (AST-mode selectors, NLP threshold, internal telemetry, `ignore_*` scope flags, `no_*`
   double-negation flags) that were *already* dropped through delegation before `#342` and are
   acknowledged tech debt, not blessed as safe — a documented gap, not a silently-dropped one.
   **`case_sensitive` is NOT in this set anymore** — audit #19 forwarded it into the native argv
   via `-s` (`main.py:3353`), so it's now bucket 1 (Forwarded), not a gap; the test file's own
   comment at the `KNOWN_GAP` set records this explicitly. Don't describe `case_sensitive` as a
   native-delegation gap in new docs. A companion test (`test_known_gap_has_no_stale_entries`)
   fails if a `KNOWN_GAP` entry is later forwarded/refused/removed and the entry isn't pruned, so
   the gap set can't rot into a false-safe list either.

**When you add a `SearchConfig` field, this test goes RED until you classify it** — that red is the
checklist: forward it (native argv), refuse it (`_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`),
confirm it's gate-handled, or add it to `_NATIVE_TG_DELEGATION_KNOWN_GAP_FIELDS` with a one-line
reason. Do not add to `KNOWN_GAP` just to make the test pass — that's exactly the silent-drop this
ratchet exists to prevent; only pre-existing, reasoned gaps belong there.

**Landmine already hit once — do not re-attempt the "differs from default" runtime gate.** The
2026-06-30 #1 naive-fix failure mode: `query_pattern` is auto-set on every search
(`main.py` ~line 6045), so a generic "does any field differ from `SearchConfig()` defaults" gate
would trip on `query_pattern` on literally every call and kill the fast path entirely. The fix must
be a specific field added to the tuple, not a blanket differs-from-default check.

```bash
# Run the ratchet directly (fast, no fixtures needed)
uv run pytest tests/unit/test_native_delegation_field_coverage.py -v

# Confirm the refuse-tuple + KNOWN_GAP set still exist at these names
grep -n '_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS = ' src/tensor_grep/cli/main.py
grep -n '_NATIVE_TG_DELEGATION_KNOWN_GAP_FIELDS = ' tests/unit/test_native_delegation_field_coverage.py

# See exactly which fields the ratchet currently derives as "forwarded"
uv run python -c "
from tensor_grep.cli import main as tg_main
import ast, inspect
src = inspect.getsource(tg_main._build_native_tg_search_command)
print(sorted({n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == 'config'}))
"
```

Miss either search-flag site and the flag reaches `rg` unrecognized for users on the published binary
— an `rg: unrecognized flag` crash that CliRunner-only tests cannot see, because CliRunner calls the
Typer app directly and bypasses the bootstrap front door (`tensor-grep-architecture-contract` covers
why). **Dogfood the real binary** (`scripts/dogfood/`) after any flag/command change — do not rely on
`CliRunner` alone.

As of v1.17.1 (#282) the CI registration-completeness gate is **blocking**: a mismatch between these
sites fails CI, not just warns (`AGENTS.md:196`). There is also a standalone checker,
`src/tensor_grep/core/registration_check.py`, driven by `.tg-registration.toml` and wired into
`.github/workflows/ci.yml:319-324` — run it locally to catch a mismatch before pushing (see
[Provenance and maintenance](#provenance-and-maintenance)).

## Discovering effective config: `tg doctor --json`

Setting an env var is not the same as confirming it took effect — a bad float value silently falls
back to the default (see the Timeouts table). `tg doctor --json` echoes back the *currently observed*
value for the routing/timeout/LSP-budget env vars it knows about, under an `env` key that only includes
vars that are actually set (`_build_doctor_payload`, `main.py:2745-2767`; the `env` dict comprehension
at `main.py:3000` filters to `os.environ.get(key)` truthy). It reports:
`TG_NATIVE_TG_BINARY`, `TG_FORCE_CPU`, `TG_RESIDENT_AST`, `TG_RUST_FIRST_SEARCH`, `TG_RUST_EARLY_RG`,
`TG_RUST_EARLY_POSITIONAL_RG`, `TENSOR_GREP_LSP_REQUEST_TIMEOUT_SECONDS`,
`TENSOR_GREP_LSP_INITIALIZE_TIMEOUT_SECONDS`, `TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS`, plus the
LSP-probe timeout env var. It does **not** currently echo every var in this skill's catalog (e.g. the
session/GPU/classify vars are not in that list) — when in doubt, check the source, not just `doctor`.

For launcher-route diagnostics (which `tg` binary actually ran) rather than config-value diagnostics,
use `tensor-grep-diagnostics-and-tooling`.

## Provenance and maintenance

Flags, defaults, and registration sites drift every release — re-verify before trusting this file on
anything but the day it's dated.

```bash
# Confirm the current version this skill was verified against
grep -n '^version = ' pyproject.toml

# Re-pull the authoritative env-var help text from both front doors (diff them against this file)
sed -n '187,200p' src/tensor_grep/cli/main.py
grep -n 'ENVIRONMENT_OVERRIDES_HELP' -A1 rust_core/src/main.rs | head -5

# Re-list every TG_*/TENSOR_GREP_* env var referenced anywhere in source (catch new ones this file misses)
grep -rhoE '"(TG_|TENSOR_GREP_)[A-Z0-9_]+"' src rust_core/src | sort -u

# Re-check the 2 search-flag front doors
sed -n '160,272p' rust_core/src/main.rs   # SEARCH_PYTHON_PASSTHROUGH_FLAGS
sed -n '23,58p' src/tensor_grep/cli/bootstrap.py   # _TG_ONLY_SEARCH_FLAGS

# Re-check the 4 command registration sites
sed -n '9,54p' src/tensor_grep/cli/commands.py     # KNOWN_COMMANDS
grep -n 'enum Commands' rust_core/src/main.rs
grep -n 'PUBLIC_TOP_LEVEL_COMMANDS = ' -A5 tests/e2e/test_routing_parity.py

# Run the standalone registration checker locally (same as CI)
PYTHONPATH=src python -m tensor_grep.core.registration_check .tg-registration.toml

# Confirm provider-mode validation still lists exactly these three
grep -n 'native.*lsp.*hybrid' src/tensor_grep/cli/main.py

# Confirm the GPU fail-loud contract still raises (not falls back)
grep -n '_raise_explicit_gpu_configuration_error\|class ConfigurationError' src/tensor_grep/core/pipeline.py

# Re-verify tg inventory's two caps (50_000 walk-only vs the AST map limit) still diverge deliberately
grep -n 'DEFAULT_MAX_INVENTORY_FILES\|max_repo_files' src/tensor_grep/cli/inventory.py src/tensor_grep/cli/main.py
grep -n 'DEFAULT_AGENT_REPO_MAP_LIMIT = \|CALLER_SCAN_FILE_CEILING = ' src/tensor_grep/cli/repo_map.py

# Re-verify the native-delegation field-coverage ratchet still exists and passes
grep -n '_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS = ' src/tensor_grep/cli/main.py
uv run pytest tests/unit/test_native_delegation_field_coverage.py -v
```

Verified against source as of 2026-07-08 (v1.49.3): `tg inventory` section including
the `DEFAULT_AGENT_REPO_MAP_LIMIT` 512->2000 raise + the `CALLER_SCAN_FILE_CEILING` trap; the
native-delegation field-coverage ratchet including `case_sensitive`'s removal from `KNOWN_GAP`
(audit #19); the new `TG_SESSION_DAEMON_RESPONSE_TIMEOUT_SECONDS` env var (#390).

Re-verified as of 2026-07-16 (v1.78.1): the new `TG_FIND_DENSE_WEIGHT` row above, read directly
against `main.py:4007-4072`. The rest of this file (env-var catalog, front-door tables, GPU/LSP/
provider sections above) was **not** re-walked line-by-line in this pass — treat those sections'
exact line numbers as needing a fresh check per the rule below, independent of the sections just
re-verified.

If `AGENTS.md`'s `release_docs_current_tag` no longer says `v1.78.1`, treat every default/line-number
claim in this file as needing re-verification, not just the version string.
