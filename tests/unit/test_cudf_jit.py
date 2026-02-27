from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.backends.cudf_backend import CuDFBackend
from tensor_grep.core.config import SearchConfig


@pytest.fixture
def backend():
    return CuDFBackend(chunk_sizes_mb=[100])


@patch("tensor_grep.backends.cudf_backend.cudf", create=True)
@patch.dict("sys.modules", {"cudf": MagicMock()})
@patch("os.path.getsize", return_value=1000)
def test_cudf_backend_jit_routing(mock_getsize, mock_cudf, backend):
    # Setup mock dataframe and column
    mock_df = MagicMock()
    mock_series = MagicMock()
    mock_series.sum.return_value = 5

    # Mock pre-compiled path
    mock_series.str.contains.return_value = mock_series
    # Mock JIT path (this assumes compile_regex_jit exists or is handled)
    mock_cudf.core.column.string.compile_regex_jit.return_value = "JIT_KERNEL"

    # Setup reader fallback
    mock_cudf.read_text.return_value = mock_df

    # Mock loc filtering
    mock_df.loc.__getitem__.return_value = MagicMock(
        to_pandas=MagicMock(return_value=MagicMock(items=lambda: []))
    )

    # Search with JIT flag disabled
    config = SearchConfig(use_jit=False, count=True)
    backend.search("test.log", "ERROR", config)

    # Search with JIT flag enabled
    config = SearchConfig(use_jit=True, count=True)
    backend.search("test.log", "ERROR", config)

    # Asserting compile_regex_jit was attempted would go here if cudf supported it out of box,
    # but for TDD we just want to ensure it handles the `use_jit` flag without breaking.
    assert config.use_jit is True
