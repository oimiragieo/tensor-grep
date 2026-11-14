"""Exercise positional scan paths through the real Python bootstrap."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _invoke(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(REPO / "src"),
        TG_DISABLE_NATIVE_TG="1",
        PYTHONIOENCODING="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-m", "tensor_grep", "scan", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_scan_text_path_preserves_scope_and_missing_path_refusal(tmp_path: Path) -> None:
    (tmp_path / "rules").mkdir()
    (tmp_path / "src" / "sub").mkdir(parents=True)
    (tmp_path / "src" / "other").mkdir()
    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
    )
    (tmp_path / "rules" / "print.yml").write_text(
        "id: print-call\nlanguage: python\nrule:\n  pattern: print($A)\n", encoding="utf-8"
    )
    (tmp_path / "src" / "sub" / "a.py").write_text("print('inside')\n", encoding="utf-8")
    (tmp_path / "src" / "other" / "b.py").write_text("print('outside')\n", encoding="utf-8")

    positive = _invoke(tmp_path, "src/sub", "--json")
    assert positive.returncode == 0, positive.stdout + positive.stderr
    assert json.loads(positive.stdout)["total_matches"] == 1

    scoped = _invoke(tmp_path, "src/sub")
    assert scoped.returncode == 0, scoped.stdout + scoped.stderr
    assert re.search(r"total_matches=1\b", scoped.stdout)

    missing = _invoke(tmp_path, "no_such_dir")
    assert missing.returncode == 2
    assert "does not exist" in (missing.stdout + missing.stderr).lower()
