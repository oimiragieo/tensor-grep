from __future__ import annotations

import subprocess

import pytest

from tensor_grep.cli import main, native_frontdoor


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        (b"tensor-grep (1.2.3)\n", b"warning\n", ["1.2.3"]),
        (b"", b"tensor-grep (1.2.3)\n", ["1.2.3"]),
        (b"tensor-grep (1.2\xff.3)\n", b"", []),
        (b"Available versions: 1.2\xff.3\n", b"", []),
        (b"tensor-grep (1.2.3)\n", b"warning\xff", []),
        (None, None, []),
    ],
)
@pytest.mark.parametrize("returncode", [0, 7])
def test_pip_index_version_streams_are_strict(
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes | None,
    stderr: bytes | None,
    expected: list[str],
    returncode: int,
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["text"] is False
        assert "encoding" not in kwargs and "errors" not in kwargs
        assert kwargs["timeout"] == 2.5
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(main.subprocess, "run", run)
    assert native_frontdoor._candidate_versions_from_pip_index(2.5) == expected
