from unittest.mock import MagicMock, patch

from tensor_grep.core.hardware.device_detect import DeviceDetector, Platform


class TestDeviceDetect:
    @patch.dict("sys.modules", {"torch": MagicMock()})
    def test_should_detect_no_gpu_when_cuda_unavailable(self):
        import torch

        torch.cuda.is_available.return_value = False
        detector = DeviceDetector()
        assert detector.has_gpu() is False
        assert detector.get_vram_capacity_mb() == 0

    @patch.dict("sys.modules", {"torch": MagicMock()})
    def test_should_report_vram_capacity(self):
        import torch

        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = 1
        torch.cuda.get_device_properties.return_value = MagicMock(total_memory=12884901888)
        detector = DeviceDetector()
        assert detector.get_vram_capacity_mb() == 12288

    @patch.dict("sys.modules", {"torch": MagicMock()})
    def test_should_detect_multiple_gpus_when_available(self):
        import torch

        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = 2
        mock_props_0 = MagicMock(total_memory=12884901888)  # 12 GB
        mock_props_1 = MagicMock(total_memory=25769803776)  # 24 GB

        def mock_get_properties(device_id):
            if device_id == 0:
                return mock_props_0
            return mock_props_1

        torch.cuda.get_device_properties.side_effect = mock_get_properties

        detector = DeviceDetector()
        assert detector.get_device_count() == 2
        assert detector.get_vram_capacity_mb(0) == 12288
        assert detector.get_vram_capacity_mb(1) == 24576

    @patch.dict("sys.modules", {"torch": MagicMock(), "kvikio": MagicMock()})
    def test_should_detect_gds_support(self):
        import kvikio
        import torch

        torch.cuda.is_available.return_value = True
        kvikio.DriverProperties.return_value = MagicMock(is_gds_available=True)
        detector = DeviceDetector()
        assert detector.has_gds() is True

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch("os.path.exists")
    def test_should_detect_platform(self, mock_exists, mock_sys):
        # Test Linux
        mock_sys.platform = "linux"
        mock_exists.return_value = False
        detector = DeviceDetector()
        assert detector.get_platform() == Platform.LINUX

        # Test WSL2
        mock_sys.platform = "linux"
        mock_exists.return_value = True
        detector = DeviceDetector()
        assert detector.get_platform() == Platform.WSL2

        # Test Windows
        mock_sys.platform = "win32"
        detector = DeviceDetector()
        assert detector.get_platform() == Platform.WINDOWS
