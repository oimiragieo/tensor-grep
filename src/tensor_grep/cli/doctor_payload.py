"""`tg doctor`'s payload assembly and text renderer.

The tail half of the `cli/doctor_report.py` split (2026-08-20,
`docs/design/2026-08-19-split-floor-escape.md`): `_build_doctor_payload` calls the whole
`_doctor_*` probe family in `cli/doctor_report.py` and shapes the JSON envelope;
`_render_doctor_payload` turns that envelope into the human-readable report. Nothing in
`doctor_report.py` calls back into this module, so the dependency runs one way.

`_self` is `cli/main.py`'s module object, imported from `cli/_main_binding` -- read that
module's docstring before adding a bare cross-module reference.
"""

import os
import sys
from pathlib import Path
from typing import Any, cast

from tensor_grep.cli._main_binding import _self as _self

# Cross-module reads back into the head half of the split. These are NOT patched on
# `main`, so a direct import is correct -- anything the test suite patches is reached
# through `_self` instead.
from tensor_grep.cli.doctor_report import (
    _doctor_apply_lsp_missing_component_remediation as _doctor_apply_lsp_missing_component_remediation,
)
from tensor_grep.cli.doctor_report import (
    _doctor_apply_lsp_workspace_warnings as _doctor_apply_lsp_workspace_warnings,
)
from tensor_grep.cli.doctor_report import (
    _doctor_ast_cache_status as _doctor_ast_cache_status,
)
from tensor_grep.cli.doctor_report import (
    _doctor_dense_model_status as _doctor_dense_model_status,
)
from tensor_grep.cli.doctor_report import (
    _doctor_gpu_status as _doctor_gpu_status,
)
from tensor_grep.cli.doctor_report import (
    _doctor_installation_health as _doctor_installation_health,
)
from tensor_grep.cli.doctor_report import (
    _doctor_installed_behind_pypi as _doctor_installed_behind_pypi,
)
from tensor_grep.cli.doctor_report import (
    _doctor_lsp_probe_timeout_seconds as _doctor_lsp_probe_timeout_seconds,
)
from tensor_grep.cli.doctor_report import (
    _doctor_lsp_providers_by_language as _doctor_lsp_providers_by_language,
)
from tensor_grep.cli.doctor_report import (
    _doctor_mcp_stdio_launcher_warning as _doctor_mcp_stdio_launcher_warning,
)
from tensor_grep.cli.doctor_report import (
    _doctor_native_frontdoor_flavor_mismatch_note as _doctor_native_frontdoor_flavor_mismatch_note,
)
from tensor_grep.cli.doctor_report import (
    _doctor_native_tg_binary_kind as _doctor_native_tg_binary_kind,
)
from tensor_grep.cli.doctor_report import (
    _doctor_path_tg_launcher_warning as _doctor_path_tg_launcher_warning,
)
from tensor_grep.cli.doctor_report import (
    _doctor_resident_worker_status as _doctor_resident_worker_status,
)
from tensor_grep.cli.doctor_report import (
    _doctor_route_version_matches as _doctor_route_version_matches,
)
from tensor_grep.cli.doctor_report import (
    _doctor_rust_binary_remediation as _doctor_rust_binary_remediation,
)
from tensor_grep.cli.doctor_report import (
    _doctor_rust_binary_version_matches as _doctor_rust_binary_version_matches,
)
from tensor_grep.cli.doctor_report import (
    _doctor_rust_binary_version_status as _doctor_rust_binary_version_status,
)
from tensor_grep.cli.doctor_report import (
    _doctor_rust_binary_warning as _doctor_rust_binary_warning,
)
from tensor_grep.cli.doctor_report import (
    _doctor_shadow_launchers as _doctor_shadow_launchers,
)
from tensor_grep.cli.doctor_report import (
    _doctor_shell_escaping_guidance as _doctor_shell_escaping_guidance,
)
from tensor_grep.cli.doctor_report import (
    _doctor_tg_foreign_remediation as _doctor_tg_foreign_remediation,
)
from tensor_grep.cli.doctor_report import (
    _doctor_tg_foreign_warning as _doctor_tg_foreign_warning,
)
from tensor_grep.cli.doctor_report import (
    _doctor_tg_launcher_kind as _doctor_tg_launcher_kind,
)


