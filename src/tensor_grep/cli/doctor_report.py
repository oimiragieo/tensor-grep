"""`tg doctor`: every probe that feeds the diagnostics payload, and the text renderer.

Split out of `cli/main.py` (see `docs/design/2026-08-19-split-floor-escape.md`). Holds the
whole `_doctor_*` probe family -- installation health, PATH/launcher shadowing, the LSP and
ast-grep provider probes, GPU tier and runtime probes, cache and daemon status -- plus
`_build_doctor_payload` and `_render_doctor_payload`. The `doctor` COMMAND itself stays in
`main.py`; only its evidence gathering moved.

`_self` is `cli/main.py`'s module object, imported from `cli/_main_binding`. Every reference
here to a symbol that still lives in `main.py` goes through it -- including `_self.subprocess`,
which the test suite patches on `main` to stub out `tg --version` probes. Read that module's
docstring before adding a bare cross-module reference.

One exception is deliberate: `_doctor_rust_binary_version` keeps its FUNCTION-LOCAL
`import subprocess` and a bare `subprocess.run`. A local import shadows the module global, so
that call never saw `monkeypatch.setattr(main, "subprocess", ...)` before the move either;
rewriting it to `_self.subprocess` would have newly exposed it to the patch.

`__file__` is load-bearing in `_doctor_native_tg_binary_kind` (`parents[3]` = the repo root).
This module sits in the same directory as `main.py`, so the depth is unchanged by the move.
"""

import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tensor_grep.cli._main_binding import _self as _self
from tensor_grep.cli.runtime_paths import (
    gpu_probe_timeout_s,
    iter_in_tree_native_tg_binaries,
)


def _doctor_installed_version() -> str:
    return _self._cli_package_version()


def _doctor_session_daemon_autostart_status() -> str:
    """v1.92.1 dogfood item 5: human-readable autostart posture for a STOPPED daemon.

    `session_daemon.running: false` on a cold box (nothing has ever warmed a daemon for this
    root) reads as broken even though the Tier-1 fast path
    (`_session_daemon_autostart_enabled`, defined further below in this module) will spin one up
    transparently, non-blocking, on the very next defs/impact/refs/callers/blast-radius call.
    Reuses that SAME gate function -- the one the real autostart dispatch actually checks -- so
    this string can never drift from the runtime behavior it describes into a new lie of its
    own (e.g. claiming "on-first-use" while an operator has explicitly disabled autostart).
    """
    if _self._session_daemon_autostart_enabled():
        return "on-first-use (not yet warmed)"
    return "disabled (TG_SESSION_DAEMON_AUTOSTART is off, or CI was detected)"


def _doctor_session_daemon_status(path: str) -> dict[str, Any]:
    from tensor_grep.cli.session_daemon import get_session_daemon_status

    status = get_session_daemon_status(path)
    if not status.get("running"):
        # Additive-only field, present only in the not-running state -- mirrors the
        # conditional `install_hint` precedent (_doctor_ast_grep_status/_doctor_dense_model_
        # status below) rather than always emitting a field that is meaningless once warm.
        status["autostart"] = _doctor_session_daemon_autostart_status()
    return status


def _upgrade_running_session_daemon_snapshot(path: str = ".") -> dict[str, Any] | None:
    try:
        status = _self._doctor_session_daemon_status(path)
    except Exception:
        return None
    if status.get("running") is not True:
        return None
    root = str(status.get("root") or "").strip()
    if not root:
        return None
    return {"root": root}


def _restart_session_daemon_after_upgrade(snapshot: dict[str, Any] | None) -> str | None:
    if not snapshot:
        return None
    root = str(snapshot.get("root") or "").strip()
    if not root:
        return None
    status_probe_error: str | None = None
    try:
        current = _self._doctor_session_daemon_status(root)
    except Exception as exc:
        # A73 hardening (2026-08-20, codex REVISE on PR #1068): `current` is a purely local
        # variable, so a probe failure here used to be silently swallowed the moment the
        # restart below happened to succeed -- the returned message read as a clean success
        # with no trace that the pre-restart status could not be determined. Disclose it on
        # the returned payload instead of discarding it.
        current = {"running": False, "status_error": str(exc)}
        status_probe_error = str(exc)
    if current.get("running") is True:
        return None
    try:
        from tensor_grep.cli.session_daemon import start_session_daemon

        started = start_session_daemon(root)
    except Exception as exc:
        suffix = (
            f" (pre-restart status probe also failed: {status_probe_error})"
            if status_probe_error
            else ""
        )
        return (
            f"WARNING: session daemon was running before upgrade but restart failed for "
            f"{root}: {exc}{suffix}"
        )
    if started.get("running") is True:
        if status_probe_error:
            return (
                f"Session daemon restarted after upgrade for {root} (note: the pre-restart "
                f"status check failed and could not confirm whether a restart was actually "
                f"needed: {status_probe_error})."
            )
        return f"Session daemon restarted after upgrade for {root}."
    suffix = (
        f" (pre-restart status probe also failed: {status_probe_error})"
        if status_probe_error
        else ""
    )
    return (
        f"WARNING: session daemon was running before upgrade but did not restart for "
        f"{root}{suffix}."
    )


def _doctor_lsp_languages() -> list[str]:
    from tensor_grep.cli.lsp_provider_setup import supported_lsp_languages

    return supported_lsp_languages()


def _doctor_lsp_probe_timeout_seconds() -> float:
    raw_timeout = os.environ.get(_self._DOCTOR_LSP_PROBE_TIMEOUT_ENV)
    if raw_timeout:
        try:
            parsed_timeout = float(raw_timeout)
        except ValueError:
            parsed_timeout = 0.0
        if parsed_timeout > 0:
            return parsed_timeout
    if sys.platform.startswith("win"):
        return _self._DOCTOR_LSP_WINDOWS_PROBE_TIMEOUT_SECONDS
    return _self._DOCTOR_LSP_PROBE_TIMEOUT_SECONDS


def _doctor_lsp_provider_statuses(path: str) -> list[dict[str, Any]]:
    from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager

    manager = ExternalLSPProviderManager()
    workspace_root = Path(path).resolve()
    probe_timeout_seconds = _doctor_lsp_probe_timeout_seconds()
    try:
        return [
            manager.provider_status(
                language=language,
                workspace_root=workspace_root,
                verify_health=True,
                probe_timeout_seconds=probe_timeout_seconds,
            )
            for language in _doctor_lsp_languages()
        ]
    finally:
        manager.stop_all()


