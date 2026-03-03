from unittest.mock import patch

from tensor_grep.core.hardware.device_detect import DeviceDetector


class TestDeviceDetector:
    def test_should_parse_explicit_device_ids_and_drop_invalid_tokens(self):
        with patch.dict(
            "os.environ",
            {"TENSOR_GREP_DEVICE_IDS": " 7, 3, bad, -1, 7, , 2 "},
            clear=False,
        ):
            parsed = DeviceDetector._parse_explicit_device_ids()

        assert parsed == [7, 3, 2]

    def test_should_return_none_for_unset_explicit_device_ids(self):
        with patch.dict("os.environ", {}, clear=True):
            parsed = DeviceDetector._parse_explicit_device_ids()

        assert parsed is None

    @patch.object(DeviceDetector, "get_device_count", return_value=4)
    def test_should_enumerate_all_detected_device_ids_when_no_explicit_override(self, _mock_count):
        with patch.dict("os.environ", {}, clear=True):
            detector = DeviceDetector()
            device_ids = detector.get_device_ids()

        assert device_ids == [0, 1, 2, 3]
        assert detector.enumerate_device_ids() == [0, 1, 2, 3]

    @patch.object(DeviceDetector, "get_device_count", return_value=4)
    def test_should_filter_explicit_device_ids_against_detected_count(self, _mock_count):
        with patch.dict(
            "os.environ",
            {"TENSOR_GREP_DEVICE_IDS": "3, 9, 1"},
            clear=False,
        ):
            detector = DeviceDetector()
            device_ids = detector.get_device_ids()

        assert device_ids == [3, 1]

    @patch.object(DeviceDetector, "get_device_count", return_value=2)
    def test_should_fallback_to_detected_ids_when_all_explicit_ids_invalid(self, _mock_count):
        with patch.dict(
            "os.environ",
            {"TENSOR_GREP_DEVICE_IDS": "7, 9"},
            clear=False,
        ):
            detector = DeviceDetector()
            device_ids = detector.get_device_ids()

        assert device_ids == [0, 1]

    @patch.object(DeviceDetector, "get_vram_capacity_mb")
    @patch.object(DeviceDetector, "get_device_count", return_value=2)
    def test_should_list_devices_with_capacity_for_each_routable_id(self, _mock_count, mock_vram):
        mock_vram.side_effect = lambda device_id: 12000 if device_id == 0 else 24000
        with patch.dict("os.environ", {}, clear=True):
            detector = DeviceDetector()
            devices = detector.list_devices()

        assert [d.device_id for d in devices] == [0, 1]
        assert [d.vram_capacity_mb for d in devices] == [12000, 24000]
