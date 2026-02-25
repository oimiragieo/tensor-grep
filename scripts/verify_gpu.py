import sys
import time

import torch

from tensor_grep.backends.torch_backend import TorchBackend
from tensor_grep.core.config import SearchConfig


def main():
    print(f"PyTorch Version: {torch.__version__}")
    print(f"CUDA Available: {torch.cuda.is_available()}")
    print(f"Device Count: {torch.cuda.device_count()}")
    for i in range(torch.cuda.device_count()):
        print(f"Device {i}: {torch.cuda.get_device_name(i)}")

    backend = TorchBackend()
    print(f"\nTorchBackend is_available(): {backend.is_available()}")

    if not backend.is_available():
        print("Backend not available, exiting.")
        sys.exit(1)

    print("\nForcing TorchBackend search on bench_data/server_0.log...")
    config = SearchConfig(debug=True)

    start = time.time()
    result = backend.search("bench_data/server_0.log", "ERROR", config)
    end = time.time()

    print(f"\nSearch complete in {end - start:.3f}s")
    print(f"Total matches found: {result.total_matches}")


if __name__ == "__main__":
    main()
