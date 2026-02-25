from unittest.mock import patch, MagicMock
from cudf_grep.gpu.device_detect import DeviceDetector, Platform

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
        torch.cuda.get_device_properties.return_value = MagicMock(total_memory=12884901888)
        detector = DeviceDetector()
        assert detector.get_vram_capacity_mb() == 12288

    @patch.dict("sys.modules", {"torch": MagicMock(), "kvikio": MagicMock()})
    def test_should_detect_gds_support(self):
        import torch
        import kvikio
        torch.cuda.is_available.return_value = True
        kvikio.DriverProperties.return_value = MagicMock(is_gds_available=True)
        detector = DeviceDetector()
        assert detector.has_gds() is True

    @patch("cudf_grep.gpu.device_detect.sys")
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
