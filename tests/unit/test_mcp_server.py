from unittest.mock import MagicMock, patch


def test_tg_ast_search_accepts_ast_wrapper_backend():
    from tensor_grep.cli import mcp_server

    fake_backend = type("AstGrepWrapperBackend", (), {"search": MagicMock()})()

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        mock_pipeline.return_value.get_backend.return_value = fake_backend
        mock_scanner.return_value.walk.return_value = []

        out = mcp_server.tg_ast_search("def $A():", "python", ".")

    assert out == "No AST matches found for pattern in .."
