import os
import subprocess
import json
import sys

# Generate 10MB corpus
with open("bench_data/server_0.log", "rb") as f:
    data = f.read(10 * 1024 * 1024)
    # Ensure we end on a newline
    last_nl = data.rfind(b'\n')
    if last_nl != -1:
        data = data[:last_nl+1]
with open("bench_data/10mb.log", "wb") as f:
    f.write(data)

patterns = ["ERROR", "timeout", "WARN", "user", "connection"]
tg_exe = r"rust_core\target\debug\tg.exe"
evidence_file = r"C:\Users\oimir\.factory\missions\5b1405db-2877-4f44-bc66-0143d658a2ee\evidence\native-gpu-engine\group-1\VAL-GPU-001-diff.txt"

with open(evidence_file, "w") as out_f:
    for pat in patterns:
        print(f"Testing pattern: {pat}")
        cpu_cmd = [tg_exe, "search", "--cpu", "--json", pat, "bench_data/10mb.log"]
        gpu_cmd = [tg_exe, "search", "--gpu-device-ids", "0", "--json", pat, "bench_data/10mb.log"]
        
        cpu_res = subprocess.run(cpu_cmd, capture_output=True, text=True)
        gpu_res = subprocess.run(gpu_cmd, capture_output=True, text=True)
        
        if cpu_res.returncode not in (0, 1) or gpu_res.returncode not in (0, 1):
            out_f.write(f"Pattern {pat} failed execution.\nCPU err: {cpu_res.stderr}\nGPU err: {gpu_res.stderr}\n")
            continue
            
        try:
            cpu_json = json.loads(cpu_res.stdout) if cpu_res.stdout.strip() else {}
            gpu_json = json.loads(gpu_res.stdout) if gpu_res.stdout.strip() else {}
        except json.JSONDecodeError as e:
            out_f.write(f"Pattern {pat} JSON parsing failed: {e}\nCPU output: {cpu_res.stdout[:200]}\nGPU output: {gpu_res.stdout[:200]}\n")
            continue
            
        # extract total_matches, total_files, and matches
        c_matches = cpu_json.get("total_matches", 0)
        g_matches = gpu_json.get("total_matches", 0)
        c_files = cpu_json.get("total_files", 0)
        g_files = gpu_json.get("total_files", 0)
        
        c_lines = [(m["file"], m["line"], m["text"]) for m in cpu_json.get("matches", [])]
        g_lines = [(m["file"], m["line"], m["text"]) for m in gpu_json.get("matches", [])]
        
        c_lines.sort()
        g_lines.sort()
        
        out_f.write(f"--- Pattern: {pat} ---\n")
        out_f.write(f"CPU matches: {c_matches}, GPU matches: {g_matches}\n")
        out_f.write(f"CPU files: {c_files}, GPU files: {g_files}\n")
        
        if c_lines == g_lines:
            out_f.write("Line-by-line comparison: IDENTICAL\n\n")
        else:
            out_f.write("Line-by-line comparison: MISMATCH\n")
            out_f.write(f"CPU lines ({len(c_lines)}): {c_lines[:5]}...\n")
            out_f.write(f"GPU lines ({len(g_lines)}): {g_lines[:5]}...\n\n")

print("Done")
