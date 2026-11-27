from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import doctor_payload as _doctor_payload
from tensor_grep.cli import windows_launcher as _windows_launcher
from tensor_grep.cli._main_binding import _self
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)

# perf (+10% campaign #6 / F2.4): import the 5 broad-scan-guard constants from the
# zero-dependency `tensor_grep.io.scan_limits` module directly, NOT from
# `tensor_grep.io.directory_scanner` (which does `from tensor_grep.core.config import
# SearchConfig` at its own module level -- SearchConfig transitively pulls in the stdlib
# `dataclasses`/`inspect` chain). This module-level import runs for every full-CLI command
# (--help, scan, test, ast-info, ...); these 5 names are plain frozensets/ints consumed only by
# the module-level broad-scan literals below, never SearchConfig itself, so there is no reason
# to pay for the heavier module merely to read 5 constants from it. `DirectoryScanner` itself is
# still imported lazily, function-local, at each call site that actually walks a tree (unchanged
# by this PR). See `tensor_grep.io.scan_limits`'s module docstring for the full rationale.
# Read this module object through late-bound
# attribute reads. A BARE call to a monkeypatched name resolves through THIS module's
# globals, welding the caller to this file -- move it and the test still passes while
# production runs the unpatched original. `_self.NAME(...)` resolves at CALL time.
# The two branches are load-bearing. At runtime only `sys.modules[__name__]` works (the
# module is mid-import, so importing itself by name would be circular), but that is typed
# `ModuleType`, whose `__getattr__` returns `Any` -- under a single-branch form every
# converted call returns Any and mypy raises `no-any-return` at each concrete-returning
# caller. The TYPE_CHECKING branch never executes; it exists so the checker resolves
# `_self` to this module and keeps every real signature.
# scripts/bare_call_ratchet.py pins the remaining bare-call count and fails if it grows.
# Re-exports from the modules split out of this file on 2026-08-20 (see
# cli/_main_binding.py). EVERY moved name is
# rebound here, not only the ones a scan can prove are read. The test suite patches many of
# them on `main` and the moved code reads them back through `_self`, so the patch target must
# not move with the code -- and tests reach them through local aliases, so 'nothing references
# it' would be a claim about the scan, not about the code. Rebinding all of them keeps
# `main.<name>` exactly the surface it was before the split. Plain assignment rather than
# `from ... import x as x` only because isort expands the aliased form to three lines each.
# --- from cli/ast_scan.py ----------------------------------------------------
# --- from cli/doctor_report.py -----------------------------------------------
# --- from cli/doctor_payload.py ----------------------------------------------
from tensor_grep.cli.output_envelope import _safe_stdout_line, _with_schema_version
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.routers.upgrade import _upgrade_attempts
from tensor_grep.cli.runtime_paths import _native_tg_version as _native_tg_version
from tensor_grep.cli.runtime_paths import _native_tg_version_matches as _native_tg_version_matches
from tensor_grep.cli.runtime_paths import inspect_native_tg_binary as inspect_native_tg_binary
from tensor_grep.cli.runtime_paths import (
    is_cross_domain_native_binary as is_cross_domain_native_binary,
)

# Re-exported, not used here: a front-door checksum test reads `main.native_frontdoor_metadata_path`.
from tensor_grep.cli.runtime_paths import (
    native_frontdoor_metadata_path as native_frontdoor_metadata_path,
)
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary as resolve_native_tg_binary
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary as resolve_ripgrep_binary
from tensor_grep.cli.runtime_paths import (
    translate_path_for_windows_binary as translate_path_for_windows_binary,
)
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

_render_doctor_payload = _doctor_payload._render_doctor_payload

# --- from cli/native_frontdoor.py --------------------------------------------

# --- from cli/windows_launcher.py --------------------------------------------
_repair_windows_python_subprocess_launcher = (
    _windows_launcher._repair_windows_python_subprocess_launcher
)

