import json
import sys

def parse_output(filename):
    with open(filename, 'r') as f:
        content = f.read()
        
    # Find the JSON part
    lines = content.split('\n')
    json_lines = []
    debug_lines = []
    in_json = False
    
    # Simple heuristic to extract json block
    for line in lines:
        if line.startswith('{'):
            in_json = True
        if in_json:
            json_lines.append(line)
        else:
            debug_lines.append(line)
            
    json_str = '\n'.join(json_lines)
    try:
        data = json.loads(json_str)
        return data, '\n'.join(debug_lines)
    except json.JSONDecodeError as e:
        print(f"Failed to decode json from {filename}")
        return {}, content

cpu_data, _ = parse_output(r"C:\Users\oimir\.factory\missions\5b1405db-2877-4f44-bc66-0143d658a2ee\evidence\native-gpu-engine\group-1\VAL-GPU-003-cpu.txt")
gpu_data, gpu_debug = parse_output(r"C:\Users\oimir\.factory\missions\5b1405db-2877-4f44-bc66-0143d658a2ee\evidence\native-gpu-engine\group-1\VAL-GPU-003-gpu.txt")

with open(r"C:\Users\oimir\.factory\missions\5b1405db-2877-4f44-bc66-0143d658a2ee\evidence\native-gpu-engine\group-1\VAL-GPU-003-comparison.txt", "w") as out:
    c_matches = cpu_data.get("total_matches")
    g_matches = gpu_data.get("total_matches")
    
    # CPU doesn't output total_files when running on many files if it's not in the JSON structure? Let's check
    c_files = len(set(m["file"] for m in cpu_data.get("matches", [])))
    g_files = gpu_data.get("total_files")
    
    out.write(f"CPU matches: {c_matches}, GPU matches: {g_matches}\n")
    out.write(f"CPU files matched: {c_files}, GPU files reported: {g_files}\n")
    
    batch_found = "batch" in gpu_debug.lower() or "transfer" in gpu_debug.lower()
    out.write(f"Debug shows batch transfer: {batch_found}\n")
    if batch_found:
        out.write("Found batching lines in debug output.\n")

print("Done")
