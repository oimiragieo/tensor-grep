import os

def generate_file(path, size_mb, pattern_freq_kb=100):
    size_bytes = size_mb * 1024 * 1024
    chunk = b"A" * 1024 + b"\n"
    pattern = b"TEST_PATTERN\n"
    
    with open(path, "wb") as f:
        written = 0
        while written < size_bytes:
            if written % (pattern_freq_kb * 1024) == 0:
                f.write(pattern)
                written += len(pattern)
            else:
                f.write(chunk)
                written += len(chunk)

os.makedirs("C:\\Users\\oimir\\.factory\\missions\\5b1405db-2877-4f44-bc66-0143d658a2ee\\evidence\\native-gpu-engine\\group-2\\test_data", exist_ok=True)
generate_file("C:\\Users\\oimir\\.factory\\missions\\5b1405db-2877-4f44-bc66-0143d658a2ee\\evidence\\native-gpu-engine\\group-2\\test_data\\10MB.txt", 10)
generate_file("C:\\Users\\oimir\\.factory\\missions\\5b1405db-2877-4f44-bc66-0143d658a2ee\\evidence\\native-gpu-engine\\group-2\\test_data\\100MB.txt", 100)
generate_file("C:\\Users\\oimir\\.factory\\missions\\5b1405db-2877-4f44-bc66-0143d658a2ee\\evidence\\native-gpu-engine\\group-2\\test_data\\1GB.txt", 1024)
