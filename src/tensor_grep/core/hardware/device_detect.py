import os
import sys
from enum import Enum, auto


class Platform(Enum):
    LINUX = auto()
    WINDOWS = auto()
    WSL2 = auto()


class DeviceDetector:
    def has_gpu(self) -> bool:
        # Fast path: check if NVML library exists without loading torch
        if sys.platform == "win32":
            nvml_path = os.path.join(
                os.environ.get("WINDIR", "C:\\Windows"), "System32", "nvml.dll"
            )
            if os.path.exists(nvml_path):
                try:
                    import ctypes

                    nvml = ctypes.WinDLL(nvml_path)
                    nvml.nvmlInit_v2()
                    count = ctypes.c_uint()
                    nvml.nvmlDeviceGetCount_v2(ctypes.byref(count))
                    if count.value > 0:
                        return True
                except Exception:
                    pass
        else:
            paths = [
                "/usr/lib/wsl/lib/libnvidia-ml.so.1",
                "/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1",
                "/usr/lib64/libnvidia-ml.so.1",
            ]
            for p in paths:
                if os.path.exists(p):
                    try:
                        import ctypes

                        nvml = ctypes.CDLL(p)
                        nvml.nvmlInit_v2()
                        count = ctypes.c_uint()
                        nvml.nvmlDeviceGetCount_v2(ctypes.byref(count))
                        if count.value > 0:
                            return True
                    except Exception:
                        pass
                    break

        try:
            import torch

            return bool(torch.cuda.is_available())
        except ImportError:
            return False

    def get_device_count(self) -> int:
        if not self.has_gpu():
            return 0

        # Try fast NVML binding first to avoid torch overhead
        try:
            import ctypes

            if sys.platform == "win32":
                nvml_path = os.path.join(
                    os.environ.get("WINDIR", "C:\\Windows"), "System32", "nvml.dll"
                )
                nvml = ctypes.WinDLL(nvml_path)
            else:
                paths = [
                    "/usr/lib/wsl/lib/libnvidia-ml.so.1",
                    "/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1",
                    "/usr/lib64/libnvidia-ml.so.1",
                ]
                nvml = None
                for p in paths:
                    if os.path.exists(p):
                        nvml = ctypes.CDLL(p)
                        break
                if not nvml:
                    raise Exception("NVML not found")

            nvml.nvmlInit_v2()
            count = ctypes.c_uint()
            nvml.nvmlDeviceGetCount_v2(ctypes.byref(count))
            return int(count.value)
        except Exception:
            pass

        try:
            import torch

            return int(torch.cuda.device_count())
        except Exception:
            return 0

    def get_vram_capacity_mb(self, device_id: int = 0) -> int:
        if not self.has_gpu():
            return 0

        # Try fast NVML binding first
        try:
            import ctypes

            if sys.platform == "win32":
                nvml_path = os.path.join(
                    os.environ.get("WINDIR", "C:\\Windows"), "System32", "nvml.dll"
                )
                nvml = ctypes.WinDLL(nvml_path)
            else:
                paths = [
                    "/usr/lib/wsl/lib/libnvidia-ml.so.1",
                    "/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1",
                    "/usr/lib64/libnvidia-ml.so.1",
                ]
                nvml = None
                for p in paths:
                    if os.path.exists(p):
                        nvml = ctypes.CDLL(p)
                        break
                if not nvml:
                    raise Exception("NVML not found")

            nvml.nvmlInit_v2()

            class c_nvmlMemory(ctypes.Structure):
                pass

            c_nvmlMemory._fields_ = [
                ("total", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong),
            ]

            handle = ctypes.c_void_p()
            nvml.nvmlDeviceGetHandleByIndex_v2(device_id, ctypes.byref(handle))

            memInfo = c_nvmlMemory()
            nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memInfo))

            return int(memInfo.total // (1024 * 1024))
        except Exception:
            pass

        try:
            import torch

            props = torch.cuda.get_device_properties(device_id)
            return int(props.total_memory // (1024 * 1024))
        except Exception:
            return 0

    def has_gds(self) -> bool:
        if not self.has_gpu():
            return False
        try:
            from kvikio import DriverProperties

            props = DriverProperties()
            return bool(props.is_gds_available)
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
