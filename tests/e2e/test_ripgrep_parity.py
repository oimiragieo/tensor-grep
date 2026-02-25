import subprocess

import pytest

pytestmark = pytest.mark.characterization
PATTERNS = ["ERROR", "INFO", r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", "GET /api"]


class TestRipgrepParity:
    @pytest.mark.parametrize("pattern", PATTERNS)
    def test_output_lines_match_ripgrep(self, sample_log_file, rg_path, pattern):
        rg = subprocess.run(
            [rg_path, pattern, str(sample_log_file)],
            capture_output=True,
            text=True,
        )
        ours = subprocess.run(
            ["tg", pattern, str(sample_log_file)],
            capture_output=True,
            text=True,
        )
        rg_lines = sorted(rg.stdout.strip().splitlines())
        our_lines = sorted(ours.stdout.strip().splitlines())
        assert our_lines == rg_lines
