import io
import json
from pathlib import Path

import pytest

from tensor_grep.backends.ast_wrapper_backend import (
    AstGrepWrapperBackend as RealAstGrepWrapperBackend,
)
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.core.result import MatchLine, SearchResult


class AstBackend:
    def is_available(self):
        return False


class AstGrepWrapperBackend:
    search_project_calls = 0
    search_many_calls = 0

    def is_available(self):
        return True

    def search(self, file_path, pattern, config=None) -> SearchResult:
        _ = file_path
        _ = pattern
        _ = config
        return SearchResult(
            matches=[],
            matched_file_paths=["a.py"],
            total_files=1,
            total_matches=1,
            routing_backend="AstGrepWrapperBackend",
        )

    def search_many(self, file_paths, pattern, config=None) -> SearchResult:
        _ = file_paths
        _ = pattern
        _ = config
        type(self).search_many_calls += 1
        return SearchResult(
            matches=[],
            matched_file_paths=["a.py"],
            total_files=1,
            total_matches=1,
            routing_backend="AstGrepWrapperBackend",
        )

    def search_project(self, root_path: str, config_path: str) -> dict[str, SearchResult]:
        _ = root_path
        _ = config_path
        type(self).search_project_calls += 1
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


class _AvailableAstBackend:
    def is_available(self):
        return True


class _UnavailableAstGrepWrapperBackend:
    def is_available(self):
        return False


class _CountingWrapperBackend(AstGrepWrapperBackend):
    init_count = 0

    def __init__(self):
        type(self).init_count += 1


@pytest.fixture(autouse=True)
def clear_ast_caches():
    from tensor_grep.cli import ast_workflows

    ast_workflows._BACKEND_AVAILABILITY.clear()
    ast_workflows._CACHED_BACKENDS.clear()
    yield
    ast_workflows._BACKEND_AVAILABILITY.clear()
    ast_workflows._CACHED_BACKENDS.clear()


def test_scan_command_should_reuse_backend_selection_per_rule(monkeypatch, tmp_path, capsys):
    from tensor_grep.cli.ast_workflows import scan_command

    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        AstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _CountingWrapperBackend,
    )
    _CountingWrapperBackend.init_count = 0
    AstGrepWrapperBackend.search_project_calls = 0
    AstGrepWrapperBackend.search_many_calls = 0

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
        AstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        AstGrepWrapperBackend,
    )
    AstGrepWrapperBackend.search_project_calls = 0
    AstGrepWrapperBackend.search_many_calls = 0

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
    assert AstGrepWrapperBackend.search_project_calls == 1
    assert AstGrepWrapperBackend.search_many_calls == 0


def test_scan_command_reports_wrapper_runtime_errors_without_traceback(
    monkeypatch, tmp_path, capsys
):
    from tensor_grep.cli.ast_workflows import scan_command

    class ErroringAstGrepWrapperBackend:
        def is_available(self):
            return True

        def search_project(self, root_path: str, config_path: str):
            _ = root_path
            _ = config_path
            raise RuntimeError("ast-grep failed with exit code 8: invalid language")

        def search_many(self, file_paths, pattern, config=None):
            _ = file_paths
            _ = pattern
            _ = config
            raise RuntimeError("ast-grep failed with exit code 8: invalid language")

    ErroringAstGrepWrapperBackend.__name__ = "AstGrepWrapperBackend"

    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        AstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        ErroringAstGrepWrapperBackend,
    )

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
    assert exit_code == 1
    assert "Error: ast-grep failed with exit code 8: invalid language" in captured.err
    assert "Traceback" not in captured.err


