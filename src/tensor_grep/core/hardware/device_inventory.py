import inspect
from dataclasses import dataclass

from tensor_grep.core.hardware.device_detect import DeviceDetector, DeviceInfo


@dataclass(frozen=True)
class DeviceInventory:
    platform: str
    has_gpu: bool
    device_count: int
    routable_device_ids: list[int]
    devices: list[DeviceInfo]

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "has_gpu": self.has_gpu,
            "device_count": self.device_count,
            "routable_device_ids": list(self.routable_device_ids),
            "devices": [
                {"device_id": device.device_id, "vram_capacity_mb": device.vram_capacity_mb}
                for device in self.devices
            ],
        }


def collect_device_inventory(detector: DeviceDetector | None = None) -> DeviceInventory:
    resolved_detector = detector or DeviceDetector()

    routable_device_ids: list[int] = []
    used_enumeration_contract = False
    try:
        if _has_detector_method(resolved_detector, "enumerate_device_ids"):
            routable_device_ids = list(resolved_detector.enumerate_device_ids())
            used_enumeration_contract = True
        elif _has_detector_method(resolved_detector, "get_device_ids"):
            routable_device_ids = list(resolved_detector.get_device_ids())
    except Exception:
        routable_device_ids = []

    devices_info = resolved_detector.list_devices()
    if routable_device_ids:
        by_id = {device.device_id: device for device in devices_info}
        normalized_devices: list[DeviceInfo] = []
        for device_id in routable_device_ids:
            if device_id in by_id:
                normalized_devices.append(by_id[device_id])
                continue
            normalized_devices.append(
                DeviceInfo(
                    device_id=device_id,
                    vram_capacity_mb=resolved_detector.get_vram_capacity_mb(device_id),
                )
            )
        devices_info = normalized_devices
    elif not used_enumeration_contract:
        routable_device_ids = [device.device_id for device in devices_info]
    else:
        devices_info = []

    return DeviceInventory(
        platform=resolved_detector.get_platform().name.lower(),
        has_gpu=resolved_detector.has_gpu(),
        device_count=len(routable_device_ids),
        routable_device_ids=routable_device_ids,
        devices=devices_info,
    )


def _has_detector_method(detector: DeviceDetector, name: str) -> bool:
    try:
        inspect.getattr_static(detector, name)
    except AttributeError:
        return False
    return callable(getattr(detector, name, None))
