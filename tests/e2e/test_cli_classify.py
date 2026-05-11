import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.acceptance


class TestCLIClassify:
    def test_should_classify_log_lines(self, sample_log_file):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tensor_grep.cli.main",
                "classify",
                "--format",
                "json",
                str(sample_log_file),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "classifications" in data
        assert any(
            c["label"] in ["error", "info", "warn", "warning"] for c in data["classifications"]
        )
        first = data["classifications"][0]
        assert first["file"] == str(sample_log_file.resolve())
        assert first["path"] == str(sample_log_file.resolve())
        assert first["line"] == 1
        assert "snippet" in first
