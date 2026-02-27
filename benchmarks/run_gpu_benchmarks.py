import time
from pathlib import Path

# Setup minimal data for GPU benchmark if not exists
BENCH_DIR = Path("gpu_bench_data")
BENCH_DIR.mkdir(exist_ok=True)

# 1. AST Data (Python)
AST_FILE = BENCH_DIR / "test_api.py"
AST_FILE.write_text("""
def process_data(data):
    if not data:
        return None
    for item in data:
        print(item)
    return True

class DataManager:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)

    def process_data(self, data):
        return [x * 2 for x in data]
""")

# 2. cyBERT Data (Logs)
LOG_FILE = BENCH_DIR / "system.log"
# Create a decent sized log for classification (10,000 lines)
log_content = "2026-02-25 10:00:01 [INFO] User logged in successfully.\n" * 4000
log_content += "2026-02-25 10:00:02 [WARNING] Memory usage is high.\n" * 4000
log_content += "2026-02-25 10:00:03 [ERROR] Database connection timeout.\n" * 2000
LOG_FILE.write_text(log_content)


print("Starting Advanced GPU Backends Benchmark...")
print("-" * 65)

# --- AST Backend Benchmark ---
try:
    import unittest.mock as mock

    with mock.patch("tensor_grep.backends.ast_backend.AstBackend.is_available", return_value=True):
        from tensor_grep.backends.ast_backend import AstBackend
        from tensor_grep.core.config import SearchConfig

        print("Testing AST Backend (Structural Code Search):")
        ast_backend = AstBackend()
        cfg = SearchConfig(ast=True, lang="python")

        start = time.time()
        # Find method definition matching 'def process_data($DATA)'
        res = ast_backend.search(str(AST_FILE), "def process_data($DATA):", cfg)
        ast_time = time.time() - start

        print("  Query: 'def process_data($DATA):'")
        print(f"  Found {res.total_matches} structural matches in {ast_time:.3f}s")
        print("-" * 65)
except Exception as e:
    print(f"AST Backend failed: {e}")
    import traceback

    traceback.print_exc()
    print("-" * 65)

# --- cyBERT Backend Benchmark ---
try:
    print("Testing cyBERT Backend (Semantic NLP Log Classification):")
    from tensor_grep.backends.cybert_backend import CybertBackend

    cybert_backend = CybertBackend()

    # Read the 10,000 log lines
    lines = LOG_FILE.read_text().splitlines()

    start = time.time()
    classifications = cybert_backend.classify(lines)
    cybert_time = time.time() - start

    error_count = sum(1 for c in classifications if c["label"] == "error")

    print(f"  Processed {len(lines)} lines")
    print(f"  Found {error_count} ERRORs semantically in {cybert_time:.3f}s")
    print("-" * 65)
except Exception as e:
    print(f"cyBERT Backend failed: {e}")
    import traceback

    traceback.print_exc()
    print("-" * 65)

# --- TorchBackend String Benchmark ---
try:
    import unittest.mock as mock

    from tensor_grep.backends.torch_backend import TorchBackend
    from tensor_grep.core.config import SearchConfig

    print("Testing TorchBackend (VRAM-native Exact String Matching):")
    with (
        mock.patch("torch.cuda.is_available", return_value=True),
        mock.patch("torch.device", return_value="cpu"),
    ):
        torch_backend = TorchBackend()
        # Force the backend to use CPU since the container has no GPU
        torch_backend.device = "cpu"
        cfg = SearchConfig()

        start = time.time()
        res = torch_backend.search(str(LOG_FILE), "Database connection timeout", cfg)
        torch_time = time.time() - start

        print("  Query: 'Database connection timeout'")
        print(f"  Found {res.total_matches} matches across 10k lines in {torch_time:.3f}s")
        print("-" * 65)
except Exception as e:
    print(f"Torch Backend failed: {e}")
    import traceback

    traceback.print_exc()
    print("-" * 65)
