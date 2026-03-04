from unittest.mock import MagicMock

from tensor_grep.core.hardware.device_detect import DeviceInfo, Platform
from tensor_grep.core.hardware.device_inventory import DeviceInventory, collect_device_inventory


def test_device_inventory_to_dict_should_serialize_devices():
    inventory = DeviceInventory(
        platform="windows",
        has_gpu=True,
        device_count=2,
        devices=[
            DeviceInfo(device_id=7, vram_capacity_mb=12288),
            DeviceInfo(device_id=3, vram_capacity_mb=24576),
        ],
    )

    payload = inventory.to_dict()

    assert payload["platform"] == "windows"
    assert payload["has_gpu"] is True
    assert payload["device_count"] == 2
    assert payload["devices"] == [
        {"device_id": 7, "vram_capacity_mb": 12288},
        {"device_id": 3, "vram_capacity_mb": 24576},
    ]


def test_collect_device_inventory_should_read_from_detector_contract():
    detector = MagicMock()
    detector.list_devices.return_value = [DeviceInfo(device_id=1, vram_capacity_mb=8192)]
    detector.get_platform.return_value = Platform.WINDOWS
    detector.has_gpu.return_value = True

    inventory = collect_device_inventory(detector)

    assert inventory.platform == "windows"
    assert inventory.has_gpu is True
    assert inventory.device_count == 1
    assert inventory.devices == [DeviceInfo(device_id=1, vram_capacity_mb=8192)]