def _build_doctor_payload(
    path: str, config: str | None = None, *, with_lsp: bool
) -> dict[str, Any]:
    root = Path(path).resolve()
    if config:
        config_p = Path(config)
        resolved_config = config_p if config_p.is_absolute() else (root / config_p).resolve()
        root = resolved_config.parent
    else:
        resolved_config = root / "sgconfig.yml"
    native_tg_binary = _self.resolve_native_tg_binary()
    env_keys = [
        "TG_NATIVE_TG_BINARY",
        "TG_FORCE_CPU",
        "TG_RESIDENT_AST",
        "TG_RUST_FIRST_SEARCH",
        "TG_RUST_EARLY_RG",
        "TG_RUST_EARLY_POSITIONAL_RG",
        "TENSOR_GREP_LSP_REQUEST_TIMEOUT_SECONDS",
        "TENSOR_GREP_LSP_INITIALIZE_TIMEOUT_SECONDS",
        "TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS",
        _self._DOCTOR_LSP_PROBE_TIMEOUT_ENV,
    ]
    installed_version = _self._doctor_installed_version()
    # NIT-1 + MF-2 (#172): compute rust_binary_version BEFORE the inspect_native_tg_binary call
    # (it used to be computed after) so it can be threaded through as version_text below --
    # inspect_native_tg_binary's own internal _native_tg_version call would otherwise spawn a
    # SECOND `tg --version` subprocess against the identical native_tg_binary that this line
    # already spawned one for.
    rust_binary_version = _self._doctor_rust_binary_version(native_tg_binary)
    # P0-2 (#171): surface the native-frontdoor flavor metadata that inspect_native_tg_binary
    # already computes (merging installed-vs-requested asset flavor) -- until now this was only
    # consumed by benchmarks/run_benchmarks.py and benchmarks/run_gpu_native_benchmarks.py, so
    # `tg doctor` had no way to tell a caller "you asked for nvidia but got cpu" without them
    # reaching for a benchmark script. Empty dict (no keys) for no binary / no metadata file (an
    # in-tree dev build never writes tg-native-metadata.json), so every lookup below is a safe
    # `.get(...)` returning None.
    native_frontdoor_inspection = (
        _self.inspect_native_tg_binary(
            native_tg_binary,
            expected_version=installed_version,
            version_text=rust_binary_version,
        )
        if native_tg_binary is not None
        else {}
    )
    native_frontdoor_flavor = native_frontdoor_inspection.get("native_frontdoor_flavor")
    native_frontdoor_requested_flavor = native_frontdoor_inspection.get(
        "native_frontdoor_requested_flavor"
    )
    native_tg_binary_kind = _doctor_native_tg_binary_kind(native_tg_binary)
    rust_binary_version_matches = _doctor_rust_binary_version_matches(
        installed_version,
        rust_binary_version,
    )
    rust_binary_version_status = _doctor_rust_binary_version_status(
        native_tg_binary_kind=native_tg_binary_kind,
        rust_binary_version=rust_binary_version,
        rust_binary_version_matches=rust_binary_version_matches,
    )
    skipped_native_tg_binaries = _self._doctor_skipped_native_tg_binaries(
        installed_version,
        native_tg_binary,
    )
    if native_tg_binary is None and any(
        candidate.get("version_status") == "stale" for candidate in skipped_native_tg_binaries
    ):
        rust_binary_version_status = "stale-skipped"
    rust_core_extension_available = _self._doctor_rust_core_extension_available()
    path_tg_candidates = _self._doctor_path_tg_candidates()
    path_tg_first_raw_version = path_tg_candidates[0].get("version") if path_tg_candidates else None
    path_tg_first_version = (
        str(path_tg_first_raw_version) if path_tg_first_raw_version is not None else None
    )
    path_tg_first_path = str(path_tg_candidates[0].get("path")) if path_tg_candidates else None
    path_tg_first_launcher_kind = _doctor_tg_launcher_kind(
        path_tg_first_path,
        path_tg_first_version,
    )
    fresh_shell_path_tg_candidates = _self._doctor_fresh_shell_path_tg_candidates()
    fresh_shell_path_tg_first_raw_version = (
        fresh_shell_path_tg_candidates[0].get("version") if fresh_shell_path_tg_candidates else None
    )
    fresh_shell_path_tg_first_version = (
        str(fresh_shell_path_tg_first_raw_version)
        if fresh_shell_path_tg_first_raw_version is not None
        else None
    )
    fresh_shell_path_tg_first_path = (
        str(fresh_shell_path_tg_candidates[0].get("path"))
        if fresh_shell_path_tg_candidates
        else None
    )
    fresh_shell_path_tg_first_launcher_kind = _doctor_tg_launcher_kind(
        fresh_shell_path_tg_first_path,
        fresh_shell_path_tg_first_version,
    )
    python_subprocess_path_tg_first = _self._doctor_python_subprocess_path_tg_candidate()
    python_subprocess_path_tg_first_raw_version = (
        python_subprocess_path_tg_first.get("version") if python_subprocess_path_tg_first else None
    )
    python_subprocess_path_tg_first_version = (
        str(python_subprocess_path_tg_first_raw_version)
        if python_subprocess_path_tg_first_raw_version is not None
        else None
    )
    python_subprocess_path_tg_first_path = (
        str(python_subprocess_path_tg_first.get("path"))
        if python_subprocess_path_tg_first
        else None
    )
    python_subprocess_path_tg_first_launcher_kind = _doctor_tg_launcher_kind(
        python_subprocess_path_tg_first_path,
        python_subprocess_path_tg_first_version,
    )
    path_tg_foreign_warning = _doctor_tg_foreign_warning(
        label="PATH",
        path=path_tg_first_path,
        version=path_tg_first_version,
        expected_version=installed_version,
    )
    fresh_shell_path_tg_foreign_warning = _doctor_tg_foreign_warning(
        label="fresh-shell PATH",
        path=fresh_shell_path_tg_first_path,
        version=fresh_shell_path_tg_first_version,
        expected_version=installed_version,
    )
    python_subprocess_path_tg_foreign_warning = _doctor_tg_foreign_warning(
        label="Python subprocess PATH",
        path=python_subprocess_path_tg_first_path,
        version=python_subprocess_path_tg_first_version,
        expected_version=installed_version,
    )
    python_subprocess_remediation_candidates: list[dict[str, str | None]] = []
    if python_subprocess_path_tg_first is not None:
        python_subprocess_remediation_candidates.append(python_subprocess_path_tg_first)
    python_subprocess_remediation_candidates.extend(path_tg_candidates)
    python_subprocess_remediation_candidates.extend(fresh_shell_path_tg_candidates)
    mcp_stdio_launchers = [
        ("PATH", path_tg_first_launcher_kind, path_tg_first_path),
        (
            "fresh-shell PATH",
            fresh_shell_path_tg_first_launcher_kind,
            fresh_shell_path_tg_first_path,
        ),
        (
            "Python subprocess PATH",
            python_subprocess_path_tg_first_launcher_kind,
            python_subprocess_path_tg_first_path,
        ),
    ]
    for label, candidates in (
        ("PATH candidate", path_tg_candidates),
        ("fresh-shell PATH candidate", fresh_shell_path_tg_candidates),
    ):
        for index, candidate in enumerate(candidates, start=1):
            candidate_path = candidate.get("path")
            candidate_version = candidate.get("version")
            mcp_stdio_launchers.append((
                f"{label} {index}",
                _doctor_tg_launcher_kind(candidate_path, candidate_version),
                candidate_path,
            ))
    gpu_status = _doctor_gpu_status()
    gpu_status["search_runtime_probe"] = _self._doctor_gpu_search_runtime_probe(native_tg_binary)
    # audit M10: gpu.available reflects whether a CUDA device is *present*, not whether the
    # GPU search runtime actually routes through NativeGpuBackend. Surface an honest
    # search_ready boolean derived from the runtime probe so callers don't read
    # gpu.available=true as "GPU search works".
    gpu_status["search_ready"] = (
        cast(dict[str, Any], gpu_status["search_runtime_probe"]).get("status") == "supported"
    )
    if gpu_status.get("available") and not gpu_status["search_ready"]:
        # Round-5 UX: `available=True search_ready=False` reads as "GPU is broken". It is not —
        # GPU search is experimental/opt-in. State that so agents/users don't chase a non-bug.
        gpu_status["search_ready_note"] = (
            "GPU search is experimental/opt-in; search_ready=False is expected and not a "
            "failure -- text and AST search are unaffected"
        )
    # Complete the promotion_proof tier now that the runtime probe result is available.
    # This is the highest tier: GPU search actually routed through NativeGpuBackend.
    cast(dict[str, Any], gpu_status["tier"])["promotion_proof"] = gpu_status["search_ready"]
    payload: dict[str, Any] = {
        "schema_version": _self._DOCTOR_SCHEMA_VERSION,
        "doctor_schema_version": _self._DOCTOR_SCHEMA_VERSION,
        "version": installed_version,
        "platform": sys.platform,
        "python_executable": sys.executable,
        "python_version": ".".join([str(x) for x in sys.version_info[:3]]),
        "invoked_as": sys.argv[0] if sys.argv else "tg",
        "root": str(root),
        "config": str(resolved_config),
        "native_tg_binary": str(native_tg_binary) if native_tg_binary is not None else None,
        "native_tg_binary_exists": native_tg_binary is not None,
        "native_tg_binary_kind": native_tg_binary_kind,
        "native_frontdoor_flavor": native_frontdoor_flavor,
        "native_frontdoor_requested_flavor": native_frontdoor_requested_flavor,
        "native_frontdoor_asset_name": native_frontdoor_inspection.get(
            "native_frontdoor_asset_name"
        ),
        "native_frontdoor_metadata_status": native_frontdoor_inspection.get(
            "native_frontdoor_metadata_status"
        ),
        "native_frontdoor_flavor_mismatch_note": _doctor_native_frontdoor_flavor_mismatch_note(
            installed_flavor=native_frontdoor_flavor,
            requested_flavor=native_frontdoor_requested_flavor,
        ),
        "rust_core_extension_available": rust_core_extension_available,
        "search_acceleration_backend": (
            "standalone-native-tg"
            if native_tg_binary is not None
            else "rust-core-extension"
            if rust_core_extension_available
            else "python"
        ),
        "rust_binary_version": rust_binary_version,
        "rust_binary_expected_version": installed_version,
        "rust_binary_version_matches": rust_binary_version_matches,
        "rust_binary_version_status": rust_binary_version_status,
        "rust_binary_version_warning": _doctor_rust_binary_warning(
            expected_version=installed_version,
            rust_binary_version=rust_binary_version,
            rust_binary_version_status=rust_binary_version_status,
            skipped_native_tg_binaries=skipped_native_tg_binaries,
        ),
        "rust_binary_remediation": _doctor_rust_binary_remediation(
            rust_binary_version_status=rust_binary_version_status,
            native_tg_binary_kind=native_tg_binary_kind,
        ),
        "skipped_native_tg_binaries": skipped_native_tg_binaries,
        "path_tg_candidates": path_tg_candidates,
        "path_tg_first_version": path_tg_first_version,
        "path_tg_first_launcher_kind": path_tg_first_launcher_kind,
        "path_tg_first_version_matches": _doctor_rust_binary_version_matches(
            installed_version,
            path_tg_first_version,
        ),
        "path_tg_first_is_foreign": path_tg_first_launcher_kind == "foreign",
        "path_tg_foreign_warning": path_tg_foreign_warning,
        "path_tg_foreign_remediation": _doctor_tg_foreign_remediation(
            foreign_path=path_tg_first_path if path_tg_foreign_warning else None,
            candidates=path_tg_candidates,
        ),
        "fresh_shell_path_tg_candidates": fresh_shell_path_tg_candidates,
        "fresh_shell_path_tg_first_version": fresh_shell_path_tg_first_version,
        "fresh_shell_path_tg_first_launcher_kind": fresh_shell_path_tg_first_launcher_kind,
        "fresh_shell_path_tg_first_version_matches": _doctor_rust_binary_version_matches(
            installed_version,
            fresh_shell_path_tg_first_version,
        ),
        "fresh_shell_path_tg_first_is_foreign": (
            fresh_shell_path_tg_first_launcher_kind == "foreign"
        ),
        "fresh_shell_path_tg_foreign_warning": fresh_shell_path_tg_foreign_warning,
        "fresh_shell_path_tg_foreign_remediation": _doctor_tg_foreign_remediation(
            foreign_path=(
                fresh_shell_path_tg_first_path if fresh_shell_path_tg_foreign_warning else None
            ),
            candidates=fresh_shell_path_tg_candidates,
        ),
        "python_subprocess_path_tg_first": python_subprocess_path_tg_first,
        "python_subprocess_path_tg_first_version": python_subprocess_path_tg_first_version,
        "python_subprocess_path_tg_first_launcher_kind": (
            python_subprocess_path_tg_first_launcher_kind
        ),
        "python_subprocess_path_tg_first_version_matches": (
            _doctor_rust_binary_version_matches(
                installed_version,
                python_subprocess_path_tg_first_version,
            )
        ),
        "python_subprocess_path_tg_first_is_foreign": (
            python_subprocess_path_tg_first_launcher_kind == "foreign"
        ),
        "python_subprocess_path_tg_foreign_warning": (python_subprocess_path_tg_foreign_warning),
        "python_subprocess_path_tg_foreign_remediation": _doctor_tg_foreign_remediation(
            foreign_path=(
                python_subprocess_path_tg_first_path
                if python_subprocess_path_tg_foreign_warning
                else None
            ),
            candidates=python_subprocess_remediation_candidates,
        ),
        "path_tg_launcher_warning": _doctor_path_tg_launcher_warning(
            current_kind=path_tg_first_launcher_kind,
            current_path=path_tg_first_path,
            fresh_kind=fresh_shell_path_tg_first_launcher_kind,
            fresh_path=fresh_shell_path_tg_first_path,
        ),
        "mcp_stdio_launcher_warning": _doctor_mcp_stdio_launcher_warning(
            native_tg_binary=native_tg_binary,
            launchers=mcp_stdio_launchers,
            path_tg_candidates=path_tg_candidates,
        ),
        "shell_escaping_guidance": _doctor_shell_escaping_guidance(),
        "gpu": gpu_status,
        "ast_grep": _self._doctor_ast_grep_status(),
        "ast_cache": _doctor_ast_cache_status(str(root), str(resolved_config)),
        "resident_worker": _doctor_resident_worker_status(str(root)),
        "env": {key: os.environ[key] for key in env_keys if os.environ.get(key)},
        "session_daemon": _self._doctor_session_daemon_status(str(root)),
        "dense_model": _doctor_dense_model_status(),
    }

    # A90 PATH-honesty (docs/plans/2026-08-11-doctor-path-honesty.md): surface the resolved
    # `tg`-vs-expected-wheel mismatch and the wheel-vs-PyPI drift as an aggregate health signal
    # so a foreign 0.32.0 shadow or a stale install is UNMISSABLE, not just scattered booleans.
    pypi_latest = _self._latest_pypi_tensor_grep_version()
    payload["pypi_latest"] = pypi_latest
    installed_behind_pypi = _doctor_installed_behind_pypi(installed_version, pypi_latest)
    payload["installed_behind_pypi"] = installed_behind_pypi
    route_entries = [
        {
            "route": "path",
            "path": path_tg_first_path,
            "version": path_tg_first_version,
            "kind": path_tg_first_launcher_kind,
            "foreign": path_tg_first_launcher_kind == "foreign",
            "version_matches": _doctor_route_version_matches(
                installed_version, path_tg_first_version
            ),
        },
        {
            "route": "fresh_shell_path",
            "path": fresh_shell_path_tg_first_path,
            "version": fresh_shell_path_tg_first_version,
            "kind": fresh_shell_path_tg_first_launcher_kind,
            "foreign": fresh_shell_path_tg_first_launcher_kind == "foreign",
            "version_matches": _doctor_route_version_matches(
                installed_version, fresh_shell_path_tg_first_version
            ),
        },
        {
            "route": "python_subprocess_path",
            "path": python_subprocess_path_tg_first_path,
            "version": python_subprocess_path_tg_first_version,
            "kind": python_subprocess_path_tg_first_launcher_kind,
            "foreign": python_subprocess_path_tg_first_launcher_kind == "foreign",
            "version_matches": _doctor_route_version_matches(
                installed_version, python_subprocess_path_tg_first_version
            ),
        },
    ]
    shadow_launchers = _doctor_shadow_launchers(route_entries)
    payload["shadow_launchers"] = shadow_launchers
    payload["installation_health"] = _doctor_installation_health(
        shadow_launchers,
        installed_version=installed_version,
        installed_behind_pypi=installed_behind_pypi,
        pypi_unavailable=pypi_latest is None,
        pypi_latest=pypi_latest,
    )
    if with_lsp:
        lsp_providers = _doctor_apply_lsp_missing_component_remediation(
            _doctor_apply_lsp_workspace_warnings(_self._doctor_lsp_provider_statuses(str(root)))
        )
        lsp_providers_by_language = _doctor_lsp_providers_by_language(lsp_providers)
        payload["lsp"] = {
            "schema_version": _self._DOCTOR_LSP_SCHEMA_VERSION,
            "enabled": True,
            "probe_timeout_seconds": _doctor_lsp_probe_timeout_seconds(),
            "providers": lsp_providers,
            "providers_by_language": lsp_providers_by_language,
        }
    else:
        lsp_providers = []
        lsp_providers_by_language = {}
        payload["lsp"] = {
            "schema_version": _self._DOCTOR_LSP_SCHEMA_VERSION,
            "enabled": False,
            "probe_timeout_seconds": None,
            "providers": lsp_providers,
            "providers_by_language": lsp_providers_by_language,
        }
    payload["lsp_provider_items"] = lsp_providers
    payload["lsp_providers"] = lsp_providers_by_language
    return payload


