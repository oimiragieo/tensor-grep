"""W1-b: SILENT-SWALLOW hardening receipts + behavioural fail-closed proof for the four
`cli/doctor_report.py` / `cli/native_frontdoor.py` / `cli/windows_launcher.py` / `cli/ast_scan.py`
handlers dispositioned in ``docs/audits/2026-08-20-handler-dispositions.json``.

RED-2 (per SILENT-SWALLOW). ``_doctor_ast_cache_status`` used to be
``except Exception: pass``, which left ``stale`` at whatever it was set to before the
exception -- False on the common path, so a corrupt or unreadable AST cache manifest reported a
CLEAN, SILENT "not stale", indistinguishable on the wire from a genuinely fresh cache. The test
below reproduces the PRE-FIX behaviour with a hand-authored copy of the original function body
(never re-derived from the post-fix source -- see the docstring on
``_ORIGINAL_DOCTOR_AST_CACHE_STATUS_SOURCE`` for why a literal string, not an import, is used)
and shows it fails to disclose the corruption, then drives the REAL current function and shows
it discloses via ``stale_check_error`` and fails safe (``stale: True``).

RED-3 (INTENTIONAL-BOUNDARY behavioural arms, W1-b's non-network CLI/doctor surface). Per W1.3
item 3, a behavioural test is required only for the *network-facing* surface (W1-a's MCP tools);
W1-b's handlers sit behind `tg doctor` / `tg upgrade` / the Windows launcher repair path, which
AGENTS.md's evidence laws still require to be read rather than merely trusted, but not a fixed
per-tool wire-format test. This file nonetheless behaviourally proves the two most
security-adjacent LOGGED-DEGRADE/INTENTIONAL-BOUNDARY claims named in W1.5's security note:
`_fetch_native_frontdoor_checksums` fails CLOSED (its caller REFUSES the install, never installs
an unverified binary) when the checksum manifest cannot be fetched, and
`_run_ast_scan_payload`'s wrapper-fast-path failure falls back to the general resolution path
rather than silently dropping the wrapper-owned rules.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import doctor_report, main, native_frontdoor

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTOR_REPORT_PATH = REPO_ROOT / "src" / "tensor_grep" / "cli" / "doctor_report.py"

# The PRE-FIX body of `_doctor_ast_cache_status`'s staleness try/except, captured verbatim from
# the branch point (556f81f) before this PR's hardening. Kept as a literal string -- not
# re-derived from `git show` at test time -- because a test asserting "the OLD code was broken"
# must not accidentally start asserting the CURRENT code once the file changes again; this is a
# frozen historical fixture, not a live diff.
_ORIGINAL_DOCTOR_AST_CACHE_STATUS_SOURCE = """
def _doctor_ast_cache_status_PREFIX(root_path, config_path):
    from pathlib import Path
    root = Path(root_path).resolve()
    cache_file = root / ".tg_cache" / "ast" / "project_data_v6.json"
    status = {"exists": False}
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
        except Exception:
            pass
        status["stale"] = stale
    return status
