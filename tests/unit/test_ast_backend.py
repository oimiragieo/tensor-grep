import pytest

from tensor_grep.core.config import SearchConfig


class TestAstBackend:
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
