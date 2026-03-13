import os
from pathlib import Path

import pytest

from tensor_grep.core.config import SearchConfig

_NON_SIMPLE_AST_PATTERN = "(function_definition name: (identifier) @name)"


class _FakeCapturedNode:
    def __init__(self, line_number: int = 0):
        self.start_point = (line_number, 0)


class _FakeCacheQuery:
    def captures(self, _root):
        return [(_FakeCapturedNode(), "match")]


class _FakeCacheLanguage:
    def __init__(self):
        self.query_calls = 0

    def query(self, _pattern):
        self.query_calls += 1
        return _FakeCacheQuery()


class _FakeCacheTree:
    class RootNode:
        type = "module"
        start_point = (0, 0)
        children = ()

    root_node = RootNode()


class _FakeCacheParser:
    def __init__(self):
        self.language = _FakeCacheLanguage()
        self.parse_calls = 0

    def parse(self, _source):
        self.parse_calls += 1
        return _FakeCacheTree()


class TestAstBackend:
    def teardown_method(self):
        from tensor_grep.backends.ast_backend import AstBackend

        AstBackend._clear_shared_caches()

    def test_should_report_unavailable_when_tree_sitter_missing(self, mocker):
        # Arrange
        mocker.patch.dict("sys.modules", {"tree_sitter": None})
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()

        # Act & Assert
        assert backend.is_available() is False

    @pytest.mark.gpu
    def test_should_parse_python_ast(self, tmp_path):
        # Note: This test requires tree-sitter, tree-sitter-python, and torch_geometric to be installed.
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()

        if not backend.is_available():
            pytest.skip("AstBackend dependencies not installed")

        # Arrange
        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    print('world')\n")
        config = SearchConfig(ast=True, lang="python")

        # Act
        # In this simplistic simulated graph match, we're passing a pattern that doesn't actually match the GNN directly,
        # but verifies the graph extraction pipeline doesn't crash.
        result = backend.search(str(file_path), "def", config)

        # Assert
        assert result is not None
        assert isinstance(result.total_matches, int)

    def test_should_raise_on_invalid_ast_query(self, tmp_path, mocker):
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeLanguage:
            def query(self, _pattern):
                raise Exception("invalid query")

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            language = FakeLanguage()

            def parse(self, _source):
                return FakeTree()

        mocker.patch.object(backend, "_get_parser", return_value=FakeParser())

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    print('world')\n")

        with pytest.raises(ValueError, match="Invalid AST query pattern"):
            backend.search(
                str(file_path), "not a valid ast query", SearchConfig(ast=True, lang="python")
            )

    def test_should_support_dict_capture_shape(self, tmp_path, mocker):
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            pass

        class FakeLanguage:
            def query(self, _pattern):
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            language = FakeLanguage()

            def parse(self, _source):
                return FakeTree()

        class FakeCursor:
            def __init__(self, _query):
                pass

            def captures(self, _root):
                return {"match": [FakeNode()]}

        class FakeTreeSitterModule:
            QueryCursor = FakeCursor

        mocker.patch.object(backend, "_get_parser", return_value=FakeParser())
        mocker.patch.dict("sys.modules", {"tree_sitter": FakeTreeSitterModule})

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n")

        result = backend.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )
        assert result.total_matches == 1
        assert result.routing_backend == "AstBackend"
        assert result.routing_reason == "ast_structural_match"

    def test_should_reuse_compiled_query_and_parsed_source_for_repeated_searches(
        self, tmp_path, mocker
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        parser = FakeParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        first = backend.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )
        second = backend.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert second.total_matches == 1
        assert parser.parse_calls == 1
        assert parser.language.query_calls == 1

    def test_should_invalidate_parsed_source_cache_when_file_changes(self, tmp_path, mocker):
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        parser = FakeParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        backend.search(str(file_path), "function_definition", SearchConfig(ast=True, lang="python"))

        # Change both timestamp and file size so cache invalidation is deterministic on Windows.
        file_path.write_text("def hello_world():\n    return 1\n", encoding="utf-8")

        result = backend.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert result.total_matches == 1
        assert parser.parse_calls == 2
        assert parser.language.query_calls == 1

    def test_should_reuse_persistent_ast_cache_across_backend_instances(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)

        first = backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert parser_one.parse_calls == 1
        assert parser_one.language.query_calls == 1

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)

        second = backend_two.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert second.total_matches == 1
        assert parser_two.parse_calls == 0
        assert parser_two.language.query_calls == 0

    def test_should_invalidate_persistent_ast_cache_when_file_changes(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)
        backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        file_path.write_text("def hello_world():\n    return 1\n", encoding="utf-8")

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)
        result = backend_two.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert result.total_matches == 1
        assert parser_two.parse_calls == 1
        assert parser_two.language.query_calls == 0

    def test_should_reuse_persistent_node_type_index_for_simple_native_patterns(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))

        class FakeNode:
            def __init__(self, node_type, line_number, children=()):
                self.type = node_type
                self.start_point = (line_number, 0)
                self.children = children

        class FakeQuery:
            def captures(self, _root):
                return []

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            def __init__(self):
                self.root_node = FakeNode(
                    "module",
                    0,
                    children=(
                        FakeNode("function_definition", 0),
                        FakeNode("class_definition", 2),
                    ),
                )

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\nclass World:\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)

        first = backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert parser_one.parse_calls == 1

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)

        second = backend_two.search(
            str(file_path), "class_definition", SearchConfig(ast=True, lang="python")
        )

        assert second.total_matches == 1
        assert second.matches[0].line_number == 3
        assert parser_two.parse_calls == 0
        assert parser_two.language.query_calls == 0

    def test_should_reuse_shared_in_memory_caches_across_backend_instances(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        AstBackend._clear_shared_caches()

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)

        first = backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert parser_one.parse_calls == 1
        assert parser_one.language.query_calls == 1

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)

        second = backend_two.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert second.total_matches == 1
        assert parser_two.parse_calls == 0
        assert parser_two.language.query_calls == 0

    def test_should_apply_calibrated_multiplier_when_estimating_parsed_source_cache_entry_size(self):
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        source_bytes = b"def alpha():\n    return 1111111111\n"

        estimated = backend._estimate_parsed_source_cache_entry_size(source_bytes)

        assert estimated == len(source_bytes) * 3

    def test_should_evict_least_recently_used_parsed_source_entries_when_byte_budget_is_exceeded(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        monkeypatch.setenv("TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES", "250")
        AstBackend._clear_shared_caches()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)
        parser = _FakeCacheParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        file_c = tmp_path / "c.py"
        file_a.write_text("def alpha():\n    return 1111111111\n", encoding="utf-8")
        file_b.write_text("def bravo():\n    return 2222222222\n", encoding="utf-8")
        file_c.write_text("def charl():\n    return 3333333333\n", encoding="utf-8")

        config = SearchConfig(ast=True, lang="python")
        backend.search(str(file_a), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_b), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_a), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_c), _NON_SIMPLE_AST_PATTERN, config)

        assert parser.parse_calls == 3
        assert list(backend._parsed_source_cache) == [
            (str(file_a), "python"),
            (str(file_c), "python"),
        ]
        assert AstBackend._shared_parsed_source_cache_bytes <= 250

        backend.search(str(file_a), _NON_SIMPLE_AST_PATTERN, config)
        assert parser.parse_calls == 3

        backend.search(str(file_b), _NON_SIMPLE_AST_PATTERN, config)
        assert parser.parse_calls == 4

    def test_should_skip_caching_parsed_source_entries_that_exceed_the_byte_budget(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        monkeypatch.setenv("TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES", "10")
        AstBackend._clear_shared_caches()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)
        parser = _FakeCacheParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "oversized.py"
        file_path.write_text("def alpha():\n    return 1111111111\n", encoding="utf-8")

        config = SearchConfig(ast=True, lang="python")
        backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)

        assert parser.parse_calls == 2
        assert backend._parsed_source_cache == {}
        assert AstBackend._shared_parsed_source_cache_bytes == 0

    def test_should_skip_caching_parsed_source_entries_when_calibrated_estimate_exceeds_budget(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        monkeypatch.setenv("TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES", "100")
        AstBackend._clear_shared_caches()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)
        parser = _FakeCacheParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "midrange.py"
        file_path.write_text("def alpha():\n    return 1111111111\n", encoding="utf-8")

        config = SearchConfig(ast=True, lang="python")
        backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)

        assert parser.parse_calls == 2
        assert backend._parsed_source_cache == {}
        assert AstBackend._shared_parsed_source_cache_bytes == 0

    def test_should_invalidate_parsed_source_cache_when_file_identity_changes_without_mtime_or_size_change(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        AstBackend._clear_shared_caches()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)
        parser = _FakeCacheParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "identity.py"
        file_path.write_text("def alpha():\n    pass\n", encoding="utf-8")
        original_stat = file_path.stat()

        config = SearchConfig(ast=True, lang="python")
        first = backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)

        file_path.unlink()
        file_path.write_text("def bravo():\n    pass\n", encoding="utf-8")
        os.utime(file_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        second = backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)

        assert first.matches[0].text == "def alpha():"
        assert second.matches[0].text == "def bravo():"
        assert parser.parse_calls == 2

    def test_should_reuse_shared_in_memory_node_index_without_disk_reload(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))
        AstBackend._clear_shared_caches()

        class FakeNode:
            def __init__(self, node_type, line_number, children=()):
                self.type = node_type
                self.start_point = (line_number, 0)
                self.children = children

        class FakeQuery:
            def captures(self, _root):
                return []

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            def __init__(self):
                self.root_node = FakeNode(
                    "module",
                    0,
                    children=(
                        FakeNode("function_definition", 0),
                        FakeNode("class_definition", 2),
                    ),
                )

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\nclass World:\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)

        first = backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert parser_one.parse_calls == 1

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)
        read_text_spy = mocker.spy(Path, "read_text")

        second = backend_two.search(
            str(file_path), "class_definition", SearchConfig(ast=True, lang="python")
        )

        assert second.total_matches == 1
        assert second.matches[0].line_number == 3
        assert parser_two.parse_calls == 0
        assert parser_two.language.query_calls == 0
        assert read_text_spy.call_count == 0
