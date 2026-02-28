from tensor_grep.core.hardware.device_detect import DeviceDetector


class MemoryManager:
    def __init__(self) -> None:
        self.detector = DeviceDetector()

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
            return max(256, min(cpu_chunk, 1024)) # Bound between 256MB and 1GB per process

        return int(budget / 2)

    def get_all_device_chunk_sizes_mb(self) -> list[int]:
        if not self.detector.has_gpu():
            return []

        count = self.detector.get_device_count()
        if count == 0:
            return []

        return [self.get_recommended_chunk_size_mb(i) for i in range(count)]

    def should_use_pinned_memory(self) -> bool:
        if self.detector.has_gds():
            return False
        return True
