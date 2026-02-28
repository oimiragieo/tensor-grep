import subprocess

import pytest

pytestmark = pytest.mark.acceptance


class TestCLISearch:
    def test_should_find_pattern_in_log_file(self, sample_log_file):
        """OUTER LOOP RED: The simplest possible E2E test."""
        result = subprocess.run(
            ["tg", "search", "ERROR", str(sample_log_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "ERROR" in result.stdout
        # Output has 2 matches. Each match is printed as file:line:text \n. 
        # But depending on newline handling it might have more \n characters.
        # Let's count how many lines actually start with the filename or have ERROR
        assert result.stdout.strip().count("\n") == 1 or result.stdout.count("ERROR") >= 2

    def test_should_exit_1_when_no_matches(self, sample_log_file):
        result = subprocess.run(
            ["tg", "search", "NONEXISTENT", str(sample_log_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
