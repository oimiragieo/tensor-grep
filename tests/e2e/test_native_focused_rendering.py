"""Native passthrough and Python bootstrap expose the same focused profile."""

import importlib.util
import json
import subprocess
import sys

import pytest

from tests.e2e.test_native_plain_text_parity import _require_binaries


@pytest.mark.parametrize("entry", ["native", "python"])
def test_focused_profile_through_real_frontdoor(tmp_path, entry):
    helpers, rg_binary, tg_binary = _require_binaries()
    env = helpers.build_command_env(rg_binary)
    env.update(TG_SIDECAR_PYTHON=sys.executable, TG_SESSION_DAEMON_AUTOSTART="0")
    block = 'def calculate(total):\n    """Calculate invoices."""\n'
    block += "".join(f"    setup_{i} = {i}\n" for i in range(20))
    block += "    return total # negative\n"
    (tmp_path / "invoice.py").write_text(block, encoding="utf-8")
    prefix = [str(tg_binary)] if entry == "native" else [sys.executable, "-m", "tensor_grep"]
    result = subprocess.run(
        [
            *prefix,
            "context-render",
            str(tmp_path),
            "--query",
            "calculate negative",
            "--render-profile",
            "focused",
            "--json",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["render_profile"] == "focused"
    focus = payload["sources"][0]["focus"]
    if importlib.util.find_spec("tree_sitter") and importlib.util.find_spec("tree_sitter_python"):
        assert focus["omitted_line_count"] == 20
        assert "setup_0" not in payload["sources"][0]["source"]
        assert payload["context_consistency"]["primary_symbol_truncated"]
    else:
        # Native-build CI installs runtime dependencies only; optional AST grammars
        # are deliberately absent. The same profile must expose its full fallback.
        assert focus["fallback_reason"] == "grammar_unavailable"
        assert focus["omitted_line_count"] == 0
        assert "setup_0" in payload["sources"][0]["source"]
