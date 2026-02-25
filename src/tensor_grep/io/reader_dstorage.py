import sys
from typing import Any


class DStorageReader:
    def is_available(self) -> bool:
        try:
            import importlib.util

            if not importlib.util.find_spec("dstorage_gpu"):
                return False

            return sys.platform == "win32"
        except ImportError:
            return False

    def read_to_gpu(self, file_path: str) -> Any:
        import dstorage_gpu

        loader = dstorage_gpu.DirectStorageLoader()
        return loader.load_tensor(file_path)