_DOCTOR_LSP_WORKSPACE_ERROR_MARKERS = (
    "fetchworkspaceerror",
    "failed to fetch workspace",
    "workspace was not loaded",
    "no workspace folder",
    "could not load workspace",
    "rooturi",
)


def _doctor_lsp_workspace_error_lines(stderr_lines: list[str]) -> list[str]:
    """Return stderr lines that indicate a workspace/fetch failure."""
    matches: list[str] = []
    for raw in stderr_lines:
        line = str(raw)
        lowered = line.lower()
        if any(marker in lowered for marker in _DOCTOR_LSP_WORKSPACE_ERROR_MARKERS):
            matches.append(line)
    return matches


def _doctor_downgrade_lsp_workspace_proof(provider: dict[str, Any]) -> dict[str, Any]:
    """Demote a workspace-blind ``lsp_proof`` claim (audit M10).

    The managed health probe issues a single-file ``documentSymbol`` request, which a
    language server happily answers even when its workspace failed to load (e.g.
    rust-analyzer emitting ``FetchWorkspaceError``). The provider then reports
    ``lsp_proof:true`` while suppressing the very stderr tail that proves cross-file
    navigation is degraded. When the surfaced stderr names a workspace/fetch error, drop
    ``lsp_proof`` to ``false``, expose a ``workspace_warning``, and un-suppress the
    offending stderr lines so the JSON is honest instead of over-claiming.
    """
    if not provider.get("lsp_proof"):
        return provider
    surfaced = [str(item) for item in provider.get("provider_recent_stderr") or [] if str(item)]
    workspace_lines = _doctor_lsp_workspace_error_lines(surfaced)
    if not workspace_lines:
        return provider
    updated = dict(provider)
    updated["lsp_proof"] = False
    updated["lsp_workspace_ready"] = False
    updated["workspace_warning"] = (
        "Single-file documentSymbol probe succeeded, but the provider reported a "
        "workspace/fetch error, so cross-file navigation is not proven. Treat lsp_proof "
        "as degraded until the workspace loads cleanly."
    )
    updated.setdefault(
        "not_lsp_proof_reason",
        "Provider answered the single-file probe but its workspace failed to load "
        "(see provider_recent_stderr); cross-file navigation is unproven.",
    )
    # Stop hiding the evidence: restore the workspace-error lines to stderr_tail and clear
    # the suppression flag that previously masked them.
    existing_tail = [str(item) for item in updated.get("stderr_tail") or [] if str(item)]
    merged_tail = existing_tail + [line for line in workspace_lines if line not in existing_tail]
    updated["stderr_tail"] = merged_tail[-50:]
    updated["stderr_tail_suppressed"] = False
    return updated


