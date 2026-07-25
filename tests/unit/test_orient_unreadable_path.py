"""Task #276 slice 1: `tg orient` walks the repo through `repo_map._iter_repo_files` /
`_iter_repo_bucket_files` -- a SEPARATE walker from `tg search`'s `DirectoryScanner`, but with the
identical defect: both wrap their `os.scandir()` call in a bare `except OSError` with no signal at
all. A permission-denied subdirectory silently vanished from `tg orient`'s central-file/entry-point
ranking, with no envelope field to tell an agent coverage was incomplete.

`tg orient` has NO exit-2 contract (docs/CONTRACTS.md) -- a truncated scan must stay exit `0`,
gaining only the informational `partial`/`partial_reason`/`incomplete_reason_class`/
`incomplete_reason` fields (mirroring `tg search`'s vocabulary).

Every test in this file FAILS on pre-fix `main` (no `unreadable_paths` key on the underlying repo
map, no `partial`/`incomplete_reason_class` on the capsule) and PASSES after threading
`_UnreadablePathFlag` through `repo_map._iter_repo_files`/`_iter_repo_bucket_files` and consuming
it in `build_repo_map` + `orient_capsule.build_orient_capsule_from_map`.
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import repo_map as repo_map_module
from tensor_grep.cli.main import app
from tensor_grep.cli.orient_capsule import build_orient_capsule


def _icacls(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["icacls", *args], capture_output=True, text=True)


class TestBuildRepoMapUnreadablePath:
    def test_monkeypatched_scandir_error_flags_unreadable_paths_platform_agnostic(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Platform-agnostic discriminator: monkeypatch `os.scandir` (what
        `_iter_repo_files`/`_iter_repo_bucket_files` actually call) to raise on one specific
        subdirectory while behaving normally everywhere else -- a real, not mocked, `OSError`
        reaching the real production `except OSError` handlers."""
        (tmp_path / "keep.py").write_text("def keep():\n    pass\n", encoding="utf-8")
        denied_dir = tmp_path / "denied"
        denied_dir.mkdir()
        (denied_dir / "secret.py").write_text("def secret():\n    pass\n", encoding="utf-8")

        real_scandir = os.scandir

        def _fake_scandir(path="."):
            if os.fspath(path) == os.fspath(denied_dir):
                raise PermissionError(13, "Permission denied", str(denied_dir))
            return real_scandir(path)

        monkeypatch.setattr(repo_map_module.os, "scandir", _fake_scandir)

        rm = repo_map_module.build_repo_map(tmp_path, max_repo_files=1000)

        assert any(f.endswith("keep.py") for f in rm["files"])
        assert "unreadable_paths" in rm
        assert rm["unreadable_paths"]["count"] >= 1
        assert any("denied" in s for s in rm["unreadable_paths"]["sample"])

    def test_complete_scan_omits_unreadable_paths_key(self, tmp_path: Path) -> None:
        """Omit-when-complete control arm at the repo_map level: no permission errors ->
        no `unreadable_paths` key at all (not merely an empty/False one)."""
        (tmp_path / "keep.py").write_text("def keep():\n    pass\n", encoding="utf-8")

        rm = repo_map_module.build_repo_map(tmp_path, max_repo_files=1000)

        assert "unreadable_paths" not in rm


