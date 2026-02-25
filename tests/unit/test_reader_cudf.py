from tensor_grep.io.reader_cudf import CuDFReader
from unittest.mock import MagicMock, patch

import sys
from unittest.mock import MagicMock, patch
from tensor_grep.io.reader_cudf import CuDFReader

class TestCuDFReader:
    @patch.dict("sys.modules", {"cudf": MagicMock()})
    def test_should_read_file_and_yield_lines(self):
        import cudf
        mock_series = MagicMock()
        mock_series.to_pandas.return_value = ["line1", "line2"]
        cudf.read_text.return_value = mock_series
        
        reader = CuDFReader()
        lines = list(reader.read_lines("test.log"))
        
        assert len(lines) == 2
        assert lines[0] == "line1\n"
        assert lines[1] == "line2\n"
        cudf.read_text.assert_called_once()
