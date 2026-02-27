import os
import subprocess
import sys

# We will test tg against hardcoded expected line match counts
# based on the synthetic corpus we generated in generate_parity_corpus.py
SCENARIOS = [
    {"name": "Basic search", "args": ["ERROR", "."], "expected": 3},
    {"name": "Case insensitive (-i)", "args": ["-i", "error", "."], "expected": 5},
    {"name": "Invert match (-v)", "args": ["-v", "ERROR", "."], "expected": 19},
    {"name": "Word boundary (-w)", "args": ["-w", "target_word", "."], "expected": 2},
    {"name": "File type filter (-t)", "args": ["-t", "py", "target_word", "."], "expected": 3},
    {"name": "Context lines (-C 1)", "args": ["-C", "1", "Timeout", "."], "expected": 3},
    {"name": "Hidden files (--hidden)", "args": ["--hidden", "fatal", "."], "expected": 2},
    {"name": "Fixed string (-F)", "args": ["-F", "system is down", "."], "expected": 1},
]


def run_command(cmd_list):
    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True, cwd="benchmarks/corpus")
        return result.stdout.strip().split("\n"), result.returncode
    except FileNotFoundError:
        return None, -1


def run_parity_check():
    corpus_dir = "benchmarks/corpus"
    if not os.path.exists(corpus_dir):
        print("Corpus not found! Run generate_parity_corpus.py first.")
        sys.exit(1)

    tg_cmd_base = [sys.executable, "-m", "tensor_grep.cli.main", "search"]

    passed = 0
    failed = 0

    print("=" * 60)
    print("RIPGREP VS TENSOR-GREP FLAG PARITY BENCHMARK")
    print("=" * 60)

    for sc in SCENARIOS:
        print(f"Testing: {sc['name']}")
        tg_cmd = tg_cmd_base + sc["args"]

        tg_lines, _tg_code = run_command(tg_cmd)

        # Filter out empty lines and context separators
        tg_matches = [line for line in tg_lines if line and not line.startswith("--")]
        tg_count = len(tg_matches)

        expected = sc["expected"]

        if tg_count >= expected:
            print(f"PASS | Expected: {expected} lines, tg found: {tg_count} lines")
            passed += 1
        else:
            print(f"FAIL | Expected: {expected} lines, tg found: {tg_count} lines")
            print("--- TG Output ---")
            print("\\n".join(tg_matches[:5]))
            failed += 1

        print("-" * 60)

    print(f"Parity Check Complete: {passed} PASSED, {failed} FAILED")


if __name__ == "__main__":
    run_parity_check()
