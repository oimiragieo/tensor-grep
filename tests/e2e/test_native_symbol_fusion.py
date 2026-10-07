"""Exercise opt-in fusion through the native sidecar and Python bootstrap."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.test_native_plain_text_parity import _require_binaries


@pytest.mark.parametrize("entry", ["native", "python"])
def test_symbol_fusion_through_real_frontdoor(tmp_path, entry):
    helpers, rg_binary, tg_binary = _require_binaries()
    env = helpers.build_command_env(rg_binary)
    env.update(
        TG_SIDECAR_PYTHON=sys.executable,
        TG_SESSION_DAEMON_AUTOSTART="0",
        TG_RRF_SYMBOLS="1",
        TG_SEMANTIC_MODEL_DIR=str(tmp_path / "absent-model"),
    )
    env.pop("TG_RRF_CHANNELS", None)
    (tmp_path / "decoy.py").write_text("# " + "target_func " * 12 + "\n", encoding="utf-8")
    (tmp_path / "target.py").write_text("def target_func():\n    pass\n", encoding="utf-8")
    prefix = [str(tg_binary)] if entry == "native" else [sys.executable, "-m", "tensor_grep"]
    result = subprocess.run(
        [*prefix, "find", "target_func", str(tmp_path), "--json", "--limit", "1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload["matches"]) == 1
    assert Path(payload["matches"][0]["file"]).name == "target.py"
    assert payload["rank_fusion"]["ast_symbols"]["matched_symbols"] == ["target_func"]
    assert payload["rank_fusion"]["dense"]["available"] is False
    assert payload["rank_fallback_reason"]
