import subprocess
import torch
import sys
import os

print("Allocating VRAM...")
device = torch.device("cuda:0")
try:
    # Try to allocate most of the memory
    total_memory = torch.cuda.get_device_properties(0).total_memory
    print(f"Total memory: {total_memory / 1e9:.2f} GB")
    # Allocate in chunks to fill up without crashing Python
    tensors = []
    # Leave very little memory (e.g., 50MB)
    target_free = 50 * 1024 * 1024
    to_allocate = int(total_memory * 0.95) # allocate 95%
    print(f"Allocating {to_allocate / 1e9:.2f} GB...")
    t = torch.empty(to_allocate, dtype=torch.uint8, device=device)
    tensors.append(t)
    print("Memory allocated. Running tg search...")

    with open("dummy_test_file.txt", "w") as f:
        f.write("test " * 1000)

    result = subprocess.run(
        ["rust_core/target/debug/tg.exe", "search", "--gpu-device-ids", "0", "test", "dummy_test_file.txt"],
        capture_output=True,
        text=True
    )

    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)
    print("EXIT CODE:", result.returncode)

except Exception as e:
    print(f"Error: {e}")
finally:
    # Cleanup
    if os.path.exists("dummy_test_file.txt"):
        os.remove("dummy_test_file.txt")
