class KvikIOReader:
    def is_available(self) -> bool:
        try:
            import kvikio
            return True
        except ImportError:
            return False

    def read_to_gpu(self, file_path: str):
        import kvikio
        # Simplified for demonstration. Normally you'd allocate a CuPy array and read into it.
        cufile = kvikio.CuFile(file_path, "r")
        return cufile.read()
