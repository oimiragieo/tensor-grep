import os
import sys
from dataclasses import dataclass
from enum import Enum, auto


class Platform(Enum):
    LINUX = auto()
    WINDOWS = auto()
    WSL2 = auto()


@dataclass(frozen=True)
class DeviceInfo:
    device_id: int
    vram_capacity_mb: int


class DeviceDetector:
    def __init__(self) -> None:
        self._has_gpu_cache: bool | None = None
        self._device_count_cache: int | None = None
        self._vram_capacity_cache_mb: dict[int, int] = {}

    def clear_cache(self) -> None:
        self._has_gpu_cache = None
        self._device_count_cache = None
        self._vram_capacity_cache_mb.clear()

    @staticmethod
    def _parse_explicit_device_ids() -> list[int] | None:
        """
        Parse explicit routing IDs from TENSOR_GREP_DEVICE_IDS.
        Returns None when unset; otherwise returns a de-duplicated list.
        """
        raw = os.environ.get("TENSOR_GREP_DEVICE_IDS")
        if raw is None:
            return None

        ids: list[int] = []
        seen: set[int] = set()
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                value = int(token)
            except ValueError:
                continue
            if value < 0 or value in seen:
                continue
            seen.add(value)
            ids.append(value)

        return ids

    def has_gpu(self) -> bool:
        if self._has_gpu_cache is not None:
            return self._has_gpu_cache

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
                        self._has_gpu_cache = True
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
                            self._has_gpu_cache = True
                            return True
                    except Exception:
                        pass
                    break

        try:
            import torch

            self._has_gpu_cache = bool(torch.cuda.is_available())
            return self._has_gpu_cache
        except ImportError:
            self._has_gpu_cache = False
            return False

    def get_device_count(self) -> int:
        if self._device_count_cache is not None:
            return self._device_count_cache

        if not self.has_gpu():
            self._device_count_cache = 0
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
            self._device_count_cache = int(count.value)
            return self._device_count_cache
        except Exception:
            pass

        try:
            import torch

            self._device_count_cache = int(torch.cuda.device_count())
            return self._device_count_cache
        except Exception:
            self._device_count_cache = 0
            return 0

    def get_device_ids(self) -> list[int]:
        """
        Return concrete CUDA device IDs available for routing/sharding.
        We keep this explicit API so higher layers don't assume [0..N-1].
        """
        count = self.get_device_count()
        if count <= 0:
            return []
        explicit_ids = self._parse_explicit_device_ids()
        if explicit_ids is None:
            return list(range(count))

        filtered_ids = [device_id for device_id in explicit_ids if device_id < count]
        return filtered_ids if filtered_ids else list(range(count))

    def enumerate_device_ids(self) -> list[int]:
        """
        Public stable API for routing layers that only need concrete, routable
        CUDA device IDs in selection order.
        """
        return self.get_device_ids()

    def list_devices(self) -> list[DeviceInfo]:
        """
        Public device enumeration API for routing and scheduling layers.
        Returns concrete CUDA device IDs and their VRAM capacity in MB.
        """
        device_ids = self.get_device_ids()
        if not device_ids:
            return []

        devices: list[DeviceInfo] = []
        for device_id in device_ids:
            devices.append(
                DeviceInfo(
                    device_id=device_id,
                    vram_capacity_mb=self.get_vram_capacity_mb(device_id),
                )
            )
        return devices

    def get_vram_capacity_mb(self, device_id: int = 0) -> int:
        cached = self._vram_capacity_cache_mb.get(device_id)
        if cached is not None:
            return cached

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

            vram_mb = int(memInfo.total // (1024 * 1024))
            self._vram_capacity_cache_mb[device_id] = vram_mb
            return vram_mb
        except Exception:
            pass

        try:
            import torch

            props = torch.cuda.get_device_properties(device_id)
            vram_mb = int(props.total_memory // (1024 * 1024))
            self._vram_capacity_cache_mb[device_id] = vram_mb
            return vram_mb
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
