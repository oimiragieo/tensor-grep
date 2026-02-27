import json
import subprocess

import pytest

pytestmark = pytest.mark.acceptance


class TestCLIClassify:
    def test_should_classify_log_lines(self, sample_log_file):
        result = subprocess.run(
            ["tg", "classify", "--format", "json", str(sample_log_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "classifications" in data
        assert any(c["label"] in ["error", "info", "warn", "warning"] for c in data["classifications"])