def _doctor_apply_lsp_workspace_warnings(
    providers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [_doctor_downgrade_lsp_workspace_proof(provider) for provider in providers]


_DOCTOR_RUST_ANALYZER_MISSING_COMPONENT_TOOLCHAIN_RE = re.compile(r"toolchain '([^']+)'")


def _doctor_lsp_missing_rust_analyzer_component_lines(stderr_lines: list[str]) -> list[str]:
    """Return stderr lines matching rustup's missing-component proxy fingerprint.

    When the ``rust-analyzer`` rustup component is not installed for the active toolchain,
    the rustup proxy binary at ``~/.cargo/bin/rust-analyzer`` still spawns successfully (the
    process starts), but immediately exits, printing e.g. ``error: unknown binary
    'rust-analyzer' in toolchain '1.96.0-x86_64-pc-windows-msvc'`` to stderr. Requiring BOTH
    markers keeps this narrow: it must not fire on other rust-analyzer stderr noise that
    happens to mention "toolchain" or "binary" alone.
    """
    matches: list[str] = []
    for raw in stderr_lines:
        line = str(raw)
        lowered = line.lower()
        if "unknown binary" in lowered and "rust-analyzer" in lowered:
            matches.append(line)
    return matches


def _doctor_rust_analyzer_missing_component_remediation(stderr_lines: list[str]) -> str | None:
    """Return the exact ``rustup component add`` remediation, or None when rustup's missing-
    component fingerprint (see ``_doctor_lsp_missing_rust_analyzer_component_lines``) is absent
    from ``stderr_lines``. The toolchain, when parseable from the matched line, is threaded into
    ``--toolchain`` so the remediation is copy-pasteable as-is; otherwise this falls back to the
    plain (no-toolchain) form rather than emitting a broken ``--toolchain`` with no value.
    """
    matches = _doctor_lsp_missing_rust_analyzer_component_lines(stderr_lines)
    if not matches:
        return None
    toolchain: str | None = None
    for line in matches:
        found = _DOCTOR_RUST_ANALYZER_MISSING_COMPONENT_TOOLCHAIN_RE.search(line)
        if found:
            toolchain = found.group(1)
            break
    command = (
        f"rustup component add rust-analyzer --toolchain {toolchain}"
        if toolchain
        else "rustup component add rust-analyzer"
    )
    return (
        f"Missing rustup component: run `{command}` "
        "(or `tg lsp-setup --include-toolchain-providers` to let tensor-grep manage it)."
    )


def _doctor_apply_lsp_rust_analyzer_remediation(provider: dict[str, Any]) -> dict[str, Any]:
    """Append the rustup component-add remediation to a rust provider's surfaced guidance when
    its stderr matches the missing-component fingerprint. Narrow by design: only
    ``language == "rust"`` and only this one fingerprint are touched -- every other language and
    every other error shape passes through unchanged (this must never become a generic error
    rewriter)."""
    if str(provider.get("language", "")).strip().lower() != "rust":
        return provider
    stderr_lines = [
        str(item)
        for item in (
            list(provider.get("stderr_tail") or [])
            + list(provider.get("provider_recent_stderr") or [])
            + [provider.get("last_error")]
        )
        if item
    ]
    remediation = _doctor_rust_analyzer_missing_component_remediation(stderr_lines)
    if remediation is None:
        return provider
    updated = dict(provider)
    existing_reason = str(updated.get("not_lsp_proof_reason") or "").strip()
    if remediation in existing_reason:
        return updated  # already applied -- idempotent, avoids duplicate text on repeat calls
    updated["not_lsp_proof_reason"] = (
        f"{existing_reason} {remediation}" if existing_reason else remediation
    )
    updated["lsp_missing_component_remediation"] = remediation
    return updated


def _doctor_apply_lsp_missing_component_remediation(
    providers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [_doctor_apply_lsp_rust_analyzer_remediation(provider) for provider in providers]


def _doctor_lsp_providers_by_language(
    providers: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for provider in providers:
        language = str(provider.get("language", "")).strip()
        if not language:
            continue
        entry = dict(provider)
        if "health" not in entry and "health_status" in entry:
            entry["health"] = entry["health_status"]
        keyed[language] = entry
    return keyed


def _doctor_ast_grep_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "schema_version": 1,
        "available": False,
        "binary": None,
        "wrapper_backend": "AstGrepWrapperBackend",
        "required_for": "tg run ast-grep semantic options",
        "semantic_run_options": ["--selector", "--strictness", "--stdin", "--globs"],
        "timeout_env": "TG_AST_GREP_TIMEOUT_SECONDS",
        "timeout_seconds": None,
    }
    try:
        from tensor_grep.backends.ast_wrapper_backend import (
            AstGrepWrapperBackend,
            _ast_grep_command_timeout_seconds,
        )

        backend = AstGrepWrapperBackend()
        binary = backend._get_binary_name()
        available = backend.is_available()
        status["available"] = available
        status["binary"] = binary if available else None
        status["timeout_seconds"] = _ast_grep_command_timeout_seconds()
        if not available:
            status["install_hint"] = (
                "Install ast-grep or put an ast-grep/sg binary on PATH to use "
                "tg run --selector, --strictness, --stdin, or --globs."
            )
    except Exception as exc:
        status["error"] = str(exc)
    return status


def _doctor_dense_model_status() -> dict[str, Any]:
    """CEO#7: has the `tg find` / `tg search --semantic` dense-embedding leg been fetched?

    A pure filesystem check (mirrors `load_dense_model`'s own "is it fetched" test -- does the
    directory exist -- rather than actually loading the model, so this stays cheap for `tg doctor`)
    reported next to `ast_grep`/`resident_worker`'s optional-capability shape."""
    from tensor_grep.core.retrieval_dense import default_model_dir

    model_dir = default_model_dir()
    fetched = model_dir.is_dir()
    status: dict[str, Any] = {
        "schema_version": 1,
        "fetched": fetched,
        "dir": str(model_dir),
    }
    if not fetched:
        status["install_hint"] = "run `tg install-dense` to fetch the dense-embedding model"
    return status


def _doctor_rust_core_extension_available() -> bool:
    try:
        from tensor_grep.backends.rust_backend import HAVE_RUST
    except Exception:
        return False
    return bool(HAVE_RUST)


def _doctor_rust_binary_version(native_tg_binary: Path | None) -> str | None:
    if not native_tg_binary:
        return None
    try:
        import subprocess

        res = subprocess.run(
            [str(native_tg_binary), "--version"], capture_output=True, text=True, timeout=2
        )
        if res.returncode == 0:
            return res.stdout.strip()
        return None
    except Exception:
        return None


def _doctor_rust_binary_version_matches(
    expected_version: str, rust_binary_version: str | None
) -> bool | None:
    if rust_binary_version is None:
        return None
    return _self._native_tg_version_matches(expected_version, rust_binary_version)


def _doctor_tg_version_looks_like_tensor_grep(version_text: str | None) -> bool:
    if not version_text:
        return False
    stripped = version_text.strip().lower()
    return stripped.startswith("tg ") or stripped.startswith("tensor-grep ")


def _doctor_native_tg_binary_kind(native_tg_binary: Path | None) -> str:
    if native_tg_binary is None:
        return "missing"

    repo_root = Path(__file__).resolve().parents[3]
    try:
        relative = native_tg_binary.resolve().relative_to(repo_root.resolve())
    except (OSError, ValueError):
        return "standalone-executable"

    parts = tuple(part.lower() for part in relative.parts)
    if len(parts) >= 4 and parts[:2] == ("rust_core", "target"):
        if parts[2] == "debug":
            return "in-tree-debug"
        if parts[2] == "release":
            return "in-tree-release"
        return "in-tree-target"
    return "standalone-executable"


def _doctor_rust_binary_version_status(
    *,
    native_tg_binary_kind: str,
    rust_binary_version: str | None,
    rust_binary_version_matches: bool | None,
) -> str:
    if rust_binary_version is None:
        return "missing"
    if rust_binary_version_matches is True:
        return "matches"
    if native_tg_binary_kind.startswith("in-tree-"):
        return "stale"
    return "mismatch"


def _doctor_skipped_native_tg_binaries(
    expected_version: str,
    selected_binary: Path | None,
) -> list[dict[str, str | None]]:
    skipped: list[dict[str, str | None]] = []
    try:
        selected_resolved = selected_binary.resolve() if selected_binary is not None else None
    except OSError:
        selected_resolved = selected_binary

    for candidate in iter_in_tree_native_tg_binaries():
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if selected_resolved is not None and resolved == selected_resolved:
            continue
        version = _self._native_tg_version(resolved)
        version_matches = _self._native_tg_version_matches(expected_version, version)
        if version_matches:
            continue
        skipped.append({
            "path": str(resolved),
            "kind": _doctor_native_tg_binary_kind(resolved),
            "version": version,
            "version_status": "stale" if version is not None else "unknown",
        })
    return skipped


def _doctor_rust_binary_remediation(
    *,
    rust_binary_version_status: str,
    native_tg_binary_kind: str,
) -> str | None:
    if (
        rust_binary_version_status == "stale" and native_tg_binary_kind.startswith("in-tree-")
    ) or rust_binary_version_status == "stale-skipped":
        return (
            "Rebuild the in-tree native tg binary, for example "
            "`C:/Users/oimir/.cargo/bin/cargo.exe build --manifest-path rust_core/Cargo.toml "
            "--release`, or set TG_NATIVE_TG_BINARY to opt in to a specific native binary."
        )
    if rust_binary_version_status == "mismatch":
        return "Set TG_NATIVE_TG_BINARY to the intended release binary or refresh the tg install."
    return None


def _doctor_version_tuple(version_text: str | None) -> tuple[int, ...] | None:
    """Semantic version comparison key (A90 PATH-honesty). Strips a 'tg '/'tensor-grep '
    prefix if present, then parses a PURE dotted-numeric release (`1.110.13` -> (1,110,13) so
    1.110.9 < 1.110.10 numerically). STRICT by design (codex HIGH): ANY trailing/embedded
    non-dotted-numeric content — `+dev`, `-rc1`, `rc1`, `.post1`, a `v` prefix — is REJECTED and
    yields None, so a prerelease/local build is treated as UNVERIFIABLE (fail-closed), never
    silently truncated into a stable-looking tuple. Returns None on any unparseable value;
    callers MUST treat None as 'unverifiable', never as a confident comparison."""
    if not version_text:
        return None
    stripped = version_text.strip()
    for prefix in ("tensor-grep ", "tg "):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
            break
    if not stripped:
        return None
    if not all(ch.isdigit() or ch == "." for ch in stripped):
        return None
    if stripped.startswith(".") or stripped.endswith(".") or ".." in stripped:
        return None
    if stripped.count(".") > 8:
        return None
    parts: list[int] = []
    for segment in stripped.split("."):
        if not segment:
            return None
        try:
            parts.append(int(segment))
        except ValueError:
            return None
    if len(parts) < 2:
        # A single-segment "version" is not a real release tuple (e.g. "9", "0"); the harness
        # always emits X.Y(.Z...) — treat short forms as unverifiable rather than comparable.
        return None
    return tuple(parts)


def _doctor_version_compare(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    """PEP-440-style padded comparison: `1.0` equals `1.0.0` (missing trailing segments are 0).
    Returns -1/0/1. Both tuples must already be valid (non-None)."""
    width = max(len(a), len(b))
    av = a + (0,) * (width - len(a))
    bv = b + (0,) * (width - len(b))
    if av < bv:
        return -1
    if av > bv:
        return 1
    return 0


def _doctor_installed_behind_pypi(installed_version: str, pypi_latest: str | None) -> bool | None:
    """Semantic `installed < pypi_latest` (padded: 1.0 == 1.0.0). None when either version is
    unparseable OR pypi_latest is unavailable (probe failed) — never a confident False on an
    unverifiable comparison (A90 plan REV 5, codex must-fix: no fallthrough to ok)."""
    if pypi_latest is None:
        return None
    installed_t = _doctor_version_tuple(installed_version)
    pypi_t = _doctor_version_tuple(pypi_latest)
    if installed_t is None or pypi_t is None:
        return None
    return _doctor_version_compare(installed_t, pypi_t) < 0


def _doctor_route_version_matches(installed_version: str, route_version: str | None) -> bool | None:
    """Semantic twin for shadow_launchers[].version_matches (A90 codex HIGH): returns
    - None when the route version is ABSENT (no route binary) or UNPARSEABLE (invalid) —
      never a confident False on an invalid version;
    - True when the padded semantic tuples are equal (1.0 == 1.0.0);
    - False when they differ (e.g. a shadowed old 1.110.10 vs installed 1.110.13).
    Does NOT reuse the substring matcher (which would call an invalid version a confident
    mismatch and treat 1.0 != 1.0.0 as a mismatch)."""
    if route_version is None:
        return None
    installed_t = _doctor_version_tuple(installed_version)
    route_t = _doctor_version_tuple(route_version)
    if installed_t is None or route_t is None:
        return None
    return _doctor_version_compare(installed_t, route_t) == 0


_ROUTE_ORDER = {"path": 0, "fresh_shell_path": 1, "python_subprocess_path": 2}


def _doctor_shadow_launchers(
    routes: list[dict[str, str | bool | None]],
) -> list[dict[str, str | bool | None]]:
    """Consolidated list of launcher routes where the resolved tg is foreign, its version does
    not match the installed wheel, or its version is unverifiable. A route is listed iff
    `foreign OR version_matches is False OR version_matches is None`; the null contract == the
    inclusion predicate (codex REV-4/5). ABSENT ROUTES are filtered out BEFORE the inclusion
    predicate (a path=None route is NOT 'unparseable', it simply is not listed). Deterministic
    order: path, fresh_shell_path, python_subprocess_path (codex REV-5 LOW, explicit rank)."""
    listed: list[dict[str, str | bool | None]] = []
    for entry in routes:
        if not entry.get("path"):
            continue
        foreign = bool(entry.get("foreign"))
        version_matches = entry.get("version_matches")
        if foreign or version_matches is False or version_matches is None:
            listed.append(entry)
    listed.sort(key=lambda e: _ROUTE_ORDER.get(str(e.get("route", "")), 99))
    return listed


def _doctor_installation_health(
    shadow_launchers: list[dict[str, str | bool | None]],
    *,
    installed_version: str,
    installed_behind_pypi: bool | None,
    pypi_unavailable: bool,
    pypi_latest: str | None = None,
) -> str:
    """Aggregate installation health (A90 PATH-honesty). Precedence:
    foreign_launcher > unverifiable_version > launcher_version_mismatch > stale_install >
    unknown_pypi > ok. ANY unverifiable version (invalid installed, invalid pypi_latest, or
    invalid present-route version) lands on unverifiable_version, never ok (codex must-fix
    REV-4: an invalid NON-NULL pypi_latest must not fall through to ok — hence pypi_latest is
    parsed here when provided)."""
    if any(bool(entry.get("foreign")) for entry in shadow_launchers):
        return "foreign_launcher"
    if _doctor_version_tuple(installed_version) is None or _any_route_unverifiable(
        shadow_launchers
    ):
        return "unverifiable_version"
    if pypi_latest is not None and _doctor_version_tuple(pypi_latest) is None:
        # An invalid NON-NULL pypi_latest: pypi_unavailable is False (the probe returned a
        # value), but that value is unparseable — cannot certify freshness -> unverifiable.
        return "unverifiable_version"
    if any(entry.get("version_matches") is False for entry in shadow_launchers):
        return "launcher_version_mismatch"
    if installed_behind_pypi is True:
        return "stale_install"
    if pypi_unavailable:
        return "unknown_pypi"
    return "ok"


def _any_route_unverifiable(
    shadow_launchers: list[dict[str, str | bool | None]],
) -> bool:
    """True when any listed route carries an unparseable version (version_matches is None), or
    the route's own raw version fails to parse. Used to force unverifiable_version when we
    cannot certify health."""
    for entry in shadow_launchers:
        if entry.get("version_matches") is None:
            return True
        raw = str(entry.get("version") or "")
        if raw and _doctor_version_tuple(raw) is None:
            return True
    return False


def _doctor_rust_binary_warning(
    *,
    expected_version: str,
    rust_binary_version: str | None,
    rust_binary_version_status: str,
    skipped_native_tg_binaries: list[dict[str, str | None]] | None = None,
) -> str | None:
    if rust_binary_version_status == "stale-skipped":
        skipped = skipped_native_tg_binaries or []
        if skipped:
            first = skipped[0]
            return (
                "ignored stale in-tree native tg binary: "
                f"expected {expected_version}, found {first.get('version') or 'unknown'} "
                f"at {first.get('path')}"
            )
        return f"ignored stale in-tree native tg binary: expected {expected_version}"
    if rust_binary_version_status == "stale":
        return (
            "in-tree native tg binary is stale: "
            f"expected {expected_version}, found {rust_binary_version or 'unknown'}"
        )
    if rust_binary_version_status == "mismatch":
        return (
            "native tg binary version mismatch: "
            f"expected {expected_version}, found {rust_binary_version or 'unknown'}"
        )
    return None


def _doctor_tg_candidate_version(candidate: Path) -> str | None:
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    try:
        res = _self.subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            env=env,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


_DOCTOR_VERSION_NOT_PROVIDED = object()


def _doctor_tg_launcher_kind(
    path: str | None,
    version_text: str | object | None = _DOCTOR_VERSION_NOT_PROVIDED,
) -> str | None:
    if not path:
        return None
    if version_text is not _DOCTOR_VERSION_NOT_PROVIDED and not isinstance(version_text, str):
        return "foreign"
    if isinstance(version_text, str) and not _doctor_tg_version_looks_like_tensor_grep(
        version_text
    ):
        return "foreign"

    candidate = Path(path)
    suffix = candidate.suffix.lower()
    parts = tuple(part.lower() for part in candidate.parts)
    if suffix in {".cmd", ".bat"}:
        return "cmd-shim"
    if suffix == ".ps1":
        return "powershell-shim"
    if suffix in {".com", ".exe"}:
        if ".tensor-grep" in parts and "bin" in parts:
            return "managed-native"
        if suffix == ".exe" and isinstance(version_text, str) and version_text.startswith("tg "):
            return "native-exe"
        if "scripts" in parts and (
            suffix == ".exe"
            and (
                ".venv" in parts
                or "venv" in parts
                or any(part.startswith("python") for part in parts)
            )
        ):
            return "python-entrypoint"
        return "native-exe"
    if candidate.name.lower() == "tg":
        if ".tensor-grep" in parts and "bin" in parts:
            return "managed-native"
        if sys.platform.startswith("win"):
            return "bash-shim"
        return "native-exe"
    return "unknown"


def _doctor_windows_registry_path_value(root: Any, subkey: str) -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _value_type = winreg.QueryValueEx(key, "Path")
    except OSError:
        return None
    if not isinstance(value, str) or not value:
        return None
    return os.path.expandvars(value)


def _doctor_fresh_shell_path_value() -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    machine_path = _doctor_windows_registry_path_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    )
    user_path = _doctor_windows_registry_path_value(winreg.HKEY_CURRENT_USER, "Environment")
    parts: list[str] = []
    for value in (machine_path, user_path):
        if value:
            parts.extend(entry for entry in value.split(";") if entry)
    if not parts:
        return None
    return ";".join(parts)


def _doctor_path_list_separator(path_value: str) -> str:
    if not sys.platform.startswith("win"):
        return os.pathsep
    if ";" in path_value or re.search(r"(?:^|;)[A-Za-z]:[\\/]", path_value):
        return ";"
    return os.pathsep


def _doctor_path_tg_candidates(path_value: str | None = None) -> list[dict[str, str | None]]:
    if sys.platform.startswith("win"):
        raw_exts = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
        extensions = [ext.lower() for ext in raw_exts.split(";") if ext]
        if not extensions:
            extensions = [".com", ".exe", ".bat", ".cmd"]
        names = [f"tg{ext}" for ext in extensions]
        names.append("tg")
        # PowerShell can resolve script commands even when .PS1 is not in PATHEXT.
        # Include it as a non-primary candidate so doctor can flag MCP/stdio traps.
        if ".ps1" not in extensions:
            names.append("tg.ps1")
    else:
        names = ["tg"]

    candidates: list[dict[str, str | None]] = []
    seen: set[str] = set()
    path_to_scan = os.environ.get("PATH", "") if path_value is None else path_value
    for entry in path_to_scan.split(_doctor_path_list_separator(path_to_scan)):
        if not entry:
            continue
        directory = Path(entry)
        for name in names:
            candidate = directory / name
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            key = str(resolved).lower() if sys.platform.startswith("win") else str(resolved)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "path": str(resolved),
                "version": _self._doctor_tg_candidate_version(resolved),
            })
    return candidates


