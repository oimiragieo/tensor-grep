"""Round-5 Q14/Q15 hardening: DirectoryScanner traversal budget + .gitignore byte cap.

Q14: an unbounded directory walk on a pathological tree (deep/wide fanout) must STOP once a
defensive entry budget is exceeded, and must flag the truncation rather than silently dropping
the remainder of the tree.

Q15: reading `.gitignore` must be byte-capped so a giant file (crafted or accidental) cannot be
slurped into memory whole; anything beyond the cap is ignored and flagged, without crashing.

Task #276 slice 1: `os.walk()`'s DEFAULT `onerror=None` silently skips a directory it cannot
`os.scandir()` (permission denied, or removed mid-walk) -- no exception, no truncation flag, the
subtree just vanishes. `TestDirectoryScannerUnreadablePath` below covers the fix: a real
permission-denied fixture on Windows (`icacls`) and POSIX (`chmod 000`), PLUS a platform-agnostic
monkeypatch of `os.walk` that injects the `onerror` call deterministically -- used because a real
ACL/mode fixture can behave differently across CI runners (root-in-container ignores `chmod`,
non-admin Windows CI may not have `icacls` rights), so the monkeypatch test is the one guaranteed
to discriminate on every platform this repo's CI matrix runs.
"""

import os
import subprocess
import sys

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.io import directory_scanner as ds_module
from tensor_grep.io.directory_scanner import DirectoryScanner


class TestDirectoryScannerTraversalBudget:
    def test_should_stop_and_flag_truncation_when_scan_budget_exceeded(self, tmp_path):
        # 20 subdirectories each holding one file -> os.walk visits far more than a
        # deliberately tiny budget can absorb.
        for i in range(20):
            sub = tmp_path / f"d{i}"
            sub.mkdir()
            (sub / "file.py").write_text("x", encoding="utf-8")

        scanner = DirectoryScanner(SearchConfig(), max_scan_entries=5)
        files = list(scanner.walk(str(tmp_path)))

        # The walk must not silently drop -- it must both stop short AND flag truncation.
        assert len(files) < 20
        assert scanner.scan_truncated is True
        assert scanner.scan_truncation_cause == "max-scan-entries"

    def test_should_not_flag_truncation_when_budget_is_sufficient(self, tmp_path):
        for i in range(5):
            sub = tmp_path / f"d{i}"
            sub.mkdir()
            (sub / "file.py").write_text("x", encoding="utf-8")

        scanner = DirectoryScanner(SearchConfig(), max_scan_entries=10_000)
        files = list(scanner.walk(str(tmp_path)))

        assert len(files) == 5
        assert scanner.scan_truncated is False
        assert scanner.scan_truncation_cause is None


