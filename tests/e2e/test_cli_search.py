import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.acceptance


class TestCLISearch:
    def _write_fake_rg_json_binary(self, tmp_path):
        fake_rg_py = tmp_path / "fake_rg_json.py"
        fake_rg_py.write_text(
            "\n".join([
                "import json",
                "import sys",
                "path = sys.argv[-1] if len(sys.argv) > 1 else 'sample.log'",
                "print(json.dumps({'type': 'begin', 'data': {'path': {'text': path}}}))",
                "print(json.dumps({'type': 'match', 'data': {'path': {'text': path}, 'lines': {'text': 'ERROR sample\\n'}, 'line_number': 1, 'absolute_offset': 0, 'submatches': []}}))",
                "print(json.dumps({'type': 'summary', 'data': {'elapsed_total': {'human': '0s', 'nanos': 0, 'secs': 0}, 'stats': {'bytes_printed': 0, 'bytes_searched': 0, 'elapsed': {'human': '0s', 'nanos': 0, 'secs': 0}, 'matched_lines': 1, 'matches': 1, 'searches': 1, 'searches_with_match': 1}}}))",
            ]),
            encoding="utf-8",
        )
        if os.name == "nt":
            fake_rg = tmp_path / "rg.cmd"
            fake_rg.write_text(
                f'@echo off\r\n"{sys.executable}" "{fake_rg_py}" %*\r\n',
                encoding="utf-8",
            )
            return fake_rg
        fake_rg = tmp_path / "rg"
        fake_rg.write_text(
            f"#!{sys.executable}\n" + fake_rg_py.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        fake_rg.chmod(0o755)
        return fake_rg

    def test_should_find_pattern_in_log_file(self, sample_log_file):
        """OUTER LOOP RED: The simplest possible E2E test."""
        result = subprocess.run(
            [sys.executable, "-m", "tensor_grep.cli.main", "search", "ERROR", str(sample_log_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "ERROR" in result.stdout
        assert result.stdout.count("\n") == 2  # Two ERROR lines

    def test_should_exit_1_when_no_matches(self, sample_log_file):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tensor_grep.cli.main",
                "search",
                "NONEXISTENT",
                str(sample_log_file),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1

    def test_should_emit_json_without_hanging(self, sample_log_file):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tensor_grep.cli.main",
                "search",
                "ERROR",
                str(sample_log_file),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["total_matches"] == 2

    def test_should_emit_ripgrep_json_lines_when_format_rg_is_explicit(
        self, sample_log_file, tmp_path
    ):
        fake_rg = self._write_fake_rg_json_binary(tmp_path)
        env = os.environ.copy()
        env["TG_RG_PATH"] = str(fake_rg)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tensor_grep.cli.main",
                "search",
                "--format",
                "rg",
                "--json",
                "ERROR",
                str(sample_log_file),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )

        assert result.returncode == 0
        events = [json.loads(line) for line in result.stdout.splitlines()]
        assert events[0]["type"] == "begin"
        assert any(event["type"] == "match" for event in events)
        assert events[-1]["type"] == "summary"
