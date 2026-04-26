from pathlib import Path

content = Path("src/tensor_grep/cli/ast_workflows.py").read_text()
print(f"RUN_START: {content.find('def run_command(')}")
print(f"RUN_END: {content.find('def _get_cached_backend(')}")
print(f"SCAN_START: {content.find('def scan_command(')}")
print(f"SCAN_END: {content.find('def test_command(')}")
print(f"MAIN_START: {content.find('def main_entry(')}")
