from unittest.mock import MagicMock, patch

from tensor_grep.core.hardware.device_detect import DeviceDetector, DeviceInfo, Platform


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

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch("os.path.exists", return_value=False)
    def test_should_cache_has_gpu_result(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.side_effect = [True, False]

        detector = DeviceDetector()
        assert detector.has_gpu() is True
        assert detector.has_gpu() is True
        assert torch.cuda.is_available.call_count == 1

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch("os.path.exists", return_value=False)
    def test_should_cache_device_count_result(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.side_effect = [2, 0]

        detector = DeviceDetector()
        assert detector.get_device_count() == 2
        assert detector.get_device_count() == 2
        assert torch.cuda.device_count.call_count == 1

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch("os.path.exists", return_value=False)
    def test_should_expose_public_device_enumeration_contract(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = 2

        detector = DeviceDetector()
        with (
            patch.object(detector, "get_device_ids", return_value=[3, 7]),
            patch.object(detector, "get_vram_capacity_mb", side_effect=[24576, 24576]),
        ):
            devices = detector.list_devices()

        assert devices == [
            DeviceInfo(device_id=3, vram_capacity_mb=24576),
            DeviceInfo(device_id=7, vram_capacity_mb=24576),
        ]

    @patch.dict("sys.modules", {"torch": MagicMock()})
    @patch("os.path.exists", return_value=False)
    def test_should_expose_empty_device_enumeration_when_no_gpu(self, mock_exists):
        import torch

        torch.cuda.is_available.return_value = False
        detector = DeviceDetector()
        assert detector.list_devices() == []

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch("os.path.exists", return_value=False)
    def test_should_expose_public_device_id_enumeration_contract(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = 8

        detector = DeviceDetector()
        with patch.object(detector, "get_device_ids", return_value=[7, 3]):
            assert detector.enumerate_device_ids() == [7, 3]

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
