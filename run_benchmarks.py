import os
import time
import subprocess
from pathlib import Path

# Scenarios to test
SCENARIOS = [
    {
        "name": "1. Simple String Match",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "ERROR", "bench_data"],
        "tg_args": ["tg", "search", "ERROR", "bench_data"]
    },
    {
        "name": "2. Case-Insensitive Match",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "-i", "warning", "bench_data"],
        "tg_args": ["tg", "search", "-i", "warning", "bench_data"]
    },
    {
        "name": "3. Regex Match",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", r"ERROR.*timeout", "bench_data"],
        "tg_args": ["tg", "search", r"ERROR.*timeout", "bench_data"]
    },
    {
        "name": "4. Invert Match",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "-v", "INFO", "bench_data"],
        "tg_args": ["tg", "search", "-v", "INFO", "bench_data"]
    },
    {
        "name": "5. Count Matches",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "-c", "ERROR", "bench_data"],
        "tg_args": ["tg", "search", "-c", "ERROR", "bench_data"]
    },
    {
        "name": "6. Context Lines (Before & After)",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "-C", "2", "CRITICAL", "bench_data"],
        "tg_args": ["tg", "search", "-C", "2", "CRITICAL", "bench_data"]
    },
    {
        "name": "7. Max Count Limit",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "-m", "5", "ERROR", "bench_data"],
        "tg_args": ["tg", "search", "-m", "5", "ERROR", "bench_data"]
    },
    {
        "name": "8. File Glob Filtering",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "-g", "*.log", "ERROR", "bench_data"],
        "tg_args": ["tg", "search", "-g", "*.log", "ERROR", "bench_data"]
    },
    {
        "name": "9. Word Boundary",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "-w", "timeout", "bench_data"],
        "tg_args": ["tg", "search", "-w", "timeout", "bench_data"]
    },
    {
        "name": "10. Fixed Strings",
        "rg_args": ["ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "-F", "[ERROR]", "bench_data"],
        "tg_args": ["tg", "search", "-F", "[ERROR]", "bench_data"]
    }
]

def generate_test_data(directory: str, num_files: int = 5, lines_per_file: int = 100000):
    print(f"Generating synthetic log data in '{directory}'...")
    os.makedirs(directory, exist_ok=True)
    
    log_templates = [
        "2026-02-25 10:00:01 [INFO] User logged in successfully.\n",
        "2026-02-25 10:00:02 [WARNING] Memory usage is high.\n",
        "2026-02-25 10:00:03 [ERROR] Database connection timeout.\n",
        "2026-02-25 10:00:04 [INFO] Request processed in 20ms.\n",
        "2026-02-25 10:00:05 [CRITICAL] System failure detected!\n",
    ]
    
    for i in range(num_files):
        file_path = os.path.join(directory, f"server_{i}.log")
        with open(file_path, "w", encoding="utf-8") as f:
            for j in range(lines_per_file):
                f.write(log_templates[j % len(log_templates)])
    
    # Add a txt file to test globbing
    with open(os.path.join(directory, "readme.txt"), "w", encoding="utf-8") as f:
        f.write("This is a readme file.\nERROR: do not delete.\n")

def run_cmd(cmd):
    start = time.time()
    try:
        # Run subprocess, suppress output to not clutter benchmark
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception as e:
        print(f"Failed to run {' '.join(cmd)}: {e}")
    return time.time() - start

def main():
    bench_dir = "bench_data"
    generate_test_data(bench_dir, num_files=10, lines_per_file=100000) # ~5M lines total
    
    print("\nStarting Benchmarks: ripgrep vs tensor-grep")
    print("-" * 65)
    print(f"{'Scenario':<35} | {'ripgrep':<10} | {'tensor-grep':<10}")
    print("-" * 65)
    
    # Ensure tg resolves to python module
    tg_cmd = ["python", "-m", "tensor_grep.cli.main", "search"]
    
    for scenario in SCENARIOS:
        rg_cmd = scenario["rg_args"]
        # Replace 'tg search' with the actual python module call to avoid PATH issues
        actual_tg_cmd = tg_cmd + scenario["tg_args"][2:]
        
        # Warmup caches
        run_cmd(rg_cmd)
        run_cmd(actual_tg_cmd)
        
        # Actual benchmark
        rg_time = run_cmd(rg_cmd)
        tg_time = run_cmd(actual_tg_cmd)
        
        print(f"{scenario['name']:<35} | {rg_time:>8.3f}s | {tg_time:>8.3f}s")

if __name__ == "__main__":
    main()