# backlog #1 : kept numerically in sync with
# repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT (raised 512 -> 2000 for routing accuracy -- a file past
# the old cap never entered the map, so edit-plan/agent/context-render/defs misrouted on repos
# >512 files). This is a SEPARATE literal (not an import) because it is this module's CLI-option
# default, shared across both ROUTING commands (edit-plan/agent/context-render/defs/source) and
# CALLER-SCAN commands (callers/refs/blast-radius/impact/blast-radius-plan). Raising it to 2000
# is safe for the caller-scan commands ONLY because repo_map.CALLER_SCAN_FILE_CEILING bounds
# their actual per-file scan work at 512 regardless of how large this default is -- the
# chokepoint, not a per-command repoint, is what keeps them fast.
_DEFAULT_AGENT_REPO_SCAN_LIMIT = 2000
_DEFAULT_BLAST_RADIUS_JSON_MAX_CALLERS = 25
_DEFAULT_BLAST_RADIUS_JSON_MAX_FILES = 25
# audit #96 (answer-first payloads): defs/refs/callers/impact's own DEDICATED tests-cap, wired
# on-by-default like blast-radius's --max-callers/--max-files precedent above (not an opt-in-only
# flag -- the audit's "95% payload filler" bug needs a default that actually fixes it).
_DEFAULT_SYMBOL_MAX_TESTS = 25
_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_WINDOWS_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_PROBE_TIMEOUT_ENV = "TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS"
_DOCTOR_SCHEMA_VERSION = 3
_DOCTOR_LSP_SCHEMA_VERSION = 2
_GUARDED_BROAD_SEARCH_ROOTS = {".claude", ".claude/context"}
_BROAD_GENERATED_SCAN_DIR_NAMES = {
    "__pycache__",
    ".claude",
    ".cache",
    ".cargo",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "AppData",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
# Single source of truth: `io/directory_scanner.py` (item #154) -- keeps this file and
# `cli/bootstrap.py`'s front-door mirror from drifting out of sync.
_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD = BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
_BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD = BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
_BROAD_WORKSPACE_PROJECT_MARKERS = BROAD_WORKSPACE_PROJECT_MARKERS
_GUARDED_BROAD_ROOT_RG_GLOBS = (
    "!context/**",
    "!**/context/**",
    "!node_modules/**",
    "!**/node_modules/**",
    "!__pycache__/**",
    "!**/__pycache__/**",
    "!dist/**",
    "!**/dist/**",
    "!build/**",
    "!**/build/**",
)


def calibrate(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a structured JSON result, including a machine-readable "
        "calibration_status skip signal when GPU calibration cannot run, instead of the "
        "default human-readable output. Forwarded to the native tg binary; does not change "
        "the exit code. GPU calibration requires NVIDIA hardware and a CUDA-enabled "
        "native binary build.",
    ),
) -> None:
    """Measure CPU vs GPU crossover thresholds using the native Rust binary."""
    native_tg_binary = _self.resolve_native_tg_binary()
    if native_tg_binary is None:
        # audit L10: calibrate is unsupported without the native binary (and on CPU-only
        # boxes the native binary itself exits non-zero when CUDA is unavailable). tg's
        # convention is exit 1 for runtime/unsupported errors, not exit 2 (usage errors).
        # P0-4 (GPU Phase-0 honesty, #596) named a remediation here so this wasn't a dead end.
        # dogfood follow-up (v1.76.6): #596's "if published ... falls back to CPU when it
        # is not" framing still invited TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia + `tg
        # upgrade` as an obtainable GPU path, but no NVIDIA-enabled asset has ever shipped (the
        # release profile that builds one is held off) -- a permanent dead end dressed up as
        # honest advice. State the evergreen, structural fact instead (GPU needs a
        # CUDA-enabled build, which isn't present here) without claiming an upgrade will --or
        # won't-- fetch one; `tg doctor` is the live way to check once a binary exists.
        # #182 NIT-1: the v1.76.6 revision still name-dropped
        # TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia in a "confirm before relying on" aside --
        # dropped here so this Python wrapper matches the Rust side (crossover.rs), whose
        # detect_device_name test forbids that override as an obtainable path. This
        # wrapper uses inherited stdio for the real `calibrate` subprocess below and must NOT
        # capture its stderr (that would break streaming), so only THIS wrapper-owned
        # missing-binary message gets touched; the native binary's own calibrate-failure
        # remediation is Rust-owned (crossover.rs) and follows the same discipline there.
        # v20 dogfood (GPU honesty / harness-misread, additive): a --json caller gets a
        # structured stdout signal too, so a harness can tell "binary missing" apart from a
        # generic exit-1 failure the same way it can now tell the Rust side's no-cuda-build
        # skip apart from a genuine calibration failure. The human-readable stderr message
        # below is unchanged either way (still exit 1, still the same remediation text).
        if json_output:
            _safe_stdout_line(json.dumps({"calibration_status": "native_binary_unavailable"}))
        typer.echo(
            "Error: native tg binary not found for calibrate command.\n"
            "Run 'tg upgrade' to install the native tg binary that calibrate requires. GPU "
            "(CUDA) acceleration is experimental and is not shipped in any current build; run "
            "'tg doctor' after upgrading to check this install's native flavor.",
            err=True,
        )
        raise typer.Exit(1)

    from tensor_grep.cli.frontdoor_hops import child_env_or_refusal

    argv = [str(native_tg_binary), "calibrate"]
    if json_output:
        argv.append("--json")
    child_env, refusal = child_env_or_refusal()
    if child_env is None:
        raise typer.Exit(refusal)
    completed = subprocess.run(argv, check=False, env=child_env)
    raise typer.Exit(int(completed.returncode))


