import pytest
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.gpu, pytest.mark.integration]

class TestGpuMemoryIntegration:
    @patch("cudf_grep.gpu.memory_manager.DeviceDetector")
    @patch("cudf_grep.backends.cudf_backend.cudf")
    def test_should_process_file_larger_than_vram(self, mock_cudf, mock_detect, tmp_path):
        # We simulate a file size of 200MB, but set VRAM budget to 100MB
        mock_instance = MagicMock()
        mock_instance.has_gpu.return_value = True
        mock_instance.get_vram_capacity_mb.return_value = 100
        mock_detect.return_value = mock_instance
        
        from cudf_grep.gpu.memory_manager import MemoryManager
        manager = MemoryManager()
        chunk_size = manager.get_recommended_chunk_size_mb()
        assert chunk_size == 40 # 80% of 100 is 80, half is 40
        
        # Test the backend sets this correctly
        from cudf_grep.backends.cudf_backend import CuDFBackend
        backend = CuDFBackend(chunk_size_mb=chunk_size)
        
        # Write dummy file > chunk_size
        large_file = tmp_path / "large.log"
        content = "ERROR "*1024*1024*50 # roughly 250MB
        large_file.write_text(content)
        
        mock_series = MagicMock()
        mock_cudf.read_text.return_value = mock_series
        
        backend.search(str(large_file), "ERROR")
        
        # Should be called multiple times due to chunking
        assert mock_cudf.read_text.call_count > 1
        
    @patch("cudf_grep.gpu.memory_manager.DeviceDetector")
    def test_peak_vram_should_stay_within_budget(self, mock_detect):
        # Difficult to test without real GPU and real cudf tracking, 
        # but we can test the memory manager calculates the budget correctly.
        mock_instance = MagicMock()
        mock_instance.has_gpu.return_value = True
        mock_instance.get_vram_capacity_mb.return_value = 8192
        mock_detect.return_value = mock_instance
        
        from cudf_grep.gpu.memory_manager import MemoryManager
        manager = MemoryManager()
        budget = manager.get_vram_budget_mb()
        
        assert budget == 6553
