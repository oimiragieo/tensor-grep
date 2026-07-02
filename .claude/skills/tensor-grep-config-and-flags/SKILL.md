---
name: tensor-grep-config-and-flags
description: Use when adding, changing, or auditing a tg environment variable, CLI flag, or provider mode (native/lsp/hybrid); when a search flag silently leaks to ripgrep or a command misroutes; when deciding whether a config axis (GPU, LSP, classify, semantic) is production or EXPERIMENTAL default-OFF; or before registering a new `tg search --flag` or `tg COMMAND`. Catalogs the load-bearing TG_*/TENSOR_GREP_* env vars (routing, timeouts, GPU, classify, session, MCP security, LSP) with their default and guard, and the 2-front-door / 4-site registration checklist.
---

# tensor-grep config and flags

A ground-truthed catalog of every tg config axis — env vars, CLI flags, provider modes — plus the
registration checklist for adding a new one. Verified against source as of 2026-07-02, **v1.17.25**
(`pyproject.toml:322`). Re-verify commands are in [Provenance and maintenance](#provenance-and-maintenance)
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

GPU remains **EXPERIMENTAL** end-to-end: slower than CPU, no promotion-ready path, P1 CUDA kernel work
is paused (`AGENTS.md:226-234`, "Roadmap Sequencing"). Do not market or default-enable it.

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
| Local hybrid semantic search (BM25 + CPU dense embeddings) | **Not yet shipped** — roadmap item #1 (`AGENTS.md:226-234`) | n/a | Do not document flags for this as if they exist; check `AGENTS.md` Roadmap Sequencing for current status before claiming it's available. |

## Checklist: adding a flag or command

This is the single highest-value thing to get right in this repo — miss a registration site and the
new flag/command **misroutes silently**, passing CliRunner tests while breaking the real published
binary. **See `tensor-grep-architecture-contract` for the full 4-site command / 2-site search-flag
registration table and the rationale for why each site exists**, and `tensor-grep-change-control` for
the PR/merge gate around it (`AGENTS.md:178-196`) — this skill does not restate that table.

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
```

If `AGENTS.md`'s `release_docs_current_tag` no longer says `v1.17.25`, treat every default/line-number
claim in this file as needing re-verification, not just the version string.
