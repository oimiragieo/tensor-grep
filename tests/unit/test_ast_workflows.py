from tensor_grep.core.result import SearchResult


class _FakeUnavailableAstBackend:
    def is_available(self):
        return False


class _FakeWrapperBackend:
    search_project_calls = 0
    search_many_calls = 0

    def is_available(self):
        return True

    def search_many(self, file_paths, pattern, config=None) -> SearchResult:
        _ = file_paths
        _ = pattern
        _ = config
        _FakeWrapperBackend.search_many_calls += 1
        return SearchResult(matches=[], total_files=0, total_matches=0)

    def search_project(self, root_path: str, config_path: str) -> dict[str, SearchResult]:
        _ = root_path
        _ = config_path
        _FakeWrapperBackend.search_project_calls += 1
        return {
            "error-rule": SearchResult(
                matches=[],
                matched_file_paths=["a.py"],
                total_files=1,
                total_matches=1,
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_project_scan_json",
            )
        }


def test_scan_command_should_use_wrapper_project_fast_path(monkeypatch, tmp_path, capsys):
    from tensor_grep.cli.ast_workflows import scan_command

    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        _FakeUnavailableAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _FakeWrapperBackend,
    )
    _FakeWrapperBackend.search_project_calls = 0
    _FakeWrapperBackend.search_many_calls = 0

    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\nlanguage: python\n",
        encoding="utf-8",
    )
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "error.yml").write_text(
        "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
        encoding="utf-8",
    )
    (tmp_path / "a.py").write_text("ERROR in file\n", encoding="utf-8")

    exit_code = scan_command(str(tmp_path / "sgconfig.yml"))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[scan] rule=error-rule lang=python matches=1 files=1" in captured.out
    assert _FakeWrapperBackend.search_project_calls == 1
    assert _FakeWrapperBackend.search_many_calls == 0
