class KvikIOReader:
    """Experimental GPU Direct Storage reader spike -- NOT wired into the
    production read path. Production GPU reads use the Rust FFI
    ``read_mmap_to_arrow_chunked`` via ``cudf_backend``; this class is retained
    behind its own unit test only and is not constructed at runtime.
    """

    def is_available(self) -> bool:
        try:
            import importlib.util

            if not importlib.util.find_spec("kvikio"):
                return False

            return True
        except ImportError:
            return False

    def read_to_gpu(self, file_path: str) -> bytes:
        import kvikio

        # Simplified for demonstration. Normally you'd allocate a CuPy array and read into it.
        cufile = kvikio.CuFile(file_path, "r")
        return bytes(cufile.read())
