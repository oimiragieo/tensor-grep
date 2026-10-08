from __future__ import annotations

import os
import sys

import pytest

from tensor_grep.cli import freshness_process, native_frontdoor


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
def test_pip_index_version_streams_are_strict(
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes | None,
    stderr: bytes | None,
    expected: list[str],
) -> None:
    calls: list[list[str]] = []

    def capture(
        command: list[str], *, timeout_seconds: float, env: dict[str, str]
    ) -> tuple[bytes | None, bytes | None]:
        calls.append(command)
        assert command == [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            "-m",
            "pip",
            "index",
            "versions",
            "tensor-grep",
            "--no-cache-dir",
            "--index-url",
            "https://pypi.org/simple",
        ]
        assert timeout_seconds == 2.5
        assert env["PIP_DISABLE_PIP_VERSION_CHECK"] == os.environ.get(
            "PIP_DISABLE_PIP_VERSION_CHECK", "1"
        )
        return stdout, stderr

    monkeypatch.setattr(native_frontdoor, "_capture_freshness_probe", capture)
    assert native_frontdoor._candidate_versions_from_pip_index(2.5) == expected
    assert len(calls) == 1


@pytest.mark.parametrize("returncode", [0, 7])
def test_pip_index_keeps_completed_output_from_contained_child(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    calls: list[list[str]] = []

    def capture(
        command: list[str], *, timeout_seconds: float, env: dict[str, str]
    ) -> tuple[bytes, bytes]:
        calls.append(command)
        assert command[1:8] == ["-I", "-X", "utf8", "-m", "pip", "index", "versions"]
        assert timeout_seconds == 5
        return freshness_process.capture_probe(
            [
                sys.executable,
                "-c",
                "import sys; print('tensor-grep (1.2.3)', flush=True); "
                "print('warning', file=sys.stderr, flush=True); "
                f"sys.exit({returncode})",
            ],
            timeout_seconds=timeout_seconds,
            env=env,
        )

    monkeypatch.setattr(native_frontdoor, "_capture_freshness_probe", capture)
    assert native_frontdoor._candidate_versions_from_pip_index(5) == ["1.2.3"]
    assert len(calls) == 1
