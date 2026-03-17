import json
import subprocess
import os

tg_exe = r"rust_core\target\debug\tg.exe"
evidence_file = r"C:\Users\oimir\.factory\missions\5b1405db-2877-4f44-bc66-0143d658a2ee\evidence\native-gpu-engine\group-1\VAL-GPU-026-comparison.txt"
test_file = r"bench_data\boundary_test.txt"

# Create a file where "BOUNDARY" appears across multiples of 256
with open(test_file, "wb") as f:
    for i in range(1, 10):
        # target byte offset = i * 256
        target_offset = i * 256
        # fill with 'a'
        current_size = f.tell()
        padding = b'a' * (target_offset - current_size - 4)  # leave space before
        f.write(padding)
        f.write(b'xyz BOUNDARY abc\n')
        
    f.write(b'ending line\n')

with open(evidence_file, "w", encoding="utf-8") as out_f:
    cpu_cmd = [tg_exe, "search", "--cpu", "--json", "BOUNDARY", test_file]
    gpu_cmd = [tg_exe, "search", "--gpu-device-ids", "0", "--json", "BOUNDARY", test_file]
    
    cpu_res = subprocess.run(cpu_cmd, capture_output=True, text=True, encoding="utf-8")
    gpu_res = subprocess.run(gpu_cmd, capture_output=True, text=True, encoding="utf-8")
    
    cpu_json = json.loads(cpu_res.stdout) if cpu_res.stdout.strip() else {}
    gpu_json = json.loads(gpu_res.stdout) if gpu_res.stdout.strip() else {}
    
    c_lines = [(m["file"], m["line"], m["text"]) for m in cpu_json.get("matches", [])]
    g_lines = [(m["file"], m["line"], m["text"]) for m in gpu_json.get("matches", [])]
    
    out_f.write(f"--- Pattern: BOUNDARY ---\n")
    out_f.write(f"CPU matches: {len(c_lines)}, GPU matches: {len(g_lines)}\n")
    
    if c_lines == g_lines:
        out_f.write("Line-by-line comparison: IDENTICAL\n\n")
    else:
        out_f.write("Line-by-line comparison: MISMATCH\n")
        out_f.write(f"CPU: {c_lines}\n")
        out_f.write(f"GPU: {g_lines}\n\n")

print("Done")
