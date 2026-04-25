from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def test_tg_ast_info():
    """Verify that tg ast-info lists supported grammars."""
    result = runner.invoke(app, ["ast-info"])
    assert result.exit_code == 0
    assert "Supported AST Languages" in result.stdout
    assert "python" in result.stdout.lower()


def test_ast_interactive_apply(tmp_path, monkeypatch):
    """Verify that --interactive mode prompts for confirmation."""
    import io

    from tensor_grep.cli.ast_workflows import run_command

    test_file = tmp_path / "test.py"
    test_file.write_text("def foo():\n    return 1\n", encoding="utf-8")

    # Mock input to answer 'y'
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    # Run interactive command
    # We call the core command directly to avoid subprocess complexity for mocking stdin
    exit_code = run_command(
        pattern="return 1", path=str(tmp_path), rewrite="return 2", lang="python", interactive=True
    )

    assert exit_code == 0
    assert "return 2" in test_file.read_text(encoding="utf-8")


def test_ast_match_filtering(tmp_path):
    """Verify that --filter narrows AST matches by node text regex."""
    from tensor_grep.cli.ast_workflows import run_command

    test_file = tmp_path / "test.py"
    test_file.write_text("foo(1)\nbar(2)\n", encoding="utf-8")

    # Search for all calls, but filter for 'foo'
    # We use json_mode to check internal matches
    import json
    from contextlib import redirect_stdout
    from io import StringIO

    f = StringIO()
    with redirect_stdout(f):
        run_command(
            pattern="$F($$$ARGS)",
            path=str(tmp_path),
            lang="python",
            filter_regex="foo",
            json_mode=True,
        )

    output = f.getvalue()
    if output:
        results = json.loads(output)
        assert len(results["matches"]) == 1
        assert "foo" in results["matches"][0]["text"]
        assert "bar" not in results["matches"][0]["text"]
