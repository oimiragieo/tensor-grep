import os
import subprocess
import sys

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


class _CountingWrapperBackend(_FakeWrapperBackend):
    init_count = 0

    def __init__(self):
        type(self).init_count += 1


class _CountingAstBackend:
    init_count = 0

    def __init__(self):
        type(self).init_count += 1

    def is_available(self):
        return True

    def search(self, file_path, pattern, config=None) -> SearchResult:
        _ = file_path
        _ = pattern
        _ = config
        return SearchResult(matches=[], total_files=0, total_matches=0)


def test_scan_command_should_reuse_backend_selection_per_rule(monkeypatch, tmp_path, capsys):
    from tensor_grep.cli.ast_workflows import scan_command

    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        _FakeUnavailableAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _CountingWrapperBackend,
    )
    _CountingWrapperBackend.init_count = 0
    _CountingWrapperBackend.search_project_calls = 0
    _CountingWrapperBackend.search_many_calls = 0

    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\nlanguage: python\n",
        encoding="utf-8",
    )
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "rule_a.yml").write_text(
        "id: rule-a\nlanguage: python\nrule:\n  pattern: ERROR_A\n",
        encoding="utf-8",
    )
    (tmp_path / "rules" / "rule_b.yml").write_text(
        "id: rule-b\nlanguage: python\nrule:\n  pattern: ERROR_B\n",
        encoding="utf-8",
    )
    (tmp_path / "a.py").write_text("ERROR_A\nERROR_B\n", encoding="utf-8")

    exit_code = scan_command(str(tmp_path / "sgconfig.yml"))

    _ = capsys.readouterr()
    assert exit_code == 0
    assert _CountingWrapperBackend.init_count == 1


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


def test_ast_workflows_import_should_not_eagerly_load_directory_scanner():
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"src{os.pathsep}{existing}" if existing else "src"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import tensor_grep.cli.ast_workflows; "
                "print('tensor_grep.io.directory_scanner' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.stdout.strip() == "False"


def test_test_command_should_reuse_backend_selection_for_rule_linked_cases(
    monkeypatch, tmp_path, capsys
):
    from tensor_grep.cli.ast_workflows import test_command

    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        _CountingAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _FakeUnavailableAstBackend,
    )
    _CountingAstBackend.init_count = 0

    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
        encoding="utf-8",
    )
    (tmp_path / "rules").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "rules" / "native.yml").write_text(
        "id: native-rule\nlanguage: python\nrule:\n  pattern: identifier\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "cases.yml").write_text(
        "tests:\n"
        "  - id: case-a\n"
        "    ruleId: native-rule\n"
        "    valid:\n"
        "      - 'pass'\n"
        "  - id: case-b\n"
        "    ruleId: native-rule\n"
        "    valid:\n"
        "      - 'pass'\n",
        encoding="utf-8",
    )

    exit_code = test_command(str(tmp_path / "sgconfig.yml"))

    _ = capsys.readouterr()
    assert exit_code == 0
    assert _CountingAstBackend.init_count == 1
