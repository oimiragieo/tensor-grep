from unittest.mock import MagicMock, patch

from tensor_grep.gpu.memory_manager import MemoryManager


class TestMemoryManager:
    @patch("tensor_grep.gpu.memory_manager.DeviceDetector")
    def test_should_calculate_chunk_size_from_vram_budget(self, mock_detect):
        mock_instance = MagicMock()
        mock_instance.has_gpu.return_value = True
        mock_instance.get_vram_capacity_mb.return_value = 8192
        mock_detect.return_value = mock_instance

        manager = MemoryManager()
        # 8192 * 0.8 / 2 roughly
        assert manager.get_recommended_chunk_size_mb(0) == 3276

    @patch("tensor_grep.gpu.memory_manager.DeviceDetector")
    def test_should_reserve_20_percent_vram_headroom(self, mock_detect):
        mock_instance = MagicMock()
        mock_instance.has_gpu.return_value = True
        mock_instance.get_vram_capacity_mb.return_value = 10000
        mock_detect.return_value = mock_instance

        manager = MemoryManager()
        assert manager.get_vram_budget_mb(0) == 8000

    @patch("tensor_grep.gpu.memory_manager.DeviceDetector")
    def test_should_recommend_pinned_memory_for_geforce(self, mock_detect):
        mock_instance = MagicMock()
        mock_instance.has_gds.return_value = False
        mock_detect.return_value = mock_instance

        manager = MemoryManager()
        assert manager.should_use_pinned_memory() is True

    @patch("tensor_grep.gpu.memory_manager.DeviceDetector")
    def test_should_recommend_gds_for_datacenter_gpu(self, mock_detect):
        mock_instance = MagicMock()
        mock_instance.has_gds.return_value = True
        mock_detect.return_value = mock_instance

        manager = MemoryManager()
        assert manager.should_use_pinned_memory() is False

    @patch("tensor_grep.gpu.memory_manager.DeviceDetector")
    def test_should_handle_zero_vram_gracefully(self, mock_detect):
        mock_instance = MagicMock()
        mock_instance.has_gpu.return_value = False
        mock_detect.return_value = mock_instance

        manager = MemoryManager()
        assert manager.get_recommended_chunk_size_mb(0) == 0
        assert manager.get_vram_budget_mb(0) == 0

    @patch("tensor_grep.gpu.memory_manager.DeviceDetector")
    def test_should_shardDataAcrossGPUs_when_multiGpuDetected(self, mock_detect):
        mock_instance = MagicMock()
        mock_instance.has_gpu.return_value = True
        mock_instance.get_device_count.return_value = 2

        def mock_vram(device_id):
            if device_id == 0:
                return 10000  # 10 GB
            return 20000  # 20 GB

        mock_instance.get_vram_capacity_mb.side_effect = mock_vram
        mock_detect.return_value = mock_instance

        manager = MemoryManager()

        # Test VRAM Budget
        assert manager.get_vram_budget_mb(0) == 8000
        assert manager.get_vram_budget_mb(1) == 16000

        # Test Chunk Size
        assert manager.get_recommended_chunk_size_mb(0) == 4000
        assert manager.get_recommended_chunk_size_mb(1) == 8000

        # Test getting all device chunks
        chunks = manager.get_all_device_chunk_sizes_mb()
        assert len(chunks) == 2
        assert chunks[0] == 4000
        assert chunks[1] == 8000
