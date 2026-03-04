from dataclasses import dataclass

from tensor_grep.core.hardware.device_detect import DeviceDetector, DeviceInfo


@dataclass(frozen=True)
class DeviceInventory:
    platform: str
    has_gpu: bool
    device_count: int
    devices: list[DeviceInfo]

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "has_gpu": self.has_gpu,
            "device_count": self.device_count,
            "devices": [
                {"device_id": device.device_id, "vram_capacity_mb": device.vram_capacity_mb}
                for device in self.devices
            ],
        }


def collect_device_inventory(detector: DeviceDetector | None = None) -> DeviceInventory:
    resolved_detector = detector or DeviceDetector()
    devices_info = resolved_detector.list_devices()
    return DeviceInventory(
        platform=resolved_detector.get_platform().name.lower(),
        has_gpu=resolved_detector.has_gpu(),
        device_count=len(devices_info),
        devices=devices_info,
    )
