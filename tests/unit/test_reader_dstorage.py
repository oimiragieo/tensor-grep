from unittest.mock import MagicMock, patch


class TestDStorageReader:
    @patch.dict("sys.modules", {"dstorage_gpu": MagicMock()})
    @patch("tensor_grep.io.reader_dstorage.sys")
    def test_should_report_dstorage_available_on_windows(self, mock_sys):
        mock_sys.platform = "win32"
        from tensor_grep.io.reader_dstorage import DStorageReader

        reader = DStorageReader()
        assert reader.is_available() is True

    @patch("tensor_grep.io.reader_dstorage.sys")
    def test_should_fallback_when_dstorage_unavailable(self, mock_sys):
        mock_sys.platform = "win32"
        # We don't mock dstorage_gpu here, so it raises ImportError
        from tensor_grep.io.reader_dstorage import DStorageReader

        reader = DStorageReader()
        assert reader.is_available() is False

    @patch.dict("sys.modules", {"dstorage_gpu": MagicMock()})
    @patch("tensor_grep.io.reader_dstorage.sys")
    def test_should_load_tensor_via_directstorage(self, mock_sys):
        mock_sys.platform = "win32"
        import dstorage_gpu

        mock_loader = MagicMock()
        mock_loader.load_tensor.return_value = "mock_tensor"
        dstorage_gpu.DirectStorageLoader.return_value = mock_loader

        from tensor_grep.io.reader_dstorage import DStorageReader

        reader = DStorageReader()
        tensor = reader.read_to_gpu("test.log")

        assert tensor == "mock_tensor"
        mock_loader.load_tensor.assert_called_once_with("test.log")