def _doctor_python_subprocess_path_tg_candidate(
    path_value: str | None = None,
) -> dict[str, str | None] | None:
    path_to_scan = os.environ.get("PATH", "") if path_value is None else path_value
    if sys.platform.startswith("win"):
        names = ["tg.exe"]
    else:
        names = ["tg"]

    seen: set[str] = set()
    for entry in path_to_scan.split(_doctor_path_list_separator(path_to_scan)):
        if not entry:
            continue
        directory = Path(entry)
        for name in names:
            candidate = directory / name
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            key = str(resolved).lower() if sys.platform.startswith("win") else str(resolved)
            if key in seen:
                continue
            seen.add(key)
            return {
                "path": str(resolved),
                "version": _self._doctor_tg_candidate_version(resolved),
            }
    return None


def _doctor_fresh_shell_path_tg_candidates() -> list[dict[str, str | None]]:
    fresh_path_value = _self._doctor_fresh_shell_path_value()
    if not fresh_path_value:
        return []
    return _self._doctor_path_tg_candidates(fresh_path_value)


def _doctor_path_tg_launcher_warning(
    *,
    current_kind: str | None,
    current_path: str | None,
    fresh_kind: str | None,
    fresh_path: str | None,
) -> str | None:
    compatibility_kinds = {"bash-shim", "cmd-shim", "powershell-shim", "python-entrypoint"}
    native_kinds = {"managed-native", "native-exe"}
    if current_kind in compatibility_kinds and fresh_kind in native_kinds:
        return (
            "current process PATH resolves a compatibility shim before the managed native "
            f"front door ({current_path}); fresh-shell PATH resolves {fresh_path}. "
            "restart the shell or refresh PATH before benchmarking subprocess-heavy workflows."
        )
    if current_kind in compatibility_kinds:
        return (
            "current process PATH resolves a compatibility shim "
            f"({current_path}); benchmark timing may include shim overhead."
        )
    return None


