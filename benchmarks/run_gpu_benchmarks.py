import os
import platform
import sys
import time
from importlib.util import find_spec
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def resolve_gpu_bench_data_dir() -> Path:
    """
    Resolve GPU benchmark data location. Defaults to artifacts to avoid mutating
    tracked repository fixtures during repeated local/CI benchmark runs.
    """
    override = os.environ.get("TENSOR_GREP_GPU_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "gpu_bench_data"


def _module_available(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except Exception:
        return False


def _record_result(
    rows: list[dict[str, object]], name: str, time_s: float | None, details: str, status: str
) -> None:
    rows.append({
        "backend": name,
        "time_s": round(time_s, 6) if isinstance(time_s, float) else None,
        "status": status,
        "details": details,
    })


def _is_skippable_cybert_exception(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "connection refused",
            "actively refused",
            "failed to establish a new connection",
            "timed out",
        )
    )


def main() -> int:
    # Ensure local `src/` imports work when running this script directly.
    root_dir = ROOT_DIR
    src_dir = root_dir / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from tensor_grep.perf_guard import ensure_artifacts_dir, write_json

    # Setup minimal data for GPU benchmark if not exists
    bench_dir = resolve_gpu_bench_data_dir()
    bench_dir.mkdir(parents=True, exist_ok=True)

    # 1. AST Data (Python)
    ast_file = bench_dir / "test_api.py"
    ast_file.write_text(
        """
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
""",
        encoding="utf-8",
    )

    # 2. cyBERT Data (Logs)
    log_file = bench_dir / "system.log"
    # Create a decent sized log for classification (10,000 lines)
    log_content = "2026-02-25 10:00:01 [INFO] User logged in successfully.\n" * 4000
    log_content += "2026-02-25 10:00:02 [WARNING] Memory usage is high.\n" * 4000
    log_content += "2026-02-25 10:00:03 [ERROR] Database connection timeout.\n" * 2000
    log_file.write_text(log_content, encoding="utf-8")

    print("Starting Advanced GPU Backends Benchmark...")
    print("-" * 65)

    failures: list[str] = []
    rows: list[dict[str, object]] = []

    # --- AST Backend Benchmark ---
    try:
        import unittest.mock as mock

        if not _module_available("tree_sitter"):
            raise RuntimeError("tree_sitter not installed (install benchmark deps: tree-sitter-*)")

        from tensor_grep.backends.ast_backend import AstBackend
        from tensor_grep.core.config import SearchConfig

        with mock.patch.object(AstBackend, "is_available", return_value=True):
            print("Testing AST Backend (Structural Code Search):")
            ast_backend = AstBackend()
            cfg = SearchConfig(ast=True, lang="python")

            start = time.time()
            # Use a valid tree-sitter query node pattern for python function definitions.
            res = ast_backend.search(str(ast_file), "function_definition", cfg)
            ast_time = time.time() - start

            print("  Query: 'function_definition'")
            print(f"  Found {res.total_matches} structural matches in {ast_time:.3f}s")
            print("-" * 65)
            _record_result(
                rows,
                "ast_backend",
                ast_time,
                f"query=function_definition matches={res.total_matches}",
                "PASS",
            )
    except Exception as exc:
        print(f"AST Backend failed: {exc}")
        print("-" * 65)
        failures.append(f"AST backend failed: {exc}")
        _record_result(rows, "ast_backend", None, str(exc), "FAIL")

    # --- cyBERT Backend Benchmark ---
    try:
        print("Testing cyBERT Backend (Semantic NLP Log Classification):")
        from tensor_grep.backends.cybert_backend import CybertBackend

        cybert_backend = CybertBackend()

        # Read the 10,000 log lines
        lines = log_file.read_text(encoding="utf-8").splitlines()

        start = time.time()
        classifications = cybert_backend.classify(lines)
        cybert_time = time.time() - start

        error_count = sum(1 for c in classifications if c["label"] == "error")

        print(f"  Processed {len(lines)} lines")
        print(f"  Found {error_count} ERRORs semantically in {cybert_time:.3f}s")
        print("-" * 65)
        _record_result(
            rows,
            "cybert_backend",
            cybert_time,
            f"processed={len(lines)} error_labels={error_count}",
            "PASS",
        )
        # Benchmark quality gate: for synthetic corpus, at least one error label is expected.
        if error_count == 0:
            msg = "cyBERT returned 0 error labels on corpus containing explicit ERROR lines"
            failures.append(msg)
            print(f"cyBERT quality gate failed: {msg}")
    except Exception as exc:
        if _is_skippable_cybert_exception(exc):
            print(f"cyBERT Backend skipped: {exc}")
            print("-" * 65)
            _record_result(rows, "cybert_backend", None, str(exc), "SKIP")
        else:
            print(f"cyBERT Backend failed: {exc}")
            import traceback

            traceback.print_exc()
            print("-" * 65)
            failures.append(f"cyBERT backend failed: {exc}")
            _record_result(rows, "cybert_backend", None, str(exc), "FAIL")

    # --- TorchBackend String Benchmark ---
    try:
        if not _module_available("torch"):
            raise RuntimeError("torch not installed (install benchmark deps: torch)")

        import unittest.mock as mock

        from tensor_grep.backends.torch_backend import TorchBackend
        from tensor_grep.core.config import SearchConfig

        print("Testing TorchBackend (VRAM-native Exact String Matching):")
        with (
            mock.patch.object(TorchBackend, "is_available", return_value=True),
            mock.patch("torch.device", return_value="cpu"),
        ):
            torch_backend = TorchBackend()
            cfg = SearchConfig(fixed_strings=True)

            start = time.time()
            res = torch_backend.search(str(log_file), "Database connection timeout", cfg)
            torch_time = time.time() - start

            print("  Query: 'Database connection timeout'")
            print(f"  Found {res.total_matches} matches across 10k lines in {torch_time:.3f}s")
            print("-" * 65)
            _record_result(
                rows,
                "torch_backend",
                torch_time,
                f"query=Database connection timeout matches={res.total_matches}",
                "PASS",
            )
    except Exception as exc:
        print(f"Torch Backend failed: {exc}")
        import traceback

        traceback.print_exc()
        print("-" * 65)
        failures.append(f"Torch backend failed: {exc}")
        _record_result(rows, "torch_backend", None, str(exc), "FAIL")

    artifacts_dir = ensure_artifacts_dir(root_dir)
    write_json(
        artifacts_dir / "bench_run_gpu_benchmarks.json",
        {
            "suite": "run_gpu_benchmarks",
            "generated_at_epoch_s": time.time(),
            "environment": {
                "platform": platform.system().lower(),
                "machine": platform.machine().lower(),
                "python_version": platform.python_version(),
            },
            "rows": rows,
            "failures": failures,
        },
    )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