class TestOrientCapsuleUnreadablePath:
    def test_monkeypatched_scandir_error_surfaces_informational_fields(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        (tmp_path / "keep.py").write_text("def keep():\n    pass\n", encoding="utf-8")
        denied_dir = tmp_path / "denied"
        denied_dir.mkdir()
        (denied_dir / "secret.py").write_text("def secret():\n    pass\n", encoding="utf-8")

        real_scandir = os.scandir

        def _fake_scandir(path="."):
            if os.fspath(path) == os.fspath(denied_dir):
                raise PermissionError(13, "Permission denied", str(denied_dir))
            return real_scandir(path)

        monkeypatch.setattr(repo_map_module.os, "scandir", _fake_scandir)

        payload = build_orient_capsule(tmp_path, max_tokens=500)

        assert payload["partial"] is True
        assert payload["partial_reason"] == "unreadable_path"
        assert payload["incomplete_reason_class"] == "unreadable_path"
        assert "incomplete_reason" in payload

    def test_complete_scan_omits_incompleteness_fields(self, tmp_path: Path) -> None:
        (tmp_path / "keep.py").write_text("def keep():\n    pass\n", encoding="utf-8")

        payload = build_orient_capsule(tmp_path, max_tokens=500)

        assert "partial" not in payload
        assert "partial_reason" not in payload
        assert "incomplete_reason_class" not in payload
        assert "incomplete_reason" not in payload

    @pytest.mark.skipif(sys.platform != "win32", reason="icacls is Windows-only")
    def test_windows_real_unreadable_dir_stays_exit_0_with_field_present(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Real fixture, end to end through the CLI: `tg orient` on a tree with a genuinely
        permission-denied subdirectory must still exit `0` (docs/CONTRACTS.md's NO exit-2
        contract for orient), while surfacing the new field."""
        (tmp_path / "keep.py").write_text("def keep():\n    pass\n", encoding="utf-8")
        denied_dir = tmp_path / "denied"
        denied_dir.mkdir()
        (denied_dir / "secret.py").write_text("def secret():\n    pass\n", encoding="utf-8")

        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        lockdown = _icacls(
            str(denied_dir),
            "/inheritance:r",
            "/grant:r",
            "SYSTEM:(OI)(CI)F",
            "Administrators:(OI)(CI)F",
        )
        assert lockdown.returncode == 0, lockdown.stderr
        try:
            try:
                list(os.scandir(denied_dir))
            except OSError:
                pass
            else:
                pytest.skip("icacls lockdown did not deny this process -- fixture is vacuous")

            result = CliRunner().invoke(app, ["orient", str(tmp_path), "--json"])

            assert result.exit_code == 0, result.output
            payload = _json.loads(result.stdout)
            assert payload.get("incomplete_reason_class") == "unreadable_path"
            assert payload.get("partial") is True
        finally:
            if user:
                _icacls(str(denied_dir), "/grant", f"{user}:(OI)(CI)F")
            else:
                _icacls(str(denied_dir), "/reset")


class TestOrientDeadlineAndUnreadablePathConsistency:
    """Independent-gate non-blocking fold-in (item 7): `build_orient_capsule_from_map`'s #200
    post-map deadline catch-all runs AFTER the unreadable-path block and unconditionally
    overwrites `partial_reason`. Before this fix, that left `partial_reason == "deadline"`
    while `incomplete_reason_class` was frozen at `"unreadable_path"` from the earlier block --
    two agents branching on the two different fields of the SAME payload got different answers.
    The fix makes both agree (`"deadline"` wins, since it fired last/most-recently) while
    APPENDING the unreadable-path fact into `incomplete_reason` instead of silently dropping
    it."""

    def test_deadline_catchall_overwrites_class_to_agree_with_partial_reason(
        self, tmp_path: Path
    ) -> None:
        from tensor_grep.cli.orient_capsule import build_orient_capsule_from_map
        from tensor_grep.cli.repo_map import build_repo_map

        (tmp_path / "keep.py").write_text("def keep():\n    pass\n", encoding="utf-8")

        # Build a real `rm` that already carries `unreadable_paths` (no deadline applied to
        # the WALK itself, so the denied subdir is genuinely reached and recorded).
        denied_dir = tmp_path / "denied"
        denied_dir.mkdir()
        (denied_dir / "secret.py").write_text("def secret():\n    pass\n", encoding="utf-8")
        real_scandir = os.scandir

        def _fake_scandir(path="."):
            if os.fspath(path) == os.fspath(denied_dir):
                raise PermissionError(13, "Permission denied", str(denied_dir))
            return real_scandir(path)

        import tensor_grep.cli.repo_map as repo_map_mod

        original_scandir = repo_map_mod.os.scandir
        repo_map_mod.os.scandir = _fake_scandir
        try:
            rm = build_repo_map(tmp_path, max_repo_files=1000)
        finally:
            repo_map_mod.os.scandir = original_scandir
        assert "unreadable_paths" in rm, "test setup: rm must carry unreadable_paths"

        # Now build the capsule from that already-truncated `rm` with an ALREADY-EXPIRED
        # `deadline_monotonic` (0.0 <= any real time.monotonic() call) so the #200 post-map
        # catch-all fires deterministically without racing the real wall clock
        # (anti-hang-test-protocol: dependency injection over real-time racing).
        payload = build_orient_capsule_from_map(rm, max_tokens=500, deadline_monotonic=0.0)

        assert payload["partial"] is True
        assert payload["partial_reason"] == "deadline"
        # The fix: incomplete_reason_class agrees with partial_reason instead of staying
        # frozen at the earlier "unreadable_path" value.
        assert payload["incomplete_reason_class"] == "deadline"
        # The earlier unreadable-path fact must survive in the text, not be silently dropped.
        assert "unreadable" in payload["incomplete_reason"].lower()
        assert "deadline" in payload["incomplete_reason"].lower()
