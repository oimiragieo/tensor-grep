"""Task #276 slice 1: `tg search --cpu`/`--force-cpu` used the same `DirectoryScanner` walk as
every other command, but NOTHING consumed its `scan_truncated`/`scan_truncation_cause` signal on
this route -- a permission-denied subdirectory (or the scanner's own defensive entry-count cap)
silently narrowed the candidate-file list with no envelope key, no stderr line, and exit `0`. The
`rg`-backend route already reported this correctly (`result_incomplete` + `incomplete_reason`,
exit `2`, via rg's own soft per-path exit code); this file pins the CPU/native route to the same
contract, plus the shared `incomplete_reason_class` vocabulary (task #276 slice 1).

Every test in this file FAILS on pre-fix `main` (the CPU route silently exits `0` with a smaller-
than-expected match/file set and no `result_incomplete` key) and PASSES after wiring
`scanner.scan_truncated` into `all_results` in `cli/main.py`'s CPU/native search branch.
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app


def _icacls(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["icacls", *args], capture_output=True, text=True)


def _invoke_cpu_search(tmp_path: Path, monkeypatch, *extra_args: str):
    # Force the Python bootstrap path (no native binary delegation) so this test exercises the
    # Python `search_command` logic directly -- matches this test module's sibling
    # `test_cli_modes.py`'s own `--cpu` convention (e.g. line 649's
    # `resolve_native_tg_binary` monkeypatch).
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    return CliRunner().invoke(
        app,
        ["search", "needle", str(tmp_path), "--cpu", "--json", *extra_args],
    )


class TestCpuRouteUnreadablePath:
    @pytest.mark.skipif(sys.platform != "win32", reason="icacls is Windows-only")
    def test_windows_real_unreadable_dir_flags_incomplete_and_exits_2(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        (ok_dir / "hit.py").write_text("needle\n", encoding="utf-8")
        denied_dir = tmp_path / "denied"
        denied_dir.mkdir()
        (denied_dir / "hit.py").write_text("needle\n", encoding="utf-8")

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

            result = _invoke_cpu_search(tmp_path, monkeypatch)

            assert result.exit_code == 2, result.output
            payload = _json.loads(result.stdout)
            assert payload["result_incomplete"] is True
            assert payload["incomplete_reason_class"] == "unreadable_path"
            assert "incomplete_reason" in payload
            # The readable subtree's match must still be present -- partial != empty.
            assert any("ok" in path and "hit.py" in path for path in payload["matched_file_paths"])
        finally:
            if user:
                _icacls(str(denied_dir), "/grant", f"{user}:(OI)(CI)F")
            else:
                _icacls(str(denied_dir), "/reset")

    def test_monkeypatched_scanner_error_flags_incomplete_and_exits_2_platform_agnostic(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Platform-agnostic discriminator (see test_directory_scanner_hardening.py's sibling
        test for the rationale): inject a real `PermissionError` into `os.walk`'s `onerror`
        callback via a monkeypatched `os.walk`, exercising the REAL `DirectoryScanner` +
        `cli/main.py` consumption wiring end to end through the actual CLI command."""
        (tmp_path / "hit.py").write_text("needle\n", encoding="utf-8")

        import tensor_grep.io.directory_scanner as ds_module

        real_walk = os.walk

        def _fake_walk(top, onerror=None, **kwargs):
            if onerror is not None:
                onerror(PermissionError(13, "Permission denied", str(tmp_path / "denied")))
            yield from real_walk(top, onerror=onerror, **kwargs)

        monkeypatch.setattr(ds_module.os, "walk", _fake_walk)

        result = _invoke_cpu_search(tmp_path, monkeypatch)

        assert result.exit_code == 2, result.output
        payload = _json.loads(result.stdout)
        assert payload["result_incomplete"] is True
        assert payload["incomplete_reason_class"] == "unreadable_path"
        assert "unreadable" in payload["incomplete_reason"].lower()
        # The readable file's match is still reported -- partial results are KEPT, not discarded.
        assert payload["total_matches"] == 1

    def test_scan_limit_cap_flags_incomplete_with_scan_limit_class(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The scanner's OWN pre-existing defensive entry-count budget
        (`TG_DIR_SCAN_MAX_ENTRIES`) was ALSO silent on this route before this fix -- distinct
        from the unreadable-path cause, must classify as `"scan_limit"` (budget-fixable), not
        `"unreadable_path"` (not budget-fixable)."""
        for i in range(20):
            sub = tmp_path / f"d{i}"
            sub.mkdir()
            (sub / "hit.py").write_text("needle\n", encoding="utf-8")

        monkeypatch.setenv("TG_DIR_SCAN_MAX_ENTRIES", "3")
        result = _invoke_cpu_search(tmp_path, monkeypatch)

        assert result.exit_code == 2, result.output
        payload = _json.loads(result.stdout)
        assert payload["result_incomplete"] is True
        assert payload["incomplete_reason_class"] == "scan_limit"

    def test_complete_cpu_scan_omits_incompleteness_keys_and_exits_0(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Omit-when-complete control arm: a `--cpu --json` search with nothing truncated must
        stay byte-identical to today -- no new keys, exit `0`."""
        (tmp_path / "hit.py").write_text("needle\n", encoding="utf-8")

        result = _invoke_cpu_search(tmp_path, monkeypatch)

        assert result.exit_code == 0, result.output
        payload = _json.loads(result.stdout)
        assert "result_incomplete" not in payload
        assert "incomplete_reason" not in payload
        assert "incomplete_reason_class" not in payload

    def test_bare_scanner_attribute_reads_never_silently_swallow_a_missing_attribute(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Independent-gate BLOCKING 1: `cli/main.py`'s CPU-route consumption reads
        `scanner.scan_truncated`/etc. as BARE attribute reads (not `getattr(..., False)`) --
        deliberately, because this is the ONLY signal on this branch and a silent `False`
        default would resurrect the exact fail-open silence this fix exists to close. Prove it:
        a scanner-like object missing the attribute must raise `AttributeError` (surfacing as a
        genuine, loud test/production failure) instead of being swallowed into a false "nothing
        truncated" reading."""
        (tmp_path / "hit.py").write_text("needle\n", encoding="utf-8")

        import tensor_grep.cli.main as cli_main

        class _AttributelessScanner:
            def __init__(self, config=None):
                pass

            def walk(self, path):
                yield str(tmp_path / "hit.py")

        monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
        monkeypatch.setattr(
            "tensor_grep.io.directory_scanner.DirectoryScanner", _AttributelessScanner
        )

        with pytest.raises(AttributeError, match="scan_truncated"):
            CliRunner().invoke(
                app,
                ["search", "needle", str(tmp_path), "--cpu", "--json"],
                catch_exceptions=False,
            )

    def test_unrecognized_scan_truncation_cause_raises_loudly_not_silently_mislabeled(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Independent-gate non-blocking fold-in (item 5): `scan_truncation_cause` is currently
        only ever `"max-scan-entries"` or `"unreadable_path"` by construction
        (`directory_scanner.py`), but the CPU-route classifier's `else` branch must fail LOUDLY
        on an unrecognized third cause rather than silently mislabeling it `"unreadable_path"` --
        `incomplete_reason_class` is a documented closed vocabulary; guessing a wrong member is
        worse than crashing."""
        (tmp_path / "hit.py").write_text("needle\n", encoding="utf-8")

        import tensor_grep.cli.main as cli_main

        class _WeirdCauseScanner:
            def __init__(self, config=None):
                self.scan_truncated = True
                self.scan_truncation_cause = "some_future_cause_nobody_classified_yet"
                self.unreadable_path_count = 0
                self.unreadable_path_sample: list[str] = []
                self.max_scan_entries = 200_000

            def walk(self, path):
                yield str(tmp_path / "hit.py")

        monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
        monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _WeirdCauseScanner)

        with pytest.raises(AssertionError, match="some_future_cause_nobody_classified_yet"):
            CliRunner().invoke(
                app,
                ["search", "needle", str(tmp_path), "--cpu", "--json"],
                catch_exceptions=False,
            )
