from unittest.mock import MagicMock, mock_open, patch

from tensor_grep.core.hardware.device_detect import (
    DeviceDetector,
    DeviceInfo,
    Platform,
    _running_under_wsl,
)


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
    def test_should_cache_vram_capacity_per_device(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.return_value = True

        props_0 = MagicMock(total_memory=8 * 1024 * 1024 * 1024)  # 8GB
        props_1 = MagicMock(total_memory=12 * 1024 * 1024 * 1024)  # 12GB
        torch.cuda.get_device_properties.side_effect = [props_0, props_1]

        detector = DeviceDetector()
        assert detector.get_vram_capacity_mb(0) == 8192
        assert detector.get_vram_capacity_mb(0) == 8192
        assert detector.get_vram_capacity_mb(1) == 12288
        assert torch.cuda.get_device_properties.call_count == 2

    @patch("tensor_grep.core.hardware.device_detect.sys")
    @patch.dict("sys.modules", {"torch": MagicMock(), "ctypes": MagicMock()})
    @patch("os.path.exists", return_value=False)
    def test_should_clear_all_detection_caches(self, mock_exists, mock_sys):
        import torch

        mock_sys.platform = "linux"
        torch.cuda.is_available.side_effect = [True, True]
        torch.cuda.device_count.side_effect = [2, 0]
        props_0 = MagicMock(total_memory=8 * 1024 * 1024 * 1024)  # 8GB
        props_1 = MagicMock(total_memory=4 * 1024 * 1024 * 1024)  # 4GB
        torch.cuda.get_device_properties.side_effect = [props_0, props_1]

        detector = DeviceDetector()
        assert detector.has_gpu() is True
        assert detector.get_device_count() == 2
        assert detector.get_vram_capacity_mb(0) == 8192

        detector.clear_cache()

        # Recompute after cache clear should read fresh values from torch.
        assert detector.has_gpu() is True
        assert detector.get_device_count() == 0
        assert detector.get_vram_capacity_mb(0) == 4096
        assert torch.cuda.is_available.call_count == 2

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
    @patch("tensor_grep.core.hardware.device_detect._running_under_wsl")
    def test_should_detect_platform(self, mock_wsl, mock_sys):
        # Test Linux (not WSL)
        mock_sys.platform = "linux"
        mock_wsl.return_value = False
        detector = DeviceDetector()
        assert detector.get_platform() == Platform.LINUX

        # Test WSL2
        mock_sys.platform = "linux"
        mock_wsl.return_value = True
        detector = DeviceDetector()
        assert detector.get_platform() == Platform.WSL2

        # Test Windows
        mock_sys.platform = "win32"
        detector = DeviceDetector()
        assert detector.get_platform() == Platform.WINDOWS

    @patch("tensor_grep.core.hardware.device_detect.sys")
    def test_get_platform_wsl2_via_proc_version_when_env_stripped(self, mock_sys):
        # Regression: a stripped-environment WSL2 host (WSL_DISTRO_NAME/WSL_INTEROP dropped and
        # no /run/WSL) was mis-reported as Platform.LINUX because get_platform only checked
        # /run/WSL. It now consults /proc/version, matching cli.runtime_paths.is_wsl_host (#615).
        mock_sys.platform = "linux"
        proc_version = "Linux version 6.6.87.2-microsoft-standard-WSL2 (root@build) #1 SMP\n"
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("os.path.exists", return_value=False),
            patch("builtins.open", mock_open(read_data=proc_version)),
        ):
            assert DeviceDetector().get_platform() == Platform.WSL2

    def test_running_under_wsl_detects_env_signal(self):
        with (
            patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"}, clear=True),
            patch("os.path.exists", return_value=False),
        ):
            assert _running_under_wsl() is True

    def test_running_under_wsl_detects_run_wsl_marker(self):
        # env stripped of WSL vars, but /run/WSL present (the fallback signal)
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("os.path.exists", return_value=True),
        ):
            assert _running_under_wsl() is True

    def test_running_under_wsl_detects_proc_version_microsoft(self):
        # env stripped AND no /run/WSL: fall back to the /proc/version kernel stamp
        proc_version = "Linux version 6.6.87.2-microsoft-standard-WSL2 (root@build) #1 SMP\n"
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("os.path.exists", return_value=False),
            patch("builtins.open", mock_open(read_data=proc_version)),
        ):
            assert _running_under_wsl() is True

    def test_running_under_wsl_false_on_plain_linux(self):
        proc_version = "Linux version 6.8.0-45-generic (buildd@lcy02) #45-Ubuntu SMP\n"
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("os.path.exists", return_value=False),
            patch("builtins.open", mock_open(read_data=proc_version)),
        ):
            assert _running_under_wsl() is False

    def test_running_under_wsl_fails_closed_when_proc_version_unreadable(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("os.path.exists", return_value=False),
            patch("builtins.open", side_effect=OSError("no /proc/version")),
        ):
            assert _running_under_wsl() is False

    def test_running_under_wsl_matches_is_wsl_host(self):
        # `_running_under_wsl` is a deliberate copy of `cli.runtime_paths.is_wsl_host`
        # (core.hardware must not import the cli layer). Pin the two equal across every
        # signal so a future hardening of one (e.g. #615 added /proc/version) cannot
        # silently drift from the other.
        from contextlib import ExitStack

        from tensor_grep.cli.runtime_paths import is_wsl_host

        cases = [
            ("env_signal", {"WSL_INTEROP": "/run/WSL/x_interop"}, False, None),
            ("run_wsl_marker", {}, True, None),
            ("proc_microsoft", {}, False, "Linux version 6.6.87.2-microsoft-standard-WSL2\n"),
            ("plain_linux", {}, False, "Linux version 6.8.0-45-generic #45-Ubuntu\n"),
            ("proc_unreadable", {}, False, None),
        ]
        for name, env, run_wsl_exists, proc_version in cases:
            with ExitStack() as stack:
                stack.enter_context(patch.dict("os.environ", env, clear=True))
                stack.enter_context(patch("os.path.exists", return_value=run_wsl_exists))
                if proc_version is None:
                    stack.enter_context(patch("builtins.open", side_effect=OSError))
                else:
                    stack.enter_context(patch("builtins.open", mock_open(read_data=proc_version)))
                assert _running_under_wsl() == is_wsl_host(), f"WSL-detection drift in case {name!r}"