def _doctor_mcp_stdio_launcher_warning(
    *,
    native_tg_binary: Path | None,
    launchers: list[tuple[str, str | None, str | None]],
    path_tg_candidates: list[dict[str, str | None]] | None = None,
) -> str | None:
    native_stdio_path = native_tg_binary
    if native_stdio_path is None or native_stdio_path.suffix.lower() != ".exe":
        native_stdio_path = next(
            (
                Path(path)
                for _label, kind, path in launchers
                if path
                and Path(path).suffix.lower() == ".exe"
                and kind in {"managed-native", "native-exe"}
            ),
            None,
        )
    if native_stdio_path is None or native_stdio_path.suffix.lower() != ".exe":
        return None

    powershell_launchers = [
        (label, path)
        for label, kind, path in launchers
        if path and (kind == "powershell-shim" or Path(path).suffix.lower() == ".ps1")
    ]
    # Also flag .ps1 anywhere in PATH candidates: PowerShell's `Get-Command tg`
    # resolves .ps1 ahead of .exe regardless of enumeration order, so a .ps1
    # sibling next to a working .exe still traps MCP clients using Start-Process.
    if path_tg_candidates:
        seen_paths = {path for _, path in powershell_launchers if path}
        for candidate in path_tg_candidates:
            cpath = candidate.get("path")
            if cpath and cpath not in seen_paths and Path(cpath).suffix.lower() == ".ps1":
                powershell_launchers.append(("PATH .ps1 sibling", cpath))
                seen_paths.add(cpath)
    if not powershell_launchers:
        return None

    observed = "; ".join(f"{label} resolves {path}" for label, path in powershell_launchers)
    script_path = powershell_launchers[0][1]
    return (
        "MCP stdio launcher warning: "
        f"{observed}. Configure MCP clients for `tg mcp` to call the managed native "
        f"tg.exe directly: {native_stdio_path}. Windows MCP/stdio clients that launch "
        "`tg` via PowerShell Start-Process must target native tg.exe directly, not "
        "`tg.ps1`, because Start-Process can resolve the PowerShell shim instead of "
        "the native stdio-safe front door. If you intentionally use the PowerShell "
        "script shim, configure the client to launch it explicitly as "
        f"`pwsh -NoProfile -File {script_path} mcp`."
    )


