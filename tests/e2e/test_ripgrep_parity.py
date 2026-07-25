import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers.byte_parity import run_bytes  # noqa: E402

pytestmark = pytest.mark.characterization
PATTERNS = ["ERROR", "INFO", r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", "GET /api"]


class TestRipgrepParity:
    @pytest.mark.parametrize("pattern", PATTERNS)
    def test_output_lines_match_ripgrep(self, sample_log_file, rg_path, pattern):
        # Raw bytes -- no text=True. Splitting on a bare b"\n" (not str.splitlines(), which
        # treats \r\n/\r/\n as equivalent) keeps any trailing \r attached to its line so a
        # real CRLF divergence between rg and tg would stay visible instead of being erased
        # before comparison (task #262).
        rg = run_bytes([rg_path, pattern, str(sample_log_file)])
        ours = run_bytes(["tg", pattern, str(sample_log_file)])
        rg_lines = sorted(line for line in rg.stdout.split(b"\n") if line)
        our_lines = sorted(line for line in ours.stdout.split(b"\n") if line)
        assert our_lines == rg_lines