def _render_doctor_payload(payload: dict[str, Any]) -> str:
    lines = [
        "tensor-grep doctor",
        f"version: {payload['version']}",
        f"platform: {payload['platform']}",
        f"python: {payload['python_executable']} ({payload.get('python_version', 'unknown')})",
        f"invoked_as: {payload['invoked_as']}",
        f"root: {payload['root']}",
    ]
    # A90 PATH-honesty: scream loudly when the aggregate installation health is not ok, so a
    # foreign 0.32.0 shadow or a stale install can never be missed in human output. This line is
    # human-only (never touches the --json envelope).
    health = payload.get("installation_health")
    if health and health != "ok":
        lines.append(f"warning: installation_health={health}")
        if health == "foreign_launcher" or health == "launcher_version_mismatch":
            lines.append(
                "  one or more resolved `tg` launchers are foreign or not the installed wheel "
                "version (see shadow_launchers); pin the wheel: uvx --from "
                f"tensor-grep=={payload['version']} tg ..."
            )
        elif health == "stale_install":
            lines.append(
                "  the installed wheel is behind the latest PyPI release "
                f"(pypi_latest={payload.get('pypi_latest')}); run `tg upgrade`"
            )
        elif health == "unknown_pypi":
            lines.append(
                "  could not reach PyPI to verify freshness (offline?); install state is "
                "unverified, not clean"
            )
        elif health == "unverifiable_version":
            lines.append(
                "  one or more versions could not be parsed; install state is unverifiable, "
                "not clean"
            )
    native_tg_binary = payload.get("native_tg_binary")
    lines.append(f"native_tg_binary: {native_tg_binary or 'missing'}")
    lines.append(f"native_tg_binary_kind: {payload.get('native_tg_binary_kind', 'unknown')}")
    if native_frontdoor_flavor := payload.get("native_frontdoor_flavor"):
        lines.append(
            "native_frontdoor_flavor: "
            f"{native_frontdoor_flavor} "
            f"requested={payload.get('native_frontdoor_requested_flavor') or 'unknown'}"
        )
    if flavor_mismatch_note := payload.get("native_frontdoor_flavor_mismatch_note"):
        lines.append(f"native_frontdoor_flavor_mismatch_note: {flavor_mismatch_note}")
    lines.append(
        f"search_acceleration_backend: {payload.get('search_acceleration_backend', 'unknown')}"
    )
    if rust_version := payload.get("rust_binary_version"):
        lines.append(f"rust_binary_version:\n  {rust_version.replace(chr(10), chr(10) + '  ')}")
    if rust_binary_warning := payload.get("rust_binary_version_warning"):
        lines.append(f"rust_binary_version_warning: {rust_binary_warning}")
    if rust_binary_remediation := payload.get("rust_binary_remediation"):
        lines.append(f"rust_binary_remediation: {rust_binary_remediation}")
    skipped_native_tg_binaries = cast(
        list[dict[str, str | None]],
        payload.get("skipped_native_tg_binaries", []),
    )
    if skipped_native_tg_binaries:
        lines.append("skipped_native_tg_binaries:")
        for candidate in skipped_native_tg_binaries:
            lines.append(
                "  "
                f"{candidate.get('path')} "
                f"kind={candidate.get('kind') or 'unknown'} "
                f"version={candidate.get('version') or 'unknown'} "
                f"status={candidate.get('version_status') or 'unknown'}"
            )
    path_tg_candidates = cast(list[dict[str, str | None]], payload.get("path_tg_candidates", []))
    if path_tg_candidates:
        lines.append("path_tg_candidates:")
        for candidate in path_tg_candidates:
            lines.append(
                f"  {candidate.get('path')} version={candidate.get('version') or 'unknown'}"
            )
        lines.append(
            "path_tg_first_launcher_kind: "
            f"{payload.get('path_tg_first_launcher_kind') or 'unknown'}"
        )
        if payload.get("path_tg_first_version_matches") is False:
            lines.append(
                f"path_tg_warning: first PATH tg reports {payload.get('path_tg_first_version')} "
                f"expected {payload.get('version')}"
            )
    fresh_shell_path_tg_candidates = cast(
        list[dict[str, str | None]],
        payload.get("fresh_shell_path_tg_candidates", []),
    )
    if fresh_shell_path_tg_candidates:
        first_fresh = fresh_shell_path_tg_candidates[0]
        lines.append(
            "fresh_shell_path_tg_first: "
            f"{first_fresh.get('path')} "
            f"kind={payload.get('fresh_shell_path_tg_first_launcher_kind') or 'unknown'} "
            f"version={first_fresh.get('version') or 'unknown'}"
        )
    python_subprocess_path_tg_first = cast(
        dict[str, str | None] | None,
        payload.get("python_subprocess_path_tg_first"),
    )
    if python_subprocess_path_tg_first:
        lines.append(
            "python_subprocess_path_tg_first: "
            f"{python_subprocess_path_tg_first.get('path')} "
            f"kind={payload.get('python_subprocess_path_tg_first_launcher_kind') or 'unknown'} "
            f"version={python_subprocess_path_tg_first.get('version') or 'unknown'}"
        )
    if launcher_warning := payload.get("path_tg_launcher_warning"):
        lines.append(f"path_tg_launcher_warning: {launcher_warning}")
    if mcp_stdio_launcher_warning := payload.get("mcp_stdio_launcher_warning"):
        lines.append(f"mcp_stdio_launcher_warning: {mcp_stdio_launcher_warning}")
    if python_subprocess_warning := payload.get("python_subprocess_path_tg_foreign_warning"):
        lines.append(f"python_subprocess_path_tg_foreign_warning: {python_subprocess_warning}")
    if python_subprocess_remediation := payload.get(
        "python_subprocess_path_tg_foreign_remediation"
    ):
        lines.append(
            f"python_subprocess_path_tg_foreign_remediation: {python_subprocess_remediation}"
        )
    shell_escaping_guidance = cast(dict[str, Any], payload.get("shell_escaping_guidance", {}))
    if shell_escaping_guidance:
        powershell_guidance = cast(
            dict[str, Any],
            shell_escaping_guidance.get("powershell", {}),
        )
        cmd_guidance = cast(dict[str, Any], shell_escaping_guidance.get("cmd", {}))
        lines.append("shell_escaping_guidance:")
        lines.append(
            "  PowerShell: "
            f"{powershell_guidance.get('summary')} "
            f"{powershell_guidance.get('recommendation')} "
            f"example={powershell_guidance.get('literal_pattern_example')}"
        )
        metacharacters = ", ".join(str(item) for item in cmd_guidance.get("metacharacters", []))
        lines.append(
            "  cmd.exe metacharacters: "
            f"{metacharacters}. "
            f"{cmd_guidance.get('recommendation')} "
            f"example={cmd_guidance.get('literal_pattern_example')}"
        )

    gpu_payload = cast(dict[str, Any], payload.get("gpu", {}))
    gpu_tier = cast(dict[str, Any], gpu_payload.get("tier", {}))
    lines.append(
        f"gpu: available={gpu_payload.get('available', False)} "
        f"search_ready={gpu_payload.get('search_ready', False)}"
    )
    if gpu_payload.get("available") and not gpu_payload.get("search_ready"):
        lines.append(
            "  note: search_ready=False is expected -- GPU search is experimental/opt-in, "
            "not a failure; text and AST search are unaffected"
        )
    if gpu_tier:
        lines.append(
            f"  tier: installed={gpu_tier.get('installed', False)} "
            f"usable={gpu_tier.get('usable', False)} "
            f"promotion_proof={gpu_tier.get('promotion_proof', False)}"
        )
    if gpu_payload.get("error"):
        lines.append(f"  error: {gpu_payload['error']}")
    for dev in gpu_payload.get("devices", []):
        lines.append(f"  device {dev.get('id')}: {dev.get('vram_total_mb')} MB VRAM")

    ast_payload = cast(dict[str, Any], payload.get("ast_cache", {}))
    lines.append(f"ast_cache: exists={ast_payload.get('exists', False)}")
    if ast_payload.get("exists"):
        lines.append(f"  size: {ast_payload.get('size_bytes')} bytes")
        lines.append(f"  mtime: {ast_payload.get('mtime')}")
        lines.append(f"  stale: {ast_payload.get('stale')}")
    else:
        lines.append(
            "  hint: cache cold -- run `tg map .` once to warm it "
            "(avoids ~20-30s first-query latency on large trees)"
        )

    ast_grep_payload = cast(dict[str, Any], payload.get("ast_grep", {}))
    ast_grep_options = "/".join(
        str(option) for option in ast_grep_payload.get("semantic_run_options", [])
    )
    lines.append(
        "ast_grep: "
        f"available={ast_grep_payload.get('available', False)} "
        f"binary={ast_grep_payload.get('binary') or 'missing'} "
        f"semantic_run_options={ast_grep_options or 'unknown'} "
        f"timeout_seconds={ast_grep_payload.get('timeout_seconds') or 'unknown'}"
    )
    if ast_grep_payload.get("install_hint"):
        lines.append(f"  install_hint: {ast_grep_payload['install_hint']}")
    if ast_grep_payload.get("error"):
        lines.append(f"  error: {ast_grep_payload['error']}")

    worker_payload = cast(dict[str, Any], payload.get("resident_worker", {}))
    lines.append(
        f"resident_worker: port_file_exists={worker_payload.get('port_file_exists', False)} "
        f"port={worker_payload.get('port')} responding={worker_payload.get('responding', False)}"
    )

    env_payload = cast(dict[str, str], payload.get("env", {}))
    if env_payload:
        lines.append("env:")
        for key in sorted(env_payload):
            lines.append(f"  {key}={env_payload[key]}")

    session_payload = cast(dict[str, Any], payload["session_daemon"])
    if session_payload.get("running"):
        lines.append(
            "session_daemon: "
            f"running host={session_payload['host']} port={session_payload['port']} pid={session_payload['pid']}"
        )
    else:
        state = "stale-metadata" if session_payload.get("stale_metadata") else "stopped"
        lines.append(f"session_daemon: {state}")

    # tg-ledger step-0 (demand instrumentation, see docs/multi_agent_context_plane.md): a single
    # human summary line for the trailing-14-day demand receipt that the daemon persists to
    # daemon_metrics.json (read-back works even when the daemon is currently stopped).
    demand_metrics = cast(dict[str, Any], session_payload.get("demand_metrics") or {})
    demand_days_covered = int(demand_metrics.get("days_covered", 0) or 0)
    if "error" in demand_metrics or demand_days_covered == 0:
        demand_pre_gate = "NO-COVERAGE"
    else:
        demand_pre_gate = "MET" if demand_metrics.get("pre_gate_met") else "NOT-MET"
    lines.append(
        "session_daemon_demand(14d): "
        f"clients={int(demand_metrics.get('max_distinct_client_pids_14d', 0) or 0)} "
        f"concurrent_days={int(demand_metrics.get('days_with_2plus_concurrent', 0) or 0)} "
        f"dup_requests={int(demand_metrics.get('dup_requests_14d', 0) or 0)} "
        f"pre_gate={demand_pre_gate}"
    )

    lsp_payload = cast(dict[str, Any], payload.get("lsp", {}))
    if lsp_payload.get("enabled"):
        lines.append("lsp_providers:")
        if lsp_payload.get("probe_timeout_seconds") is not None:
            lines.append(f"lsp_probe_timeout_seconds: {lsp_payload['probe_timeout_seconds']}")
        for current in cast(list[dict[str, Any]], lsp_payload.get("providers", [])):
            command = current.get("command") or []
            command_str = " ".join(str(part) for part in command) if command else "missing"
            status = "running" if current.get("running") else "idle"
            availability = "available" if current.get("available") else "unavailable"
            source = current.get("command_source", "path")
            managed_root = current.get("managed_provider_root")
            last_error = current.get("last_error")
            health_status = current.get("health_status", "unknown")
            health_check = current.get("health_check", "unknown")
            lsp_proof = current.get("lsp_proof", False)
            not_lsp_proof_reason = current.get("not_lsp_proof_reason")
            suffix = f" last_error={last_error}" if last_error else ""
            if managed_root:
                suffix = f" managed_root={managed_root}{suffix}"
            if not_lsp_proof_reason:
                suffix = f"{suffix} not_lsp_proof_reason={not_lsp_proof_reason}"
            lines.append(
                f"  {current['language']}: {availability}/{status} "
                f"health={health_status} health_check={health_check} "
                f"lsp_proof={lsp_proof} source={source} command={command_str}{suffix}"
            )
    else:
        lines.append("lsp_providers: disabled")
    return "\n".join(lines)