def test_test_command_reports_unsupported_case_language_without_traceback(tmp_path, capsys):
    from tensor_grep.cli.ast_workflows import test_command

    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
        encoding="utf-8",
    )
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "error.yml").write_text(
        "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "error-test.yml").write_text(
        "\n".join(
            [
                "tests:",
                "  - id: unsupported-language",
                "    ruleId: error-rule",
                "    language: Dart",
                "    valid:",
                "      - OK",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = test_command(str(tmp_path / "sgconfig.yml"))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Unsupported AST language Dart" in captured.err
    assert "Traceback" not in captured.err


def test_ast_project_data_cache_invalidation(tmp_path, monkeypatch):
    import time

    from tensor_grep.cli.ast_workflows import _load_ast_project_data

    # Setup mock project
    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text(
        "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n", encoding="utf-8"
    )

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    rule_file = rules_dir / "rule.yml"
    rule_file.write_text("id: rule1\npattern: OLD_PATTERN\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test.yml"
    test_file.write_text(
        'id: test1\nruleId: rule1\nvalid: ["valid"]\ninvalid: ["invalid"]\n', encoding="utf-8"
    )

    src_file = tmp_path / "a.py"
    src_file.write_text("OLD_PATTERN\n", encoding="utf-8")

    # Ensure mtimes are distinct if needed, though most OS have sub-second precision now
    time.sleep(0.1)

    # 1. Initial load (Cache Miss)
    project_cfg, rule_specs, candidate_files, test_data, _hints = _load_ast_project_data(
        str(config_path)
    )
    assert rule_specs[0]["pattern"] == "OLD_PATTERN"
    assert "a.py" in [Path(f).name for f in candidate_files]
    assert test_data[0]["cases"][0]["id"] == "test1"

    # 2. Modify rule file -> should invalidate
    time.sleep(0.1)
    rule_file.write_text("id: rule1\npattern: NEW_PATTERN\n", encoding="utf-8")
    project_cfg, rule_specs, candidate_files, test_data, _hints = _load_ast_project_data(
        str(config_path)
    )
    assert rule_specs[0]["pattern"] == "NEW_PATTERN"

    # 3. Modify test file -> should invalidate
    time.sleep(0.1)
    test_file.write_text('id: test_updated\nruleId: rule1\nvalid: ["v2"]\n', encoding="utf-8")
    project_cfg, rule_specs, candidate_files, test_data, _hints = _load_ast_project_data(
        str(config_path)
    )
    assert test_data[0]["cases"][0]["id"] == "test_updated"

    # 4. Add source file -> should invalidate (via root_dir mtime)
    time.sleep(0.1)
    (tmp_path / "b.py").write_text("content\n", encoding="utf-8")
    project_cfg, rule_specs, candidate_files, test_data, _hints = _load_ast_project_data(
        str(config_path)
    )
    assert "b.py" in [Path(f).name for f in candidate_files]

    # 4.5 Add source file in nested directory -> should invalidate (via sub-dir mtime)
    time.sleep(0.1)
    sub_dir = tmp_path / "pkg" / "sub"
    sub_dir.mkdir(parents=True)
    nested_file = sub_dir / "c.py"
    nested_file.write_text("pattern\n", encoding="utf-8")
    _project_cfg, _rule_specs, candidate_files, _test_data, _hints = _load_ast_project_data(
        str(config_path)
    )
    assert "c.py" in [Path(f).name for f in candidate_files]

    # 4.6 Remove nested source file -> should invalidate
    time.sleep(0.1)
    nested_file.unlink()
    _project_cfg, _rule_specs, candidate_files, _test_data, _hints = _load_ast_project_data(
        str(config_path)
    )
    assert "c.py" not in [Path(f).name for f in candidate_files]

    # 5. Modify config file -> should invalidate
    time.sleep(0.1)
    config_path.write_text(
        "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: javascript\n", encoding="utf-8"
    )
    project_cfg, _rule_specs, _candidate_files, _test_data, _hints = _load_ast_project_data(
        str(config_path)
    )
    assert project_cfg["language"] == "javascript"


def test_collect_candidate_files_should_include_traversed_tree_dirs(tmp_path, monkeypatch):
    from tensor_grep.cli.ast_workflows import _collect_candidate_files
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.directory_scanner import DirectoryScanner

    monkeypatch.setattr("tensor_grep.io.directory_scanner.HAS_RUST_SCANNER", False)

    nested_dir = tmp_path / "pkg" / "sub"
    nested_dir.mkdir(parents=True)
    nested_file = nested_dir / "example.py"
    nested_file.write_text("print('ok')\n", encoding="utf-8")

    scanner = DirectoryScanner(SearchConfig(ast=True, ast_prefer_native=True, lang="python"))
    candidate_files, _, tree_dirs = _collect_candidate_files(scanner, [str(tmp_path)])

    assert str(nested_file) in candidate_files
    assert str(tmp_path / "pkg") in tree_dirs
    assert str(nested_dir) in tree_dirs


def test_ast_project_data_cache_should_invalidate_when_traversed_tree_dir_changes(
    tmp_path, monkeypatch
):
    import time

    from tensor_grep.cli.ast_workflows import (
        _collect_candidate_files,
        _get_cache_dir,
        _load_ast_project_data,
    )
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.directory_scanner import DirectoryScanner

    monkeypatch.setattr("tensor_grep.io.directory_scanner.HAS_RUST_SCANNER", False)

    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text(
        "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n", encoding="utf-8"
    )

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    rule_file = rules_dir / "rule.yml"
    rule_file.write_text("id: rule1\npattern: OLD_PATTERN\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test.yml"
    test_file.write_text("id: test1\nruleId: rule1\nvalid: [ok]\n", encoding="utf-8")

    nested_dir = tmp_path / "pkg" / "sub"
    nested_dir.mkdir(parents=True)
    existing_file = nested_dir / "existing.py"
    existing_file.write_text("OLD_PATTERN\n", encoding="utf-8")

    project_cfg, rule_specs, candidate_files, test_data, hints = _load_ast_project_data(
        str(config_path)
    )

    scanner = DirectoryScanner(SearchConfig(ast=True, ast_prefer_native=True, lang="python"))
    _, _, tree_dirs = _collect_candidate_files(scanner, [str(tmp_path)])
    tree_dirs_meta = {path: Path(path).stat().st_mtime_ns for path in tree_dirs}

    cache_dir = _get_cache_dir(tmp_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "project_data_v6.json"
    cache_payload = {
        "project_cfg": {
            **project_cfg,
            "config_path": str(project_cfg["config_path"]),
            "root_dir": str(project_cfg["root_dir"]),
        },
        "rule_specs": rule_specs,
        "candidate_files": candidate_files,
        "test_data": test_data,
        "orchestration_hints": hints,
        "validation_metadata": {
            "rule_files": {str(rule_file): rule_file.stat().st_mtime_ns},
            "test_files": {str(test_file): test_file.stat().st_mtime_ns},
            "tree_dirs": tree_dirs_meta,
        },
    }
    cache_file.write_text(json.dumps(cache_payload), encoding="utf-8")

    time.sleep(0.1)
    added_file = nested_dir / "added.py"
    added_file.write_text("OLD_PATTERN\n", encoding="utf-8")

    _project_cfg, _rule_specs, refreshed_candidate_files, _test_data, _hints = (
        _load_ast_project_data(str(config_path))
    )

    assert str(added_file) in refreshed_candidate_files


def test_load_yaml_dict_recovers_from_partial_yaml_cache(tmp_path, monkeypatch):
    import yaml

    from tensor_grep.cli import ast_workflows

    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text("ruleDirs: [rules]\nlanguage: python\n", encoding="utf-8")
    monkeypatch.setattr(ast_workflows, "_YAML_MODULE", yaml)
    monkeypatch.setattr(ast_workflows, "_YAML_LOADER", None)

    payload = ast_workflows._load_yaml_dict(config_path)

    assert payload["ruleDirs"] == ["rules"]


def test_load_yaml_dict_recovers_from_stale_invalid_yaml_loader(tmp_path, monkeypatch):
    import yaml

    from tensor_grep.cli import ast_workflows

    class BrokenLoader(yaml.SafeLoader):
        DEFAULT_SCALAR_TAG = None

    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text("ruleDirs: [rules]\nlanguage: python\n", encoding="utf-8")
    monkeypatch.setattr(ast_workflows, "_YAML_MODULE", yaml)
    monkeypatch.setattr(ast_workflows, "_YAML_LOADER", BrokenLoader)

    payload = ast_workflows._load_yaml_dict(config_path)

    assert payload["ruleDirs"] == ["rules"]


def test_select_ast_backend_name_for_pattern_should_prefer_native_for_native_shapes():
    from tensor_grep.cli.ast_workflows import _select_ast_backend_name_for_pattern

    assert _select_ast_backend_name_for_pattern("(function_definition)", "python") == "AstBackend"
    assert _select_ast_backend_name_for_pattern("function_definition", "python") == "AstBackend"


def test_select_ast_backend_name_for_pattern_should_use_wrapper_for_ast_grep_patterns():
    from tensor_grep.cli.ast_workflows import _select_ast_backend_name_for_pattern

    assert _select_ast_backend_name_for_pattern("def $FUNC():", "python") == "AstGrepWrapperBackend"


def test_select_ast_backend_should_reject_wrapper_pattern_when_wrapper_is_unavailable(
    monkeypatch,
):
    from tensor_grep.cli.ast_workflows import _select_ast_backend_for_pattern

    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        _AvailableAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _UnavailableAstGrepWrapperBackend,
    )

    with pytest.raises(ConfigurationError, match="ast-grep"):
        _select_ast_backend_for_pattern(
            SearchConfig(
                ast=True,
                ast_prefer_native=True,
                lang="python",
                query_pattern="return 1",
            ),
            "return 1",
            {},
        )


def test_run_command_interactive_apply_should_return_error_when_apply_fails(
    monkeypatch, tmp_path, capsys
):
    from tensor_grep.cli.ast_workflows import run_command

    test_file = tmp_path / "test.py"
    test_file.write_text("def foo():\n    return 1\n", encoding="utf-8")

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

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda config, pattern: AstGrepWrapperBackend(),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows.execute_rewrite_apply_json",
        lambda **kwargs: ('{"error": {"message": "apply failed"}}', 1),
    )

    exit_code = run_command(
        pattern="return 1",
        path=str(tmp_path),
        rewrite="return 2",
        lang="python",
        interactive=True,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Error applying rewrite" in captured.err
    assert "return 1" in test_file.read_text(encoding="utf-8")


def test_run_command_should_fall_back_for_unencodable_ast_output(monkeypatch):
    import tensor_grep.cli.ast_workflows as ast_workflows
    from tensor_grep.cli.ast_workflows import run_command

    class AstGrepWrapperBackend:
        def search_many(self, file_paths, pattern, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            _ = config
            return SearchResult(
                matches=[MatchLine(line_number=1, text="def 漢():", file="sample.py")],
                matched_file_paths=["sample.py"],
                total_files=1,
                total_matches=1,
            )

    class _Buffer:
        def __init__(self):
            self.payload = bytearray()

        def write(self, data: bytes) -> int:
            self.payload.extend(data)
            return len(data)

        def flush(self) -> None:
            return None

    class _Cp1252Stdout:
        encoding = "cp1252"

        def __init__(self):
            self.buffer = _Buffer()
            self.text_writes: list[str] = []

        def write(self, text: str) -> int:
            text.encode(self.encoding)
            self.text_writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    stdout = _Cp1252Stdout()
    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda config, pattern: AstGrepWrapperBackend(),
    )
    monkeypatch.setattr(ast_workflows.sys, "stdout", stdout)

    exit_code = run_command("def $FUNC():", path="sample.py", lang="python")

    assert exit_code == 0
    assert any(
        "Executing ast-grep structural matching run..." in chunk for chunk in stdout.text_writes
    )
    assert stdout.buffer.payload.decode("utf-8") == "1:def 漢():\n"


def test_run_command_should_escape_unencodable_ast_output_without_binary_buffer(monkeypatch):
    import tensor_grep.cli.ast_workflows as ast_workflows
    from tensor_grep.cli.ast_workflows import run_command
    from tensor_grep.core.result import MatchLine

    class AstGrepWrapperBackend:
        def search_many(self, file_paths, pattern, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            _ = config
            return SearchResult(
                matches=[MatchLine(line_number=1, text="def 漢():", file="sample.py")],
                matched_file_paths=["sample.py"],
                total_files=1,
                total_matches=1,
            )

    class _Cp1252TextOnlyStdout:
        encoding = "cp1252"

        def __init__(self):
            self.text_writes: list[str] = []

        def write(self, text: str) -> int:
            text.encode(self.encoding)
            self.text_writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    stdout = _Cp1252TextOnlyStdout()
    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda config, pattern: AstGrepWrapperBackend(),
    )
    monkeypatch.setattr(ast_workflows.sys, "stdout", stdout)

    exit_code = run_command("def $FUNC():", path="sample.py", lang="python")

    assert exit_code == 0
    assert any(
        "Executing ast-grep structural matching run..." in chunk for chunk in stdout.text_writes
    )
    assert "1:def \\u6f22():\n" in stdout.text_writes


def test_run_command_files_with_matches_outputs_only_paths(tmp_path, monkeypatch, capsys):
    """Verify AST file-list mode stays quiet and deduplicates matched files."""
    from tensor_grep.cli.ast_workflows import run_command

    first = tmp_path / "first.py"
    second = tmp_path / "second.py"

    class AstGrepWrapperBackend:
        def search_many(self, file_paths, pattern, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            _ = config
            return SearchResult(
                matches=[
                    MatchLine(line_number=1, text="class First: pass", file=str(first)),
                    MatchLine(line_number=2, text="class FirstAgain: pass", file=str(first)),
                    MatchLine(line_number=1, text="class Second: pass", file=str(second)),
                ],
                matched_file_paths=[str(first), str(second)],
                total_files=2,
                total_matches=3,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda config, pattern: AstGrepWrapperBackend(),
    )

    exit_code = run_command(
        pattern="class $NAME: $$$BODY",
        path=str(tmp_path),
        lang="python",
        files_with_matches=True,
    )

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [str(first), str(second)]


def test_run_command_returns_one_when_read_only_ast_has_no_matches(monkeypatch, capsys):
    from tensor_grep.cli import ast_workflows
    from tensor_grep.cli.ast_workflows import run_command

    class AstGrepWrapperBackend:
        def search_many(self, file_paths, pattern, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            _ = config
            return SearchResult(matches=[], matched_file_paths=[], total_files=1, total_matches=0)

    monkeypatch.setattr(
        ast_workflows,
        "_select_ast_backend_for_pattern",
        lambda config, pattern: AstGrepWrapperBackend(),
    )

    exit_code = run_command("calculateTotal($$$)", path=".", lang="js")

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Executing ast-grep structural matching run..." in captured.out


@pytest.mark.skipif(
    not RealAstGrepWrapperBackend().is_available(),
    reason="requires ast-grep binary",
)
def test_run_command_matches_javascript_call_pattern(tmp_path, capsys):
    from tensor_grep.cli.ast_workflows import run_command

    source = tmp_path / "app.js"
    source.write_text(
        "function calculateTotal(items) {\n"
        "  return items.length;\n"
        "}\n"
        "const total = calculateTotal(items);\n",
        encoding="utf-8",
    )

    exit_code = run_command("calculateTotal($$$)", path=str(tmp_path), lang="js")

    assert exit_code == 0
    assert "calculateTotal(items)" in capsys.readouterr().out


def test_run_command_should_force_wrapper_for_ast_grep_semantic_options(monkeypatch):
    from tensor_grep.cli import ast_workflows
    from tensor_grep.cli.ast_workflows import run_command

    seen: dict[str, object] = {}

    class AstGrepWrapperBackend:
        def search_many(self, file_paths, pattern, config=None) -> SearchResult:
            seen["file_paths"] = file_paths
            seen["pattern"] = pattern
            seen["config"] = config
            return SearchResult(matches=[], matched_file_paths=[], total_files=0, total_matches=0)

    def fake_select(config, pattern):
        seen["selected_config"] = config
        seen["selected_pattern"] = pattern
        return AstGrepWrapperBackend()

    monkeypatch.setattr(ast_workflows, "_select_ast_backend_for_pattern", fake_select)

    exit_code = run_command(
        pattern="print($A)",
        path="src",
        lang="python",
        selector="call",
        strictness="relaxed",
        globs=["*.py", "!generated/**"],
        json_mode=True,
    )

    assert exit_code == 1
    config = seen["config"]
    assert config.ast_prefer_native is False
    assert config.ast_selector == "call"
    assert config.ast_strictness == "relaxed"
    assert config.glob == ["*.py", "!generated/**"]
    assert seen["file_paths"] == ["src"]


def test_run_command_should_forward_stdin_without_default_path(monkeypatch):
    import io

    from tensor_grep.cli import ast_workflows
    from tensor_grep.cli.ast_workflows import run_command

    seen: dict[str, object] = {}

    class AstGrepWrapperBackend:
        def search_many(self, file_paths, pattern, config=None) -> SearchResult:
            seen["file_paths"] = file_paths
            seen["pattern"] = pattern
            seen["config"] = config
            return SearchResult(matches=[], matched_file_paths=[], total_files=0, total_matches=0)

    monkeypatch.setattr(
        ast_workflows,
        "_select_ast_backend_for_pattern",
        lambda config, pattern: AstGrepWrapperBackend(),
    )
    monkeypatch.setattr(ast_workflows.sys, "stdin", io.StringIO("print('hello')\n"))

    exit_code = run_command(
        pattern="print($A)",
        lang="python",
        stdin=True,
        json_mode=True,
    )

    assert exit_code == 1
    config = seen["config"]
    assert seen["file_paths"] == []
    assert config.ast_stdin is True
    assert config.ast_stdin_input == "print('hello')\n"
    assert config.lang == "python"


def test_run_command_should_reject_stdin_files_with_matches(capsys):
    from tensor_grep.cli.ast_workflows import run_command

    exit_code = run_command(
        pattern="print($A)",
        lang="python",
        stdin=True,
        files_with_matches=True,
    )

    assert exit_code == 1
    assert "--stdin cannot be combined with --files-with-matches" in capsys.readouterr().err


def test_run_command_should_reject_files_with_matches_json(capsys):
    from tensor_grep.cli.ast_workflows import run_command

    exit_code = run_command(
        pattern="print($A)",
        lang="python",
        files_with_matches=True,
        json_mode=True,
    )

    assert exit_code == 1
    assert "--files-with-matches is a read-only text output mode" in capsys.readouterr().err


def test_run_command_should_reject_ast_grep_semantic_options_for_rewrites(capsys):
    from tensor_grep.cli.ast_workflows import run_command

    exit_code = run_command(
        pattern="print($A)",
        rewrite="logger.info($A)",
        selector="call",
    )

    assert exit_code == 1
    assert "ast-grep semantic run options are read-only" in capsys.readouterr().err


def test_ast_workflow_entry_accepts_ast_grep_pattern_option(monkeypatch):
    """Verify the lightweight workflow parser accepts `run --pattern`, like ast-grep."""
    from tensor_grep.cli import ast_workflows

    seen: dict[str, object] = {}

    def fake_run_command(pattern: str, path: str | None = None, **kwargs: object) -> int:
        seen["pattern"] = pattern
        seen["path"] = path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(ast_workflows, "run_command", fake_run_command)

    with pytest.raises(SystemExit) as raised:
        ast_workflows.main_entry(
            [
                "run",
                "--pattern",
                "class $NAME: $$$BODY",
                "--files-with-matches",
                "src",
                "--lang",
                "python",
            ]
        )

    assert raised.value.code == 0
    assert seen["pattern"] == "class $NAME: $$$BODY"
    assert seen["path"] == "src"
    assert seen["kwargs"]["files_with_matches"] is True


def test_ast_workflow_entry_accepts_ast_grep_semantic_run_options(monkeypatch):
    """Verify the lightweight workflow parser accepts supported ast-grep run options."""
    from tensor_grep.cli import ast_workflows

    seen: dict[str, object] = {}

    def fake_run_command(pattern: str, path: str | None = None, **kwargs: object) -> int:
        seen["pattern"] = pattern
        seen["path"] = path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(ast_workflows, "run_command", fake_run_command)

    with pytest.raises(SystemExit) as raised:
        ast_workflows.main_entry(
            [
                "run",
                "--pattern",
                "print($A)",
                "--selector",
                "call",
                "--strictness",
                "relaxed",
                "--globs",
                "*.py",
                "--stdin",
                "--lang",
                "python",
            ]
        )

    assert raised.value.code == 0
    assert seen["pattern"] == "print($A)"
    assert seen["path"] is None
    assert seen["kwargs"]["selector"] == "call"
    assert seen["kwargs"]["strictness"] == "relaxed"
    assert seen["kwargs"]["globs"] == ["*.py"]
    assert seen["kwargs"]["stdin"] is True


def test_typer_run_rejects_existing_path_with_semantic_options_without_pattern(tmp_path):
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    project = tmp_path / "src"
    project.mkdir()

    result = CliRunner().invoke(app, ["run", "--selector", "call", str(project), "--json"])

    assert result.exit_code == 2
    assert "require --pattern <PATTERN> before PATH" in result.output
    assert "Traceback" not in result.output