def devices(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit device inventory as JSON for automation.",
    ),
    format_type: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Print routable GPU device IDs and VRAM inventory."""
    import json

    from tensor_grep.core.hardware.device_inventory import collect_device_inventory

    normalized_format = format_type.lower().strip()
    if json_output:
        normalized_format = "json"
    if normalized_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be one of: text, json")

    inventory = collect_device_inventory()
    payload = inventory.to_dict()

    if normalized_format == "json":
        print(json.dumps(_with_schema_version(payload)))
        return

    if not inventory.devices:
        typer.echo("No routable GPUs detected.")
        return

    typer.echo(f"Detected {inventory.device_count} routable GPU(s):")
    for device in inventory.devices:
        typer.echo(f"- gpu:{device.device_id} vram_mb={device.vram_capacity_mb}")


def lsp(
    provider: str = typer.Option(
        "native",
        "--provider",
        help=(
            "Experimental semantic provider mode. native=repo-map only, "
            "lsp=external provider only, hybrid=merge both. Invalid modes "
            "fail before the server starts."
        ),
    ),
    debug_trace_language: str | None = typer.Option(
        None,
        "--debug-trace",
        help=(
            "Run a one-shot external-provider health probe for LANGUAGE and emit "
            "JSON-RPC trace diagnostics instead of starting the tg LSP server."
        ),
    ),
    path: Path = typer.Option(
        Path("."),
        "--path",
        help="Workspace root for --debug-trace probes.",
    ),
    probe_timeout_seconds: float | None = typer.Option(
        None,
        "--probe-timeout-seconds",
        help="Override the external-provider request timeout for --debug-trace.",
    ),
) -> None:
    """Start the structural search language server.

    Examples:
      tg lsp
      tg lsp --provider native
      tg lsp --provider lsp
      tg lsp --provider hybrid
      tg lsp --debug-trace python --path .

    External LSP providers are experimental semantic evidence. Provider
    availability means the binary was found, not that initialization or
    navigation requests have succeeded.

    The provider mode is also exposed to editor clients through the
    `TG_LSP_PROVIDER` environment variable.
    """

    normalized_provider = provider.strip().lower()
    if normalized_provider not in {"native", "lsp", "hybrid"}:
        typer.echo(
            "Unsupported LSP provider mode; expected one of: native, lsp, hybrid",
            err=True,
        )
        raise typer.Exit(code=2)
    if debug_trace_language is not None:
        from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager

        payload = ExternalLSPProviderManager().provider_debug_trace(
            language=debug_trace_language,
            workspace_root=path,
            probe_timeout_seconds=probe_timeout_seconds,
        )
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        status = cast(dict[str, Any], payload.get("status", {}))
        if status.get("health_status") != "ready":
            raise typer.Exit(code=1)
        return
    # The LSP server needs the optional `ast` extra (pygls/lsprotocol). Import it lazily and
    # fail closed with an actionable message rather than leaking a ModuleNotFoundError traceback
    # (item #159). Provider validation and --debug-trace above deliberately run first: they must
    # work on a bare `pip install tensor-grep` without the extra.
    try:
        from tensor_grep.cli.lsp_server import run_lsp
    except ImportError as exc:
        typer.echo(
            "tg lsp requires the optional 'ast' extra (language-server support). "
            'Install it with: pip install "tensor-grep[ast]"',
            err=True,
        )
        raise typer.Exit(code=1) from exc
    os.environ["TG_LSP_PROVIDER"] = normalized_provider
    run_lsp()


def lsp_setup(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    include_toolchain_providers: bool = typer.Option(
        False,
        "--include-toolchain-providers",
        help=(
            "Also install/copy rust-analyzer, gopls, and csharp-ls using local "
            "toolchains. Off by default to avoid mutating external toolchains during "
            "normal installs."
        ),
    ),
) -> None:
    """Install managed external LSP providers.

    Setup availability does not prove semantic navigation. Use
    `tg doctor --with-lsp --json` and inspect health_status / health_check plus
    navigation lsp_proof fields before treating LSP evidence as dependable.
    """
    from tensor_grep.cli.lsp_provider_setup import (
        install_managed_lsp_providers,
        supported_lsp_languages,
    )

    payload = install_managed_lsp_providers(
        python_executable=sys.executable,
        managed_root=None,
        include_toolchain_providers=include_toolchain_providers,
    )
    has_install_errors = bool(payload.get("install_errors"))
    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        if has_install_errors:
            raise typer.Exit(code=1)
        return
    if has_install_errors:
        typer.echo(
            f"Managed external LSP provider setup completed with errors under {payload['managed_provider_root']}"
        )
    else:
        typer.echo(
            f"Managed external LSP provider setup complete under {payload['managed_provider_root']}"
        )
    providers = cast(dict[str, dict[str, Any]], payload["providers"])
    for language in supported_lsp_languages():
        provider = providers.get(language, {})
        command = provider.get("command") or []
        source = provider.get("command_source", "missing")
        availability = "available" if provider.get("available") else "missing"
        command_text = " ".join(str(part) for part in command) if command else "missing"
        install_error = provider.get("install_error")
        suffix = f", error={install_error}" if install_error else ""
        typer.echo(f"  {language}: {command_text} [{source}, {availability}{suffix}]")
    if has_install_errors:
        raise typer.Exit(code=1)


def mcp_server() -> None:
    """Start the Model Context Protocol (MCP) server for AI assistants"""
    from tensor_grep.cli.mcp_server import run_mcp_server

    run_mcp_server()


def repair_launcher(
    allow_foreign_rename: bool = typer.Option(
        False,
        "--allow-foreign-rename",
        help=(
            "Move aside the first foreign Windows tg.exe selected by Python subprocess "
            "resolution and replace it with the managed tensor-grep native front door. "
            "Use only when you own that foreign command."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Repair Windows Python subprocess tg resolution.

    Removes verified or self-identifying tensor-grep Python Scripts entrypoints
    that shadow the managed native front door. Use --allow-foreign-rename only
    for a foreign tg.exe that you own and want tensor-grep to back up.
    """
    payload = _repair_windows_python_subprocess_launcher(allow_foreign_rename=allow_foreign_rename)
    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
    else:
        typer.echo(payload["message"])
        if payload.get("backup_path"):
            typer.echo(f"backup_path: {payload['backup_path']}")
        if payload.get("replaced_path"):
            typer.echo(f"replaced_path: {payload['replaced_path']}")
        if payload.get("post_repair_version"):
            typer.echo(f"post_repair_version: {payload['post_repair_version']}")

    if str(payload.get("status") or "").startswith(("blocked", "failed")):
        raise typer.Exit(code=1)