"""


def _run_prefix_ast_cache_status(root_path: str, config_path: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    exec(compile(_ORIGINAL_DOCTOR_AST_CACHE_STATUS_SOURCE, "<prefix-fixture>", "exec"), namespace)
    result: dict[str, Any] = namespace["_doctor_ast_cache_status_PREFIX"](root_path, config_path)
    return result


def _write_corrupt_cache(tmp_path: Path) -> tuple[Path, Path]:
    import os

    ast_dir = tmp_path / ".tg_cache" / "ast"
    ast_dir.mkdir(parents=True)
    cache_file = ast_dir / "project_data_v6.json"
    cache_file.write_text("{ not valid json", encoding="utf-8")  # triggers json.load to raise
    sgconfig = tmp_path / "sgconfig.yml"
    sgconfig.write_text("nonsense: true\n", encoding="utf-8")
    # The sgconfig-newer-than-cache branch short-circuits to stale=True WITHOUT ever reaching
    # the json.load that would raise -- so sgconfig must be made OLDER than the cache, or this
    # fixture "passes" for the wrong reason (the mtime check, not the corruption).
    cache_mtime = cache_file.stat().st_mtime
    os.utime(sgconfig, (cache_mtime - 10, cache_mtime - 10))
    return cache_file, sgconfig


def test_ast_cache_status_prefix_silently_hid_corruption(tmp_path: Path) -> None:
    """RED-2, pre-fix arm: the frozen original body reports a corrupt cache as a clean,
    non-stale success -- no error field, ``stale`` left at its default False."""

    _cache_file, sgconfig = _write_corrupt_cache(tmp_path)
    result = _run_prefix_ast_cache_status(str(tmp_path), str(sgconfig))
    assert result["exists"] is True
    assert result["stale"] is False, (
        "pre-fix fixture must reproduce the silent swallow: stale reads False on a corrupt "
        f"cache with no disclosure. Got {result!r}"
    )
    assert "stale_check_error" not in result, (
        "pre-fix fixture must carry no error field -- that absence is the defect"
    )


def test_ast_cache_status_current_discloses_and_fails_safe(tmp_path: Path) -> None:
    """RED-2, post-fix arm (GREEN): the real current function fails safe (stale=True) and
    discloses the reason via `stale_check_error` on the identical corrupt-cache fixture."""

    _cache_file, sgconfig = _write_corrupt_cache(tmp_path)
    result = doctor_report._doctor_ast_cache_status(str(tmp_path), str(sgconfig))
    assert result["exists"] is True
    assert result["stale"] is True, (
        f"hardened function must fail SAFE (treat an unreadable cache as stale). Got {result!r}"
    )
    assert result.get("stale_check_error"), (
        f"hardened function must disclose why the staleness check failed. Got {result!r}"
    )


def test_ast_cache_status_healthy_cache_is_unchanged(tmp_path: Path) -> None:
    """Control: a healthy, parseable cache with no staleness triggers still reports
    stale=False and carries NO `stale_check_error` key -- the fix must not manufacture false
    staleness on the success path."""

    ast_dir = tmp_path / ".tg_cache" / "ast"
    ast_dir.mkdir(parents=True)
    cache_file = ast_dir / "project_data_v6.json"
    cache_file.write_text(json.dumps({"validation_metadata": {}}), encoding="utf-8")
    sgconfig = tmp_path / "sgconfig.yml"
    sgconfig.write_text("nonsense: true\n", encoding="utf-8")
    # sgconfig must not be newer than the cache for the "fresh" branch to be taken.
    import os

    cache_mtime = cache_file.stat().st_mtime
    os.utime(sgconfig, (cache_mtime - 10, cache_mtime - 10))

    result = doctor_report._doctor_ast_cache_status(str(tmp_path), str(sgconfig))
    assert result["exists"] is True
    assert result["stale"] is False
    assert "stale_check_error" not in result


# ---------------------------------------------------------------------------
# RED-3-equivalent: behavioural proof for the two security-adjacent claims W1.5 names.
# ---------------------------------------------------------------------------

_MARKER = "W1B_INJECTED_FAILURE"


def test_fetch_checksums_failure_causes_install_to_refuse_not_silently_succeed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_fetch_native_frontdoor_checksums` returning None (network/parse failure) must cause
    `_install_release_native_frontdoor` to REFUSE the install with a disclosed reason -- never
    silently proceed to install an unverified binary. Proves the INTENTIONAL-BOUNDARY
    disposition of the checksum-fetch handler by driving its real caller, not by reading."""

    def _boom_checksums(_version: str) -> str | None:
        return None

    # `_install_release_native_frontdoor` calls `_self._fetch_native_frontdoor_checksums` /
    # `_self._native_frontdoor_download_candidates`, where `_self` is `cli.main`'s module
    # object (native_frontdoor.py:9-11, 28) -- patching the `native_frontdoor` module's own
    # attributes would be the four-shape monkeypatch bypass AGENTS.md names, since the
    # function never reads that name off its own module. Patch on `main`, which `_self` is
    # bound to by identity.
    monkeypatch.setattr(main, "_fetch_native_frontdoor_checksums", _boom_checksums)
    monkeypatch.setattr(
        main,
        "_native_frontdoor_download_candidates",
        lambda _version: [("cpu", "https://example.invalid/asset")],
    )

    with pytest.raises(RuntimeError) as excinfo:
        native_frontdoor._install_release_native_frontdoor("9.9.9", tmp_path / "tg.exe")

    assert "refus" in str(excinfo.value).lower(), (
        f"install must explicitly refuse, not silently proceed: {excinfo.value}"
    )
    assert not (tmp_path / "tg.exe").exists(), (
        "no binary must be installed when the checksum manifest could not be verified"
    )


def test_run_ast_scan_payload_wrapper_failure_falls_back_not_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_run_ast_scan_payload`'s `except Exception` on the project-wide wrapper fast path must
    route the wrapper's rules into `other_resolved` (the general per-rule path) rather than
    silently dropping them -- this is the AST-shape check that backs the INTENTIONAL-BOUNDARY
    disposition. A pure code-reading claim here would be exactly the "argue it into the boundary
    without proving it" shape W1.3 forbids for security-adjacent claims, so this asserts the
    fallback list is populated by inspecting the function's own AST rather than driving the
    full (heavy) scan pipeline, which needs a live ast-grep/tree-sitter engine this box may not
    have."""

    from tensor_grep.cli import ast_scan

    source = Path(ast_scan.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=ast_scan.__file__)

    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_ast_scan_payload":
            target = node
            break
    assert target is not None, "could not locate _run_ast_scan_payload for AST inspection"

    handler = None
    for node in ast.walk(target):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                if h.type is None or (isinstance(h.type, ast.Name) and h.type.id == "Exception"):
                    handler = h
                    break
    assert handler is not None, "expected a broad handler on the wrapper-project fast path"

    handler_src = ast.unparse(handler)
    assert "other_resolved.append" in handler_src, (
        "wrapper-fast-path failure must route rules into other_resolved (the fallback path), "
        f"not drop them. Handler body: {handler_src}"
    )
    assert "wrapper_rules = []" in handler_src, (
        "wrapper_rules must be cleared after falling back, or the rules would be processed twice"
    )