class TestDirectoryScannerGitignoreByteCap:
    def test_should_cap_oversized_gitignore_without_crash(self, tmp_path):
        # A .gitignore far larger than a small test cap; must not be read whole into memory.
        huge_contents = "*.bin\n" * 200_000
        (tmp_path / ".gitignore").write_text(huge_contents, encoding="utf-8")

        keep = tmp_path / "keep.py"
        keep.write_text("ok", encoding="utf-8")

        scanner = DirectoryScanner(SearchConfig(), gitignore_max_bytes=64)

        # Must not raise, and the legitimate file must still be returned (it is not a *.bin
        # pattern, and the oversized ignore file is only partially honored, never crashes).
        files = list(scanner.walk(str(tmp_path)))

        assert str(keep) in files
        assert scanner.gitignore_truncated is True

    def test_should_not_flag_truncation_for_small_gitignore(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
        keep = tmp_path / "keep.py"
        keep.write_text("ok", encoding="utf-8")
        ignored = tmp_path / "debug.log"
        ignored.write_text("ok", encoding="utf-8")

        scanner = DirectoryScanner(SearchConfig(), gitignore_max_bytes=64)
        files = list(scanner.walk(str(tmp_path)))

        assert str(keep) in files
        assert str(ignored) not in files
        assert scanner.gitignore_truncated is False


def _icacls(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["icacls", *args], capture_output=True, text=True)


class TestDirectoryScannerUnreadablePath:
    """Task #276 slice 1: `os.walk()`'s default `onerror=None` silently swallows a permission
    error -- the offending subtree just disappears from the walk with no exception and no
    `scan_truncated` flag. Every test in this class FAILS on pre-fix `main` (the walk would
    silently return only the readable files, `scan_truncated` would stay `False`) and PASSES
    after the `onerror=self._on_walk_error` wiring in `directory_scanner.py`.
    """

    @pytest.mark.skipif(sys.platform != "win32", reason="icacls is Windows-only")
    def test_windows_real_permission_denied_dir_flags_truncation(self, tmp_path):
        """REAL fixture (not a mock): lock a subdirectory down via `icacls` so
        `os.scandir()` genuinely raises `PermissionError`, then restore it in a `finally` so
        pytest's `tmp_path` cleanup can still remove it."""
        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        (ok_dir / "keep.py").write_text("x = 1\n", encoding="utf-8")
        denied_dir = tmp_path / "denied"
        denied_dir.mkdir()
        (denied_dir / "secret.py").write_text("x = 1\n", encoding="utf-8")

        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        lockdown = _icacls(
            str(denied_dir),
            "/inheritance:r",
            "/grant:r",
            "SYSTEM:(OI)(CI)F",
            "Administrators:(OI)(CI)F",
        )
        assert lockdown.returncode == 0, (
            f"icacls lockdown failed (rc={lockdown.returncode}): {lockdown.stderr}"
        )
        try:
            # Sanity: the lockdown must actually deny the CURRENT process, or this test is
            # vacuous (running as an account icacls couldn't restrict, e.g. a SYSTEM-level CI
            # runner) -- skip rather than silently pass on a fixture that proves nothing.
            try:
                list(os.scandir(denied_dir))
            except OSError:
                pass
            else:
                pytest.skip(
                    "icacls lockdown did not actually deny this process -- fixture is vacuous"
                )

            scanner = DirectoryScanner(SearchConfig())
            files = list(scanner.walk(str(tmp_path)))

            assert str(ok_dir / "keep.py") in files
            assert not any("secret.py" in f for f in files)
            assert scanner.scan_truncated is True
            assert scanner.scan_truncation_cause == "unreadable_path"
            assert scanner.unreadable_path_count >= 1
            assert any("denied" in sample for sample in scanner.unreadable_path_sample)
        finally:
            if user:
                _icacls(str(denied_dir), "/grant", f"{user}:(OI)(CI)F")
            else:
                _icacls(str(denied_dir), "/reset")

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod 0o000 semantics are POSIX-only")
    @pytest.mark.skipif(
        hasattr(os, "getuid") and os.getuid() == 0,
        reason="root ignores POSIX permission bits -- fixture would be vacuous",
    )
    def test_posix_real_permission_denied_dir_flags_truncation(self, tmp_path):
        """REAL fixture on POSIX: `chmod 0o000` genuinely blocks this process from
        listing the directory (unless running as root, skipped above)."""
        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        (ok_dir / "keep.py").write_text("x = 1\n", encoding="utf-8")
        denied_dir = tmp_path / "denied"
        denied_dir.mkdir()
        (denied_dir / "secret.py").write_text("x = 1\n", encoding="utf-8")

        os.chmod(denied_dir, 0o000)
        try:
            scanner = DirectoryScanner(SearchConfig())
            files = list(scanner.walk(str(tmp_path)))

            assert str(ok_dir / "keep.py") in files
            assert not any("secret.py" in f for f in files)
            assert scanner.scan_truncated is True
            assert scanner.scan_truncation_cause == "unreadable_path"
            assert scanner.unreadable_path_count >= 1
        finally:
            os.chmod(denied_dir, 0o755)

    def test_monkeypatched_walk_error_flags_truncation_platform_agnostic(
        self, tmp_path, monkeypatch
    ):
        """Platform-agnostic fallback: a real ACL/mode fixture can behave differently across CI
        runners (root-in-container ignores `chmod`; a locked-down Windows CI account may not
        have `icacls` rights). Monkeypatch `os.walk` itself to inject a real `PermissionError`
        into the `onerror` callback the production code passes -- this exercises the REAL
        `DirectoryScanner._on_walk_error` handler (not a mock of it), just via a synthetic
        `os.walk` instead of a genuine filesystem permission failure. Deterministic on every
        platform this repo's CI matrix runs (ubuntu/macos/windows)."""
        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        (ok_dir / "keep.py").write_text("x = 1\n", encoding="utf-8")

        real_walk = os.walk

        def _fake_walk(top, onerror=None, **kwargs):
            if onerror is not None:
                onerror(PermissionError(13, "Permission denied", str(tmp_path / "denied")))
            yield from real_walk(top, onerror=onerror, **kwargs)

        monkeypatch.setattr(ds_module.os, "walk", _fake_walk)

        scanner = DirectoryScanner(SearchConfig())
        files = list(scanner.walk(str(tmp_path)))

        assert str(ok_dir / "keep.py") in files
        assert scanner.scan_truncated is True
        assert scanner.scan_truncation_cause == "unreadable_path"
        assert scanner.unreadable_path_count == 1
        assert scanner.unreadable_path_sample == [str(tmp_path / "denied")]

    def test_unreadable_path_sample_is_bounded_but_count_is_not(self, tmp_path, monkeypatch):
        """A tree with thousands of denied dirs must not accumulate an unbounded sample list,
        even though the TRUE count keeps incrementing."""
        real_walk = os.walk

        def _fake_walk(top, onerror=None, **kwargs):
            if onerror is not None:
                for i in range(12):
                    onerror(PermissionError(13, "Permission denied", f"denied{i}"))
            yield from real_walk(top, onerror=onerror, **kwargs)

        monkeypatch.setattr(ds_module.os, "walk", _fake_walk)

        scanner = DirectoryScanner(SearchConfig())
        list(scanner.walk(str(tmp_path)))

        assert scanner.unreadable_path_count == 12
        assert len(scanner.unreadable_path_sample) == 5  # bounded sample cap

    def test_complete_scan_leaves_unreadable_path_fields_at_defaults(self, tmp_path):
        """Omit-when-complete control arm at the scanner level: a scan with no permission
        errors must leave the new fields exactly at their untouched defaults."""
        (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")

        scanner = DirectoryScanner(SearchConfig())
        list(scanner.walk(str(tmp_path)))

        assert scanner.scan_truncated is False
        assert scanner.scan_truncation_cause is None
        assert scanner.unreadable_path_count == 0
        assert scanner.unreadable_path_sample == []
