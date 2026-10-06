from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store, diff_impact


@pytest.mark.skipif(os.name == "nt", reason="POSIX paths may contain trailing whitespace and LF")
@pytest.mark.parametrize("consumer", ["checkpoint", "diff-impact"])
def test_git_root_transport_removes_only_the_protocol_lf(
    consumer: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repo with trailing space and newline \n"
    root.mkdir()
    output = os.fsencode(root) + b"\n"
    completed = subprocess.CompletedProcess(["git"], 0, output, b"")

    if consumer == "checkpoint":
        monkeypatch.setattr(checkpoint_store, "run_subprocess", lambda *_a, **_k: completed)
        scope = checkpoint_store._detect_checkpoint_scope(root)
        assert scope.root == root
        assert scope.mode == "git-worktree-snapshot"
        return

    monkeypatch.setattr(diff_impact, "run_subprocess", lambda *_a, **_k: completed)
    monkeypatch.setattr(diff_impact, "_git_env", lambda: {})
    assert diff_impact._git_toplevel(root) == root
