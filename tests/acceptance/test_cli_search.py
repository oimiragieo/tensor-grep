import subprocess
import pytest

pytestmark = pytest.mark.acceptance

class TestCLISearch:
    def test_should_find_pattern_in_log_file(self, sample_log_file):
        """OUTER LOOP RED: The simplest possible E2E test."""
        result = subprocess.run(
            ["tg", "search", "ERROR", str(sample_log_file)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "ERROR" in result.stdout
        assert result.stdout.count("\n") == 2  # Two ERROR lines

    def test_should_exit_1_when_no_matches(self, sample_log_file):
        result = subprocess.run(
            ["tg", "search", "NONEXISTENT", str(sample_log_file)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
