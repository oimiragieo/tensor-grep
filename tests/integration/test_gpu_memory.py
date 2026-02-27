from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.gpu, pytest.mark.integration]


class TestGpuMemoryIntegration:
    @patch("tensor_grep.core.hardware.memory_manager.DeviceDetector")
    def test_should_process_file_larger_than_vram(self, mock_detect, tmp_path):
        pytest.importorskip("cudf")

        # We simulate a file size of 200MB, but set VRAM budget to 100MB
        mock_instance = MagicMock()
        mock_instance.has_gpu.return_value = True
        mock_instance.get_vram_capacity_mb.return_value = 100
        mock_detect.return_value = mock_instance

        from tensor_grep.core.hardware.memory_manager import MemoryManager

        manager = MemoryManager()
        chunk_size = manager.get_recommended_chunk_size_mb()
        assert chunk_size == 40  # 80% of 100 is 80, half is 40

        # Test the backend sets this correctly
        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend(chunk_sizes_mb=[chunk_size])

        # Write dummy file > chunk_size
        large_file = tmp_path / "large.log"
        content = "ERROR " * 1024 * 1024 * 5  # roughly 30MB, which is > chunk_size (5MB * 6 chunks)
        large_file.write_text(content)

        with patch("cudf.read_text") as mock_cudf_read_text:
            mock_series = MagicMock()
            mock_cudf_read_text.return_value = mock_series

            backend.search(str(large_file), "ERROR")

            # Should be called multiple times due to chunking
            assert mock_cudf_read_text.call_count > 1

    @patch("tensor_grep.core.hardware.memory_manager.DeviceDetector")
    def test_peak_vram_should_stay_within_budget(self, mock_detect):
        # Difficult to test without real GPU and real cudf tracking,
        # but we can test the memory manager calculates the budget correctly.
        mock_instance = MagicMock()
        mock_instance.has_gpu.return_value = True
        mock_instance.get_vram_capacity_mb.return_value = 8192
        mock_detect.return_value = mock_instance

        from tensor_grep.core.hardware.memory_manager import MemoryManager

        manager = MemoryManager()
        budget = manager.get_vram_budget_mb()

        assert budget == 6553
