import subprocess
import pytest

pytestmark = pytest.mark.acceptance

class TestCLIWithoutGPU:
    def test_should_work_with_cpu_flag(self, sample_log_file):
        result = subprocess.run(
            ["tg", "search", "--cpu", "ERROR", str(sample_log_file)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "ERROR" in result.stdout

    def test_should_output_json(self, sample_log_file):
        result = subprocess.run(
            ["tg", "search", "--cpu", "--format", "json", "ERROR", str(sample_log_file)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        import json
        data = json.loads(result.stdout)
        assert "matches" in data
