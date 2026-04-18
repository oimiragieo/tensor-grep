import subprocess
import sys
from pathlib import Path


def test_perf_guard_should_import_from_current_worktree_src():
    repo_root = Path(__file__).resolve().parents[2]

    from tensor_grep import perf_guard

    assert Path(perf_guard.__file__).resolve().is_relative_to(repo_root / "src")


def test_python_subprocess_should_import_from_current_worktree_src(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [sys.executable, "-c", "import tensor_grep; print(tensor_grep.__file__)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )

    assert Path(result.stdout.strip()).resolve().is_relative_to(repo_root / "src")
