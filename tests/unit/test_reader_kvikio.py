from unittest.mock import MagicMock, patch


class TestKvikIOReader:
    @patch("importlib.util.find_spec")
    @patch.dict("sys.modules", {"kvikio": MagicMock()})
    def test_should_read_via_gds_when_available(self, mock_find_spec):
        mock_find_spec.return_value = True
        import kvikio

        mock_file = MagicMock()
        mock_file.read.return_value = b"data"
        kvikio.CuFile.return_value = mock_file

        from tensor_grep.io.reader_kvikio import KvikIOReader

        reader = KvikIOReader()
        assert reader.is_available() is True
        data = reader.read_to_gpu("test.log")
        assert data == b"data"

    def test_should_fallback_to_compat_mode(self):
        from tensor_grep.io.reader_kvikio import KvikIOReader

        reader = KvikIOReader()
        assert reader.is_available() is False