def doctor(
    path: str = typer.Argument(".", help="Workspace root to inspect."),
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    with_lsp: bool = typer.Option(
        True,
        "--with-lsp/--no-lsp",
        help="Include external LSP provider diagnostics; inspect health_status and health_check.",
    ),
    fast: bool = typer.Option(
        False, "--fast", help="Skip slow external LSP and PyPI freshness probes."
    ),
) -> None:
    """Print system, GPU, cache, AST, daemon, shell-escaping (PowerShell/cmd.exe literal patterns), and provider-proof diagnostics. Provider availability is not navigation proof."""
    payload = (
        _self._build_doctor_payload(path, config=config, with_lsp=False, fast=True)
        if fast
        else _self._build_doctor_payload(path, config=config, with_lsp=with_lsp)
    )
    typer.echo(json.dumps(payload, indent=2) if json_output else _render_doctor_payload(payload))


_INSTALL_DENSE_PACKAGE_SPEC = "tensor-grep[semantic]"


def _run_install_dense() -> dict[str, Any]:
    """Do the actual `tg install-dense` work: install the `semantic` extra via the same
    uv-tool -> uv pip -> pip cascade `tg upgrade` uses (`_upgrade_attempts`/`_run_upgrade`, module
    level above), then run the hardened, checksum-pinned, deadline-bounded model fetch
    (`retrieval_dense.fetch_dense_model` -- never hand-rolled here).

    Never raises: every failure mode (the pip cascade exhausted, a network error, a checksum
    mismatch, or the fetch's own wall-clock deadline) is captured into the returned payload so the
    command layer picks exit code + rendering uniformly for both `--json` and text mode. Fail-
    closed -- a failed fetch leaves no partial model directory, per `fetch_dense_model`'s own
    atomic verify-before-install contract (retrieval_dense.py); a failed pip step never attempts
    the network fetch at all (`fetch_model` is reported "skipped", not silently retried).
    """
    from tensor_grep.core.retrieval_dense import default_model_dir, fetch_dense_model

    steps: dict[str, dict[str, Any]] = {}

    try:
        attempts = _upgrade_attempts(_INSTALL_DENSE_PACKAGE_SPEC)
        result, method = _self._run_upgrade(attempts)
        output = "\n".join(
            part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
        )
        steps["pip_install"] = {"status": "ok", "method": method, "detail": output or None}
    except RuntimeError as exc:
        steps["pip_install"] = {"status": "failed", "method": None, "detail": str(exc)}
        steps["fetch_model"] = {
            "status": "skipped",
            "dir": None,
            "detail": "skipped: installing the semantic extra failed",
        }
        return {
            "ok": False,
            "steps": steps,
            "dense_model_dir": None,
            "message": (
                f"tg install-dense failed: could not install {_INSTALL_DENSE_PACKAGE_SPEC} "
                f"({steps['pip_install']['detail']})"
            ),
        }

    try:
        dest = fetch_dense_model()
    except Exception as exc:
        # `fetch_dense_model` documents BackendExecutionError for every download/checksum/deadline
        # failure (retrieval_dense.py), but this command boundary catches broadly so a lower-level
        # unwrapped OSError (e.g. permission denied creating the cache dir) also exits cleanly
        # with a message instead of a raw traceback -- fail-closed never means fail-silent.
        steps["fetch_model"] = {
            "status": "failed",
            "dir": str(default_model_dir()),
            "detail": str(exc),
        }
        return {
            "ok": False,
            "steps": steps,
            "dense_model_dir": None,
            "message": f"tg install-dense failed: dense model fetch failed ({exc})",
        }

    steps["fetch_model"] = {"status": "ok", "dir": str(dest), "detail": None}
    return {
        "ok": True,
        "steps": steps,
        "dense_model_dir": str(dest),
        "message": f"tg install-dense complete: the dense semantic leg is ready (model at {dest}).",
    }