def _doctor_tg_foreign_warning(
    *,
    label: str,
    path: str | None,
    version: str | None,
    expected_version: str,
) -> str | None:
    if not path or _doctor_tg_version_looks_like_tensor_grep(version):
        return None
    return (
        f"first {label} tg is not tensor-grep: {path} reports "
        f"{version or 'no recognizable --version output'}; expected tg {expected_version}."
    )


def _doctor_tg_foreign_remediation(
    *,
    foreign_path: str | None,
    candidates: list[dict[str, str | None]],
) -> str | None:
    if not foreign_path:
        return None
    managed_candidate = next(
        (
            candidate
            for candidate in candidates
            if _doctor_tg_launcher_kind(candidate.get("path"), candidate.get("version"))
            == "managed-native"
        ),
        None,
    )
    managed_path = managed_candidate.get("path") if managed_candidate else None
    managed_dir = str(Path(managed_path).parent) if managed_path else "~/.tensor-grep/bin"
    foreign_dir = str(Path(foreign_path).parent)
    return (
        f"Move {managed_dir} earlier in PATH than {foreign_dir}, or rename the foreign tg "
        "command outside tensor-grep. If the foreign directory comes from Machine PATH, "
        "User PATH repair cannot outrank it. If you own the foreign command, run "
        "tg repair-launcher --allow-foreign-rename to back it up before installing the "
        "managed native tg.exe into that PATH slot. Do not remove unrelated launchers "
        "unless you own them."
    )


def _doctor_gpu_tier_installed() -> bool:
    """Tier 1 — is the cuDF GPU library findable in the current environment?

    Uses ``importlib.util.find_spec`` so we can detect installation without actually
    importing cuDF (which may allocate GPU memory).  Returns False if cuDF is not
    installed or if the spec lookup itself raises.
    """
    try:
        import importlib.util

        return importlib.util.find_spec("cudf") is not None
    except Exception:
        return False


def _doctor_gpu_tier_usable() -> bool:
    """Tier 2 — does CuDFBackend.is_available() confirm live GPU allocation?

    Imports CuDFBackend *by name* (orthogonal to any cudf-device-bind slice that may
    also touch CuDFBackend) and calls is_available(), which physically allocates a GPU
    tensor.  Returns False on any exception so a missing CUDA driver or GPU is reported
    cleanly.
    """
    try:
        from tensor_grep.backends.cudf_backend import CuDFBackend

        return CuDFBackend().is_available()
    except Exception:
        return False


def _doctor_gpu_status() -> dict[str, Any]:
    status: dict[str, Any] = {"available": False, "devices": [], "error": None}
    try:
        from tensor_grep.core.hardware.device_detect import DeviceDetector

        detector = DeviceDetector()
        status["available"] = detector.has_gpu()
        status["device_count"] = detector.get_device_count()
        for device in detector.list_devices():
            status["devices"].append({
                "id": device.device_id,
                "vram_total_mb": device.vram_capacity_mb,
            })
    except ImportError:
        status["error"] = "PyTorch/cuDF not installed"
    except Exception as e:
        status["error"] = str(e)
    # Observability tiers — installed and usable are computed here; promotion_proof is
    # filled in by _build_doctor_payload() after the search_runtime_probe runs.
    status["tier"] = {
        "installed": _self._doctor_gpu_tier_installed(),
        "usable": _self._doctor_gpu_tier_usable(),
        "promotion_proof": False,
    }
    return status


