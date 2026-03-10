import pytest

from tensor_grep.core.config import SearchConfig


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
