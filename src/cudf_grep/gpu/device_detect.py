from enum import Enum, auto
import os
import sys

class Platform(Enum):
    LINUX = auto()
    WINDOWS = auto()
    WSL2 = auto()

class DeviceDetector:
    def has_gpu(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    def get_vram_capacity_mb(self) -> int:
        if not self.has_gpu():
            return 0
        try:
            import torch
            props = torch.cuda.get_device_properties(0)
            return props.total_memory // (1024 * 1024)
        except Exception:
            return 0

    def has_gds(self) -> bool:
        if not self.has_gpu():
            return False
        try:
            from kvikio import DriverProperties
            props = DriverProperties()
            return props.is_gds_available
        except Exception:
            return False

    def get_platform(self) -> Platform:
        if sys.platform == "win32":
            return Platform.WINDOWS
        elif sys.platform.startswith("linux"):
            if os.path.exists("/run/WSL"):
                return Platform.WSL2
            return Platform.LINUX
        return Platform.LINUX