# P0-2 (#171 taxonomy): the native tg binary, run with --json by the probe below, prints a
# single structured error object to stdout before exiting non-zero (see
# exit_structured_search_error_if_needed in rust_core/src/main.rs) with an "error" field naming
# the failure kind. Map each KNOWN kind to a distinct doctor status instead of collapsing every
# rc!=0 outcome to one opaque "failed" -- an agent reading tg doctor --json can then tell "the
# probe's own temp/translated path went missing" (failed_path_bridging / failed_probe_path,
# see _doctor_gpu_probe_failure_status) apart from "the sentinel search input was rejected"
# (failed_input) apart from "the GPU route itself hit a real CUDA fault" (failed_gpu_unavailable).
# Any kind this table has never seen -- or stdout that is not the expected structured JSON at all
# (a raw panic, empty output, etc.) -- fails closed to failed_other rather than guessing.
#
# "path_not_found" is intentionally absent from this static table -- NIT-2 (#172):
# _doctor_gpu_probe_failure_status conditions it on is_cross_domain_native_binary(...) instead,
# because "failed_path_bridging" bakes in a WSL cross-domain assumption a same-domain host has no
# grounds to make (a vanished probe dir there is a neutral failed_probe_path, not a bridging
# failure). CONTRACTS.md does not enumerate doctor GPU-probe failure statuses, so both are
# additive diagnostics, not a breaking contract change.
_DOCTOR_GPU_PROBE_FAILURE_STATUS_BY_NATIVE_ERROR_KIND: dict[str, str] = {
    "empty_pattern": "failed_input",
    "invalid_regex": "failed_input",
    "gpu_fatal": "failed_gpu_unavailable",
    # NIT-3 (#172): the classifier's invalid-device arm (rust_core/src/main.rs
    # gpu_fatal_native_error_kind) emits this DISTINCT kind instead of the coarse "gpu_fatal" so a
    # typo'd --gpu-device-ids reads as a user-input error, not "GPU unavailable".
    "gpu_invalid_device_id": "failed_input",
}
_DOCTOR_GPU_PROBE_DEFAULT_FAILURE_STATUS = "failed_other"
_DOCTOR_GPU_PROBE_PATH_NOT_FOUND_STATUS_CROSS_DOMAIN = "failed_path_bridging"
_DOCTOR_GPU_PROBE_PATH_NOT_FOUND_STATUS_SAME_DOMAIN = "failed_probe_path"


def _doctor_gpu_probe_native_error_kind(stdout: str | None) -> str | None:
    """Parse the native binary's structured --json error kind from a failed probe's stdout.

    Returns None when stdout is empty, not JSON, not an object, or has no string "error" field
    -- any of which means there is nothing trustworthy to classify, so the caller must fail
    closed to the generic failed_other status rather than inventing a kind.
    """
    if not stdout:
        return None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    error_kind = payload.get("error")
    return error_kind if isinstance(error_kind, str) and error_kind else None


def _doctor_gpu_probe_failure_status(
    native_error_kind: str | None, *, cross_domain: bool = False
) -> str:
    if native_error_kind is None:
        return _DOCTOR_GPU_PROBE_DEFAULT_FAILURE_STATUS
    if native_error_kind == "path_not_found":
        return (
            _DOCTOR_GPU_PROBE_PATH_NOT_FOUND_STATUS_CROSS_DOMAIN
            if cross_domain
            else _DOCTOR_GPU_PROBE_PATH_NOT_FOUND_STATUS_SAME_DOMAIN
        )
    return _DOCTOR_GPU_PROBE_FAILURE_STATUS_BY_NATIVE_ERROR_KIND.get(
        native_error_kind, _DOCTOR_GPU_PROBE_DEFAULT_FAILURE_STATUS
    )


def _doctor_gpu_search_runtime_probe(native_tg_binary: Path | None) -> dict[str, Any]:
    requested_gpu_device_ids = [0]
    base: dict[str, Any] = {
        "status": "not_run",
        "requested_gpu_device_ids": requested_gpu_device_ids,
        "command": None,
        "exit_code": None,
        "routing_backend": None,
        "routing_reason": None,
        "sidecar_used": None,
        "routing_gpu_device_ids": [],
        "native_error_kind": None,
        "error": None,
    }
    if native_tg_binary is None:
        base["error"] = "native tg binary was not resolved"
        return base
    if not native_tg_binary.exists():
        base["error"] = f"native tg binary does not exist: {native_tg_binary}"
        return base

    # GPU-P0-1 (#171): on WSL, native_tg_binary can be a Windows-target binary that cannot
    # resolve a Linux TemporaryDirectory path. Detect that cross-domain mismatch and bridge the
    # sentinel path via wslpath before it becomes argv; the shared helper also raises the probe
    # timeout floor since a WSL -> Windows exec can legitimately take longer.
    cross_domain = _self.is_cross_domain_native_binary(native_tg_binary)
    probe_timeout_s = gpu_probe_timeout_s(cross_domain=cross_domain)

    sentinel = "tg doctor gpu runtime probe"
    with TemporaryDirectory(prefix="tg-doctor-gpu-probe-") as temp_dir:
        probe_file = Path(temp_dir) / "probe.log"
        probe_file.write_text(f"{sentinel}\n", encoding="utf-8")
        probe_target = str(probe_file)
        if cross_domain:
            translated = _self.translate_path_for_windows_binary(probe_file)
            if translated is None:
                base["status"] = "path_domain_mismatch"
                base["error"] = (
                    "resolved native tg binary targets Windows but this WSL host could not "
                    "translate the probe path via wslpath (path-domain mismatch, not a GPU "
                    "capability gap)"
                )
                return base
            probe_target = translated
        command = [
            str(native_tg_binary),
            "search",
            "--gpu-device-ids",
            ",".join(str(device_id) for device_id in requested_gpu_device_ids),
            "--json",
            "--no-ignore",
            "-F",
            # End-of-options sentinel (CWE-88 class, AGENTS.md), BEFORE EVERY POSITIONAL.
            #
            # The first cut of this put it BETWEEN `sentinel` and `probe_target`, terminating
            # options for the path while leaving the PATTERN unguarded. A sentinel in the wrong
            # place reads as protection and is not -- and the source-scanning census I shipped
            # alongside it could not tell the difference, because it only asked whether `--`
            # appeared somewhere in the function. Caught by an independent adversarial review, and
            # the reason the test is now BEHAVIOURAL: position is the property, presence is only a
            # proxy for it.
            #
            # Both positionals here are tg-generated, so the live risk is low -- but uniformity IS
            # the security property. A sweep whose members each carry a private risk assessment is
            # a sweep nobody can check.
            "--",
            sentinel,
            probe_target,
        ]
        base["command"] = " ".join([*command[:-1], "<doctor-gpu-probe-file>"])
        try:
            result = _self.subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=probe_timeout_s,
            )
        except _self.subprocess.TimeoutExpired:
            base["status"] = "failed"
            base["error"] = f"GPU runtime probe timed out after {probe_timeout_s:g} seconds"
            return base
        except OSError as exc:
            base["status"] = "failed"
            base["error"] = str(exc)
            return base

    base["exit_code"] = result.returncode
    if result.returncode != 0:
        native_error_kind = _doctor_gpu_probe_native_error_kind(result.stdout)
        base["native_error_kind"] = native_error_kind
        base["status"] = _doctor_gpu_probe_failure_status(
            native_error_kind, cross_domain=cross_domain
        )
        base["error"] = (result.stderr or "").strip() or "GPU runtime probe failed"
        return base

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        base["status"] = "failed"
        base["error"] = f"GPU runtime probe returned invalid JSON: {exc}"
        return base

    routing_backend = str(payload.get("routing_backend") or "")
    sidecar_used = bool(payload.get("sidecar_used", False))
    base.update({
        "routing_backend": routing_backend or None,
        "routing_reason": payload.get("routing_reason"),
        "sidecar_used": sidecar_used,
        "routing_gpu_device_ids": payload.get("routing_gpu_device_ids") or [],
    })
    if routing_backend == "NativeGpuBackend" and not sidecar_used:
        base["status"] = "supported"
        return base

    base["status"] = "unsupported"
    base["error"] = (
        "GPU route did not use NativeGpuBackend "
        f"(routing_backend={routing_backend or 'unknown'}, sidecar_used={sidecar_used})."
    )
    return base


