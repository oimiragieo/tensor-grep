from tensor_grep.gpu.device_detect import DeviceDetector
from typing import List

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
            return 0
        return int(budget / 2)
        
    def get_all_device_chunk_sizes_mb(self) -> List[int]:
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
