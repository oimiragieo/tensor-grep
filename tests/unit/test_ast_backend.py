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
