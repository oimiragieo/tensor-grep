"""Dogfood 1.23.0: `tg context-render` (and `tg session context-render`) defaulted --max-tokens to
None -> an unbounded ~800KB prompt bundle. It must bound by default (mirroring `tg context`, which
caps at 16000), with 0 as the explicit unbounded opt-out."""

import json

from typer.testing import CliRunner

from tensor_grep.cli.main import app


def _project(tmp_path):
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "payments.py").write_text(
        "def create_invoice(total):\n    return total + 1\n", encoding="utf-8"
    )
    return project


def test_context_render_bounds_by_default(tmp_path):
    project = _project(tmp_path)
    result = CliRunner().invoke(app, ["context-render", str(project), "create invoice", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    # default max_tokens is now the 16000 bound, not None (unbounded ~800KB).
    assert payload["max_tokens"] == 16000


def test_context_render_max_tokens_zero_is_unbounded(tmp_path):
    project = _project(tmp_path)
    result = CliRunner().invoke(
        app, ["context-render", str(project), "create invoice", "--json", "--max-tokens", "0"]
    )
    assert result.exit_code == 0, result.output
    # 0 = opt-out; normalized to None (unbounded) downstream.
    assert json.loads(result.stdout)["max_tokens"] is None
