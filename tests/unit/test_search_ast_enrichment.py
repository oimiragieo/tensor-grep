import json
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.main import app

runner = CliRunner()


def test_search_with_enrich_ast():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "sample.py"
        test_file.write_text(
            """def outer_function():
    target_needle = 123
    return target_needle
""",
            encoding="utf-8",
        )

        res = runner.invoke(app, ["search", "target_needle", str(tmpdir), "--enrich-ast", "--json"])
        assert res.exit_code == 0
        data = json.loads(res.stdout)
        assert "matches" in data
        assert len(data["matches"]) >= 1
        first_match = data["matches"][0]
        assert "container" in first_match
        assert first_match["container"]["name"] == "outer_function"
        assert first_match["container"]["kind"] in ("function", "def")


def test_cpu_enrichment_preserves_regex_citation_evidence(tmp_path, monkeypatch):
    source = tmp_path / "sample.py"
    source.write_text("def target_needle():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(
        CPUBackend,
        "_rust_match_set",
        lambda *_args, **_kwargs: [(1, "def target_needle():")],
    )

    result = runner.invoke(
        app,
        [
            "search",
            "target_needle|missing",
            str(tmp_path),
            "--cpu",
            "--enrich-ast",
            "--citations",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["citations"] == [
        {"path": "sample.py", "start_line": 1, "end_line": 1}
    ]


def test_bootstrap_cli_search_enrich_ast():
    import subprocess
    import sys

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "sample.py"
        test_file.write_text(
            """def helper_worker():
    found_var = "needle_value"
    return found_var
""",
            encoding="utf-8",
        )

        cmd = [
            sys.executable,
            "-m",
            "tensor_grep",
            "search",
            "needle_value",
            str(tmpdir),
            "--enrich-ast",
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert "matches" in data
        assert len(data["matches"]) >= 1
        assert data["matches"][0]["container"]["name"] == "helper_worker"
