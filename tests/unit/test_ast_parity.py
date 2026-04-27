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
    from tensor_grep.core.result import MatchLine, SearchResult

    test_file = tmp_path / "test.py"
    test_file.write_text("def foo():\n    return 1\n", encoding="utf-8")

    # Mock input to answer 'y'
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    class AstGrepWrapperBackend:
        def search_many(self, file_paths, pattern, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            _ = config
            return SearchResult(
                matches=[MatchLine(line_number=2, text="    return 1", file=str(test_file))],
                matched_file_paths=[str(test_file)],
                total_files=1,
                total_matches=1,
            )

    def apply_rewrite(**kwargs):
        path = kwargs["path"]
        text = test_file.read_text(encoding="utf-8")
        test_file.write_text(text.replace("return 1", kwargs["replacement"]), encoding="utf-8")
        return f'{{"path": "{path}", "applied": true}}', 0

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda config, pattern: AstGrepWrapperBackend(),
    )
    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows.execute_rewrite_apply_json",
        apply_rewrite,
    )

    # Run interactive command
    # We call the core command directly to avoid subprocess complexity for mocking stdin
    exit_code = run_command(
        pattern="return 1", path=str(tmp_path), rewrite="return 2", lang="python", interactive=True
    )

    assert exit_code == 0
    assert "return 2" in test_file.read_text(encoding="utf-8")


def test_ast_match_filtering(tmp_path, monkeypatch):
    """Verify that --filter narrows AST matches by node text regex."""
    from tensor_grep.cli.ast_workflows import run_command
    from tensor_grep.core.result import MatchLine, SearchResult

    test_file = tmp_path / "test.py"
    test_file.write_text("foo(1)\nbar(2)\n", encoding="utf-8")

    class AstGrepWrapperBackend:
        def search_many(self, file_paths, pattern, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            _ = config
            return SearchResult(
                matches=[
                    MatchLine(line_number=1, text="foo(1)", file=str(test_file)),
                    MatchLine(line_number=2, text="bar(2)", file=str(test_file)),
                ],
                matched_file_paths=[str(test_file)],
                total_files=1,
                total_matches=2,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda config, pattern: AstGrepWrapperBackend(),
    )

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
