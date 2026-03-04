from tensor_grep.core.hardware.device_detect import DeviceDetector


class MemoryManager:
    def __init__(self) -> None:
        self.detector = DeviceDetector()
        self._cached_detected_device_ids: list[int] | None = None

    def get_vram_budget_mb(self, device_id: int = 0) -> int:
        if not self.detector.has_gpu():
            return 0
        total = self.detector.get_vram_capacity_mb(device_id)
        return int(total * 0.8)

    def get_recommended_chunk_size_mb(self, device_id: int = 0) -> int:
        budget = self.get_vram_budget_mb(device_id)
        if budget == 0:
            import os

            import psutil

            try:
                system_ram_mb = psutil.virtual_memory().total / (1024 * 1024)
            except Exception:
                # Fallback to a safe estimate if psutil fails
                system_ram_mb = 8192

            cpu_count = os.cpu_count() or 4

            # Use ~40% of system RAM, divided by number of CPUs to give a sensible chunk
            cpu_chunk = int((system_ram_mb * 0.4) / cpu_count)
            return max(256, min(cpu_chunk, 1024))  # Bound between 256MB and 1GB per process

        return int(budget / 2)

    def get_all_device_chunk_sizes_mb(self) -> list[int]:
        return [chunk_mb for _, chunk_mb in self.get_device_chunk_plan_mb()]

    def _get_detected_device_ids(self) -> list[int]:
        if self._cached_detected_device_ids is not None:
            return list(self._cached_detected_device_ids)

        if not self.detector.has_gpu():
            self._cached_detected_device_ids = []
            return []
        try:
            if hasattr(self.detector, "enumerate_device_ids"):
                enumerated_ids = list(self.detector.enumerate_device_ids())
                if enumerated_ids:
                    self._cached_detected_device_ids = list(enumerated_ids)
                    return list(enumerated_ids)

            # Compatibility path for detectors that expose concrete ID enumeration
            # without requiring full device metadata collection.
            if hasattr(self.detector, "get_device_ids"):
                legacy_ids = list(self.detector.get_device_ids())
                if legacy_ids:
                    self._cached_detected_device_ids = list(legacy_ids)
                    return list(legacy_ids)

            devices = self.detector.list_devices()
            device_ids = [device.device_id for device in devices]
            if device_ids:
                self._cached_detected_device_ids = list(device_ids)
                return list(device_ids)
        except Exception:
            # Backward-compatible fallback when detector does not expose IDs.
            pass

        try:
            raw_count = self.detector.get_device_count()
            count = raw_count if isinstance(raw_count, int) and raw_count >= 0 else 0
        except Exception:
            count = 0
        fallback_ids = list(range(count)) if count > 0 else []
        self._cached_detected_device_ids = list(fallback_ids)
        return fallback_ids

    def get_device_ids(self, preferred_ids: list[int] | None = None) -> list[int]:
        detected_ids = self._get_detected_device_ids()
        if not detected_ids:
            return []
        if not preferred_ids:
            return detected_ids

        detected_set = set(detected_ids)
        normalized: list[int] = []
        seen: set[int] = set()
        for device_id in preferred_ids:
            if device_id in seen:
                continue
            if device_id in detected_set:
                normalized.append(device_id)
                seen.add(device_id)

        # Preserve backward-compatible behavior: if all requested IDs are invalid,
        # fall back to the detected routable set instead of disabling GPU usage.
        return normalized if normalized else detected_ids

    def get_device_chunk_plan_mb(
        self, preferred_ids: list[int] | None = None
    ) -> list[tuple[int, int]]:
        device_ids = self.get_device_ids(preferred_ids=preferred_ids)
        if not device_ids:
            return []
        return [
            (device_id, self.get_recommended_chunk_size_mb(device_id)) for device_id in device_ids
        ]

    def should_use_pinned_memory(self) -> bool:
        if self.detector.has_gds():
            return False
        return True
