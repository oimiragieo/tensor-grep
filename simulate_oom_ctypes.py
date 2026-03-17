import ctypes
import subprocess
import time
import os

print("Loading nvcuda.dll...")
try:
    nvcuda = ctypes.windll.LoadLibrary("nvcuda.dll")
    
    # Initialize CUDA
    nvcuda.cuInit(0)
    
    # Get device 0
    device = ctypes.c_int()
    nvcuda.cuDeviceGet(ctypes.byref(device), 0)
    
    # Create context
    context = ctypes.c_void_p()
    nvcuda.cuCtxCreate_v2(ctypes.byref(context), 0, device)
    
    # Get free and total memory
    free_mem = ctypes.c_size_t()
    total_mem = ctypes.c_size_t()
    nvcuda.cuMemGetInfo_v2(ctypes.byref(free_mem), ctypes.byref(total_mem))
    print(f"Free: {free_mem.value/1e9:.2f} GB, Total: {total_mem.value/1e9:.2f} GB")
    
    # Allocate almost all free memory (leave 2MB)
    to_allocate = int(free_mem.value - 2 * 1024 * 1024) # leave 2MB
    print(f"Allocating {to_allocate/1e9:.2f} GB...")
    ptr = ctypes.c_void_p()
    nvcuda.cuMemAlloc_v2.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
    result = nvcuda.cuMemAlloc_v2(ctypes.byref(ptr), to_allocate)
    print(f"Allocation result: {result}")
    
    if result == 0:
        print("Memory allocated successfully. Running tg.exe...")
        with open("dummy_test_file.txt", "w") as f:
            f.write("test " * 10000000) # ~50MB file

        proc = subprocess.run(
            ["rust_core/target/debug/tg.exe", "search", "--gpu-device-ids", "0", "test", "dummy_test_file.txt"],
            capture_output=True,
            text=True
        )

        print("STDOUT:", proc.stdout)
        print("STDERR:", proc.stderr)
        print("EXIT CODE:", proc.returncode)
        
        # Write to evidence file
        evidence_path = r"C:\Users\oimir\.factory\missions\5b1405db-2877-4f44-bc66-0143d658a2ee\evidence\native-gpu-engine\group-3\VAL-GPU-021-oom.txt"
        with open(evidence_path, "w") as f:
            f.write(f"EXIT CODE: {proc.returncode}\n")
            f.write(f"STDOUT:\n{proc.stdout}\n")
            f.write(f"STDERR:\n{proc.stderr}\n")
        
        nvcuda.cuMemFree_v2(ptr)
    else:
        print("Failed to allocate memory")
        
except Exception as e:
    print(f"Error: {e}")
finally:
    if os.path.exists("dummy_test_file.txt"):
        os.remove("dummy_test_file.txt")
