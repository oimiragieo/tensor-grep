from cudf_grep.gpu.device_detect import DeviceDetector

class MemoryManager:
    def __init__(self):
        self.detector = DeviceDetector()

    def get_vram_budget_mb(self) -> int:
        if not self.detector.has_gpu():
            return 0
        total = self.detector.get_vram_capacity_mb()
        return int(total * 0.8)

    def get_recommended_chunk_size_mb(self) -> int:
        budget = self.get_vram_budget_mb()
        if budget == 0:
            return 0
        return int(budget / 2)

    def should_use_pinned_memory(self) -> bool:
        if self.detector.has_gds():
            return False
        return True