def install_dense(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    reranker: bool = typer.Option(
        False, "--reranker", help="Also install the pinned native CPU cross-encoder assets."
    ),
) -> None:
    """One-shot install of the `tg find` / `tg search --semantic` dense-embedding leg.

    Installs the `semantic` extra (model2vec + numpy -- pure CPU/numpy, no torch or GPU
    dependency) via the same uv-tool -> uv pip -> pip cascade `tg upgrade` uses, then fetches the
    checksum-pinned potion-code-16M model (~65MB) to the local model cache
    (`~/.tensor-grep/models/potion-code-16M`, or `TG_SEMANTIC_MODEL_DIR` if set). Never runs
    automatically -- `tg find` and `tg search --semantic` degrade visibly to BM25-only until this
    has been run once. On any failure (pip install failure, network error, or a checksum mismatch
    against the pinned HuggingFace revision) this exits non-zero with a clear message and leaves
    no partial model directory behind.
    """
    payload = _self._run_install_dense()
    if reranker and payload["ok"]:
        from tensor_grep.backends.base import BackendExecutionError
        from tensor_grep.core.cross_encoder_assets import fetch_cross_encoder_assets

        try:
            destination = fetch_cross_encoder_assets()
            payload["steps"]["fetch_reranker"] = {"status": "ok", "dir": str(destination)}
            payload["reranker_dir"] = str(destination)
            payload["message"] += f" Native cross-encoder assets ready at {destination}."
        except BackendExecutionError as exc:
            payload["steps"]["fetch_reranker"] = {"status": "failed", "detail": str(exc)}
            payload["ok"] = False
            payload["message"] = f"tg install-dense failed: cross-encoder install failed ({exc})"
    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
    else:
        typer.echo(payload["message"])
        pip_step = payload["steps"].get("pip_install", {})
        typer.echo(f"  pip_install: {pip_step.get('status')} (method={pip_step.get('method')})")
        fetch_step = payload["steps"].get("fetch_model", {})
        typer.echo(f"  fetch_model: {fetch_step.get('status')} (dir={fetch_step.get('dir')})")
        if pip_step.get("status") == "failed" and pip_step.get("detail"):
            typer.echo(f"  pip_install detail: {pip_step['detail']}", err=True)
        if fetch_step.get("status") == "failed" and fetch_step.get("detail"):
            typer.echo(f"  fetch_model detail: {fetch_step['detail']}", err=True)
    if not payload["ok"]:
        raise typer.Exit(code=1)