def _doctor_ast_cache_status(root_path: str, config_path: str) -> dict[str, Any]:
    root = Path(root_path).resolve()
    cache_file = root / ".tg_cache" / "ast" / "project_data_v6.json"
    status: dict[str, Any] = {"exists": False}
    if cache_file.exists():
        stat = cache_file.stat()
        status["exists"] = True
        status["size_bytes"] = stat.st_size
        status["mtime"] = stat.st_mtime
        stale = False
        try:
            cache_mtime = stat.st_mtime
            sgconfig = Path(config_path).resolve()
            if sgconfig.exists() and sgconfig.stat().st_mtime > cache_mtime:
                stale = True
            if not stale:
                with cache_file.open("r", encoding="utf-8") as f:
                    import json

                    data = json.load(f)
                val_meta = data.get("validation_metadata", {})
                for field in ("rule_files", "test_files", "tree_dirs"):
                    for file_path_str, recorded_mtime_ns in val_meta.get(field, {}).items():
                        p = Path(file_path_str)
                        if not p.exists() or p.stat().st_mtime_ns > recorded_mtime_ns:
                            stale = True
                            break
                    if stale:
                        break
        except Exception as exc:
            # W1-b (2026-08-20) SILENT-SWALLOW hardening: this used to be `except Exception:
            # pass`, which left `stale` at whatever it was set to before the exception (False
            # on the common path -- a corrupt/unreadable cache file or manifest read at the
            # `Path(config_path).resolve()`/`json.load` calls above reported a clean, silent
            # "not stale", indistinguishable from a genuinely fresh cache. Fail SAFE instead:
            # an unreadable staleness check means "assume stale" (worst case is an unnecessary
            # rebuild, never a stale cache reported as fresh), and disclose why on the payload
            # `tg doctor --json` readers already inspect.
            stale = True
            status["stale_check_error"] = str(exc)
        status["stale"] = stale
    if not status["exists"]:
        # Round-5 UX: a cold cache silently costs ~20-30s on the first query over a large tree.
        # Surface a self-service remediation (agents read this via `doctor --json`).
        status["remediation"] = (
            "run `tg map .` once to warm the AST cache "
            "(avoids ~20-30s first-query latency on large trees)"
        )
    return status


def _doctor_resident_worker_status(path: str) -> dict[str, Any]:
    import socket

    root = Path(path).resolve()
    port_file = root / ".tg_cache" / "ast" / "worker_port.txt"
    status: dict[str, Any] = {"port_file_exists": False, "port": None, "responding": False}
    if port_file.exists():
        status["port_file_exists"] = True
        try:
            port = int(port_file.read_text(encoding="utf-8").strip())
            status["port"] = port
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(("127.0.0.1", port))
                status["responding"] = True
        except Exception:
            status["responding"] = False
    return status


def _doctor_shell_escaping_guidance() -> dict[str, Any]:
    return {
        "platform": "windows",
        "status": "informational",
        "powershell": {
            "summary": ("PowerShell double quotes expand $NAME before tensor-grep receives argv."),
            "recommendation": (
                "Use single quotes for literal patterns containing $, or escape `$` "
                "inside double-quoted PowerShell strings."
            ),
            "literal_pattern_example": "tg search '$NAME' .",
        },
        "cmd": {
            "summary": "cmd.exe parses metacharacters before tensor-grep receives argv.",
            "metacharacters": ["|", "&", "<", ">", "^", "(", ")"],
            "recommendation": (
                "Quote arguments or caret-escape cmd.exe metacharacters such as ^| and ^&; "
                "prefer normal interactive PowerShell `tg` over direct `tg.cmd` from "
                "PowerShell. MCP/stdio clients using Start-Process should target native "
                "`tg.exe` directly, not `tg.ps1`."
            ),
            "literal_pattern_example": 'cmd /c tg search "foo^|bar" .',
        },
    }


def _doctor_native_frontdoor_flavor_mismatch_note(
    *, installed_flavor: str | None, requested_flavor: str | None
) -> str | None:
    """Honest note when the installed native-frontdoor asset flavor differs from what was
    requested (TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR) at install/upgrade time -- for example the
    caller asked for `nvidia` but the installer fell back to `cpu` because no NVIDIA asset was
    published for this platform at that release. None when either side is unknown (no metadata
    file -- an in-tree dev build never writes one) or the two already agree.
    """
    if installed_flavor is None or requested_flavor is None:
        return None
    if installed_flavor == requested_flavor:
        return None
    return (
        f"installed native-frontdoor flavor '{installed_flavor}' does not match the requested "
        f"flavor '{requested_flavor}' ({_self._NATIVE_FRONTDOOR_FLAVOR_ENV}) -- the requested flavor "
        "may not have been published for this platform at install time; rerun `tg upgrade` or "
        "unset the flavor override to accept the installed build."
    )
