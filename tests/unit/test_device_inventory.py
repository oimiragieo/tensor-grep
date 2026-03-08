from unittest.mock import MagicMock, patch

from tensor_grep.core.hardware.device_detect import DeviceInfo, Platform
from tensor_grep.core.hardware.device_inventory import DeviceInventory, collect_device_inventory


def test_device_inventory_to_dict_should_serialize_devices():
    inventory = DeviceInventory(
        platform="windows",
        has_gpu=True,
        device_count=2,
        routable_device_ids=[7, 3],
        devices=[
            DeviceInfo(device_id=7, vram_capacity_mb=12288),
            DeviceInfo(device_id=3, vram_capacity_mb=24576),
        ],
    )

    payload = inventory.to_dict()

    assert payload["platform"] == "windows"
    assert payload["has_gpu"] is True
    assert payload["device_count"] == 2
    assert payload["routable_device_ids"] == [7, 3]
    assert payload["devices"] == [
        {"device_id": 7, "vram_capacity_mb": 12288},
        {"device_id": 3, "vram_capacity_mb": 24576},
    ]


def test_collect_device_inventory_should_read_from_detector_contract():
    detector = MagicMock()
    detector.list_devices.return_value = [DeviceInfo(device_id=1, vram_capacity_mb=8192)]
    detector.enumerate_device_ids = MagicMock(return_value=[1])
    detector.get_platform.return_value = Platform.WINDOWS
    detector.has_gpu.return_value = True

    inventory = collect_device_inventory(detector)

    assert inventory.platform == "windows"
    assert inventory.has_gpu is True
    assert inventory.device_count == 1
    assert inventory.routable_device_ids == [1]
    assert inventory.devices == [DeviceInfo(device_id=1, vram_capacity_mb=8192)]


def test_collect_device_inventory_should_construct_default_detector_when_missing():
    fake_detector = MagicMock()
    fake_detector.list_devices.return_value = []
    fake_detector.enumerate_device_ids = MagicMock(return_value=[])
    fake_detector.get_platform.return_value = Platform.LINUX
    fake_detector.has_gpu.return_value = False

    with patch(
        "tensor_grep.core.hardware.device_inventory.DeviceDetector",
        return_value=fake_detector,
    ) as detector_cls:
        inventory = collect_device_inventory()

    detector_cls.assert_called_once_with()
    assert inventory.platform == "linux"
    assert inventory.has_gpu is False
    assert inventory.device_count == 0
    assert inventory.routable_device_ids == []
    assert inventory.devices == []


def test_collect_device_inventory_should_preserve_explicit_device_id_order_for_routing():
    detector = MagicMock()
    detector.enumerate_device_ids = MagicMock(return_value=[7, 3])
    detector.list_devices.return_value = [
        DeviceInfo(device_id=3, vram_capacity_mb=24576),
        DeviceInfo(device_id=7, vram_capacity_mb=12288),
    ]
    detector.get_platform.return_value = Platform.WINDOWS
    detector.has_gpu.return_value = True
    detector.get_vram_capacity_mb.side_effect = lambda device_id: 12288 if device_id == 7 else 24576

    inventory = collect_device_inventory(detector)

    assert inventory.routable_device_ids == [7, 3]
    assert inventory.device_count == 2
    assert [d.device_id for d in inventory.devices] == [7, 3]


def test_collect_device_inventory_should_treat_empty_enumeration_as_authoritative():
    detector = MagicMock()
    detector.enumerate_device_ids = MagicMock(return_value=[])
    detector.list_devices.return_value = [
        DeviceInfo(device_id=0, vram_capacity_mb=8192),
        DeviceInfo(device_id=1, vram_capacity_mb=16384),
    ]
    detector.get_platform.return_value = Platform.LINUX
    detector.has_gpu.return_value = True

    inventory = collect_device_inventory(detector)

    assert inventory.platform == "linux"
    assert inventory.has_gpu is True
    assert inventory.device_count == 0
    assert inventory.routable_device_ids == []
    assert inventory.devices == []