def install_command(
    target: str = typer.Option(
        "all",
        "--target",
        "-t",
        help="Agent target to configure: claude, cursor, codex, opencode, qwen, or all.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate configuration without writing files."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm installation without prompting."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Configure AI coding agents to use tensor-grep's built-in MCP server."""
    from tensor_grep.cli.agent_installer import install_command as _impl

    _impl(target=target, dry_run=dry_run, yes=yes, json_output=json_output)


def uninstall_command(
    target: str = typer.Option(
        "all",
        "--target",
        "-t",
        help="Agent target to remove: claude, cursor, codex, opencode, qwen, or all.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate removal without writing files."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Confirm uninstallation without prompting."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Remove tensor-grep MCP integration and search guidance from AI coding agents."""
    from tensor_grep.cli.agent_installer import uninstall_command as _impl

    _impl(target=target, dry_run=dry_run, yes=yes, json_output=json_output)


def update() -> None:
    """Alias for upgrade."""
    _self.upgrade()


def worker(
    port: int | None = typer.Option(None, "--port", help="Port to bind the TCP worker."),
    stop: bool = typer.Option(False, "--stop", help="Stop the active resident worker."),
) -> None:
    """Internal command to manage the experimental Resident AST Worker."""
    native_tg_binary = _self.resolve_native_tg_binary()
    if native_tg_binary is None:
        typer.echo("Error: native tg binary not found for worker command.", err=True)
        raise typer.Exit(2)

    cmd = [str(native_tg_binary), "worker"]
    if port is not None:
        cmd.extend(["--port", str(port)])
    if stop:
        cmd.append("--stop")

    from tensor_grep.cli.frontdoor_hops import child_env_or_refusal

    child_env, refusal = child_env_or_refusal()
    if child_env is None:
        raise typer.Exit(refusal)
    completed = subprocess.run(cmd, check=False, env=child_env)
    raise typer.Exit(int(completed.returncode))


def register(app: typer.Typer) -> None:
    app.command()(calibrate)
    app.command()(devices)
    app.command()(lsp)
    app.command(name="lsp-setup")(lsp_setup)
    app.command(name="mcp")(mcp_server)
    app.command(name="repair-launcher")(repair_launcher)
    app.command()(doctor)
    app.command(name="install-dense")(install_dense)
    app.command(name="install")(install_command)
    app.command(name="uninstall")(uninstall_command)
    app.command("update")(update)
    app.command(hidden=True)(worker)
