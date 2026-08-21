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

RED-2b (per SILENT-SWALLOW, added A3 round-1 HIGH, 2026-08-20). `_restart_session_daemon_
after_upgrade`'s pre-restart status probe was miscategorized LOGGED-DEGRADE: `current` was a
purely local variable, so if the probe raised AND the subsequent restart happened to succeed,
`status_error` was discarded and the caller received a clean success message with no trace the
probe had failed -- genuinely SILENT-SWALLOW on that path even though a sibling except-branch
(restart-failed) did disclose. Hardened so `status_probe_error` survives into every reachable
return, including the restart-succeeded path.

RED-3 (INTENTIONAL-BOUNDARY behavioural arms, W1-b's non-network CLI/doctor surface). Per W1.3
item 3, a behavioural test is required only for the *network-facing* surface (W1-a's MCP tools);
W1-b's handlers sit behind `tg doctor` / `tg upgrade` / the Windows launcher repair path, which
AGENTS.md's evidence laws still require to be read rather than merely trusted, but not a fixed
per-tool wire-format test. This file nonetheless behaviourally proves the most security-adjacent
LOGGED-DEGRADE/INTENTIONAL-BOUNDARY claim named in W1.5's security note: `_fetch_native_
frontdoor_checksums` fails CLOSED (its caller REFUSES the install, never installs an unverified
binary) when the checksum manifest cannot be fetched -- with a no-injection control arm proving
the natural failure path is a genuinely different code path than the injected one (A3 round-1
MEDIUM). `_run_ast_scan_payload`'s wrapper-fast-path fallback claim is instead proved
STRUCTURALLY (AST inspection of the handler body, not a live drive of the function) -- see that
test's docstring for why (A3 round-1 LOW).
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
# RED-2b: `_restart_session_daemon_after_upgrade` HIGH hardening (A3 round-1, 2026-08-20).
# ---------------------------------------------------------------------------


def _prefix_restart_session_daemon_after_upgrade(
    snapshot: dict[str, Any] | None,
    *,
    status_fn: Any,
    start_fn: Any,
) -> str | None:
    """Frozen PRE-FIX body of `_restart_session_daemon_after_upgrade` (branch point 86facdc),
    reproducing the bug the A3 round-1 HIGH finding names: `current` is purely local, so a
    probe exception's `status_error` is discarded the moment the subsequent restart succeeds.
    A literal copy, not a live import, for the same reason `_ORIGINAL_DOCTOR_AST_CACHE_STATUS_
    SOURCE` above is one -- this must keep failing even after the real function changes again.
    """
    if not snapshot:
        return None
    root = str(snapshot.get("root") or "").strip()
    if not root:
        return None
    try:
        current = status_fn(root)
    except Exception as exc:
        current = {"running": False, "status_error": str(exc)}
    if current.get("running") is True:
        return None
    try:
        started = start_fn(root)
    except Exception as exc:
        return f"WARNING: session daemon was running before upgrade but restart failed for {root}: {exc}"
    if started.get("running") is True:
        return f"Session daemon restarted after upgrade for {root}."
    return f"WARNING: session daemon was running before upgrade but did not restart for {root}."


def test_restart_after_upgrade_prefix_silently_dropped_probe_error_on_success() -> None:
    """RED, pre-fix arm: the probe raises, but the restart happens to succeed -- the returned
    message reads as a clean success with the probe's `status_error` nowhere in it."""

    def _boom_status(_root: str) -> dict[str, Any]:
        raise RuntimeError("probe boom: daemon status file unreadable")

    def _ok_start(_root: str) -> dict[str, Any]:
        return {"running": True}

    message = _prefix_restart_session_daemon_after_upgrade(
        {"root": "C:/some/root"}, status_fn=_boom_status, start_fn=_ok_start
    )
    assert message == "Session daemon restarted after upgrade for C:/some/root.", (
        f"pre-fix fixture must reproduce the silent swallow (a clean success message with no "
        f"trace of the probe failure). Got {message!r}"
    )
    assert "boom" not in (message or "") and "unreadable" not in (message or ""), (
        "pre-fix fixture must carry no disclosure of the probe error -- that absence is the defect"
    )


def test_restart_after_upgrade_discloses_probe_error_even_on_successful_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GREEN, post-fix arm: the real, current function discloses the probe failure even when
    the restart that follows it succeeds -- the exact case the pre-fix arm above proves was
    silently dropped."""

    def _boom_status(_root: str) -> dict[str, Any]:
        raise RuntimeError("probe boom: daemon status file unreadable")

    def _ok_start_session_daemon(_root: str) -> dict[str, Any]:
        return {"running": True}

    monkeypatch.setattr(main, "_doctor_session_daemon_status", _boom_status)
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.start_session_daemon", _ok_start_session_daemon
    )

    message = doctor_report._restart_session_daemon_after_upgrade({"root": "C:/some/root"})
    assert message is not None
    assert "restarted" in message.lower(), (
        f"a successful restart must still be reported: {message!r}"
    )
    assert "boom" in message or "unreadable" in message, (
        f"the pre-restart probe failure must be disclosed even though the restart itself "
        f"succeeded. Got {message!r}"
    )


def test_restart_after_upgrade_no_probe_error_stays_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: when the probe genuinely succeeds and reports the daemon already running, the
    function still short-circuits to None (no restart attempted, no manufactured disclosure)."""

    def _healthy_status(_root: str) -> dict[str, Any]:
        return {"running": True}

    monkeypatch.setattr(main, "_doctor_session_daemon_status", _healthy_status)

    message = doctor_report._restart_session_daemon_after_upgrade({"root": "C:/some/root"})
    assert message is None


# ---------------------------------------------------------------------------
# RED-3-equivalent: behavioural proof for the two security-adjacent claims W1.5 names.
# ---------------------------------------------------------------------------

_MARKER = "W1B_INJECTED_FAILURE"


def test_fetch_checksums_failure_causes_install_to_refuse_not_silently_succeed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_fetch_native_frontdoor_checksums` failing must cause `_install_release_native_
    frontdoor` to REFUSE the install with a disclosed reason -- never silently proceed to
    install an unverified binary. Proves the INTENTIONAL-BOUNDARY disposition of the
    checksum-fetch handler by driving its real caller, not by reading.

    A3 round-1 MEDIUM (2026-08-20): the original version of this test patched
    ``_fetch_native_frontdoor_checksums`` to return ``None`` -- but the REAL function also
    returns ``None`` on version "9.9.9" the moment its own internal network call fails (no
    live PyPI release exists for that version), which it always will in a sandboxed test
    run. That made the injected arm and an un-injected natural-failure arm produce the
    IDENTICAL refusal message, so the test could pass even if the monkeypatch never fired.
    Fixed per the W1-a oracle shape (see ``test_w1a_mcp_handler_fail_closed.py``): the
    injected dependency is now the actual failure SOURCE (it raises, carrying a marker), a
    no-injection control arm proves the natural path does NOT carry that marker, and the
    injected arm asserts the marker is present -- so the two arms are only equal if the
    injection genuinely never fired.
    """

    def _boom_checksums(_version: str) -> str | None:
        raise RuntimeError(_MARKER)

    # `_install_release_native_frontdoor` calls `_self._fetch_native_frontdoor_checksums` /
    # `_self._native_frontdoor_download_candidates`, where `_self` is `cli.main`'s module
    # object (native_frontdoor.py:9-11, 28) -- patching the `native_frontdoor` module's own
    # attributes would be the four-shape monkeypatch bypass AGENTS.md names, since the
    # function never reads that name off its own module. Patch on `main`, which `_self` is
    # bound to by identity.
    monkeypatch.setattr(
        main,
        "_native_frontdoor_download_candidates",
        lambda _version: [("cpu", "https://example.invalid/asset")],
    )

    # ---- ARM A: NO INJECTION (control) -------------------------------------------------
    # The real `_fetch_native_frontdoor_checksums` is left in place. It is NOT allowed to make
    # a real network call in a test (CPU-safe / hermetic-test discipline) -- so `urlopen` is
    # stubbed to fail fast with a plain `OSError`, which the real function's own internal
    # `except Exception: return None` catches, exactly as a genuine network failure would.
    # This exercises a genuinely different code path than the injected RuntimeError below
    # (the None-return branch, not the newly-added except-and-wrap branch), and one that must
    # never carry the marker.
    import urllib.request as _urllib_request

    def _boom_urlopen(*_args: object, **_kwargs: object) -> None:
        raise OSError("stubbed: no network in test")

    monkeypatch.setattr(_urllib_request, "urlopen", _boom_urlopen)

    with pytest.raises(RuntimeError) as natural_excinfo:
        native_frontdoor._install_release_native_frontdoor("9.9.9", tmp_path / "tg-natural.exe")
    natural_message = str(natural_excinfo.value)
    assert "refus" in natural_message.lower(), (
        f"natural (un-injected) failure must also refuse the install: {natural_message}"
    )
    assert _MARKER not in natural_message, (
        "the injection marker appears WITHOUT the injection -- the control arm is "
        f"contaminated and this case proves nothing. Got: {natural_message!r}"
    )
    assert not (tmp_path / "tg-natural.exe").exists()

    # ---- ARM B: INJECTED ----------------------------------------------------------------
    monkeypatch.setattr(main, "_fetch_native_frontdoor_checksums", _boom_checksums)

    with pytest.raises(RuntimeError) as excinfo:
        native_frontdoor._install_release_native_frontdoor("9.9.9", tmp_path / "tg.exe")

    injected_message = str(excinfo.value)
    assert "refus" in injected_message.lower(), (
        f"install must explicitly refuse, not silently proceed: {injected_message}"
    )
    assert _MARKER in injected_message, (
        "an error came back, but nothing ties it to the injected failure -- it may be the "
        f"SAME natural error the control arm produced. Got: {injected_message!r}"
    )
    assert not (tmp_path / "tg.exe").exists(), (
        "no binary must be installed when the checksum manifest could not be verified"
    )


def test_run_ast_scan_payload_wrapper_failure_falls_back_not_drops_STRUCTURAL(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STRUCTURAL proof (not behavioural -- see A3 round-1 LOW, 2026-08-20) that
    `_run_ast_scan_payload`'s `except Exception` on the project-wide wrapper fast path routes
    the wrapper's rules into `other_resolved` (the general per-rule path) rather than silently
    dropping them; this backs the INTENTIONAL-BOUNDARY disposition.

    This asserts the fallback list is populated by inspecting the function's own AST rather
    than driving `_run_ast_scan_payload` itself -- it never calls the function under test. The
    `monkeypatch` fixture is accepted only to match the shared per-case signature in this file
    and is intentionally unused. Earlier drafts of this docstring/ledger described this as
    "behavioural," which was inaccurate: an AST-shape check proves the SOURCE has the right
    handler structure, not that the function BEHAVES that way at runtime (a docstring-vs-
    dead-code split would pass identically). A genuine behavioural drive of the wrapper-fast-
    path except-branch would need either a live ast-grep/tree-sitter engine this box may not
    have, or a hand-rolled fake backend named `AstGrepWrapperBackend` (the branch keys off
    `type(backend).__name__`) wired through `_select_ast_backend_for_rule` -- left as a
    follow-up rather than done here; this test's function name and this docstring are the
    honest label until then."""

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
