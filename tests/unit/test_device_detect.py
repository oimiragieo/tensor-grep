from unittest.mock import MagicMock, patch

from tensor_grep.core.hardware.device_detect import DeviceDetector, Platform


class TestDeviceDetect:
    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch.dict("os.environ", {"TENSOR_GREP_DEVICE_IDS": "3,7"})
    @patch("os.path.exists", return_value=False)
    def test_should_respect_explicit_device_ids_override(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = 8

        detector = DeviceDetector()
        assert detector.get_device_count() == 8
        assert detector.get_device_ids() == [3, 7]

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch.dict("os.environ", {"TENSOR_GREP_DEVICE_IDS": "7,foo,7,2,-1"})
    @patch("os.path.exists", return_value=False)
    def test_should_filter_invalid_explicit_device_ids(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = 8

        detector = DeviceDetector()
        assert detector.get_device_ids() == [7, 2]

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch.dict("os.environ", {"TENSOR_GREP_DEVICE_IDS": "9,10"})
    @patch("os.path.exists", return_value=False)
    def test_should_fallback_to_contiguous_ids_when_override_out_of_range(
        self, mock_exists, mock_sys
    ):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = 2

        detector = DeviceDetector()
        assert detector.get_device_ids() == [0, 1]

    @patch.dict("sys.modules", {"torch": MagicMock()})
    @patch("os.path.exists", return_value=False)
    def test_should_detect_no_gpu_when_cuda_unavailable(self, mock_exists):
        import torch

        torch.cuda.is_available.return_value = False
        detector = DeviceDetector()
        assert detector.has_gpu() is False
        assert detector.get_vram_capacity_mb() == 0

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch("os.path.exists", return_value=False)
    def test_should_report_vram_capacity(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = 1
        torch.cuda.get_device_properties.return_value = MagicMock(total_memory=12884901888)
        detector = DeviceDetector()
        assert detector.get_vram_capacity_mb() == 12288

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch("os.path.exists", return_value=False)
    def test_should_detect_multiple_gpus_when_available(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"

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
        assert detector.get_device_ids() == [0, 1]
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
