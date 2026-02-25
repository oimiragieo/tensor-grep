import sys


class DStorageReader:
    def is_available(self) -> bool:
        try:
            import dstorage_gpu

            return sys.platform == "win32"
        except ImportError:
            return False

    def read_to_gpu(self, file_path: str) -> "dstorage_gpu.Tensor":  # type: ignore
        import dstorage_gpu

        loader = dstorage_gpu.DirectStorageLoader()
        return loader.load_tensor(file_path)
