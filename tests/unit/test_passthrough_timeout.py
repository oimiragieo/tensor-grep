"""A streaming-passthrough timeout must exit cleanly (124), not raise (audit B5/#10).

``run_subprocess`` always imposes a timeout (default 600s, env-lowerable), but the
interactive passthrough delegators previously had no handler, so ``TimeoutExpired``
propagated as an uncaught traceback and SIGKILLed the child mid-stream.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.cli import bootstrap


def _timeout(*_args, **_kwargs):
    raise subprocess.TimeoutExpired(cmd="rg", timeout=600)


def test_bootstrap_rg_passthrough_timeout_returns_124(capsys) -> None:
    with patch.object(bootstrap, "run_subprocess", side_effect=_timeout):
        rc = bootstrap._run_rg_passthrough("rg", ["ERROR", "."])
    assert rc == 124
    assert "timeout" in capsys.readouterr().err.lower()


def test_bootstrap_native_search_timeout_returns_124() -> None:
    with patch.object(bootstrap, "run_subprocess", side_effect=_timeout):
        assert bootstrap._run_native_tg_search("tg", ["ERROR", "."]) == 124
        assert bootstrap._run_native_tg_command("tg", ["search", "ERROR"]) == 124


def test_ripgrep_backend_passthrough_timeout_returns_124() -> None:
    backend = RipgrepBackend()
    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.run_subprocess",
            side_effect=_timeout,
        ),
    ):
        rc = backend.search_passthrough(".", "ERROR")
    assert rc == 124


def test_bootstrap_passthrough_returns_real_code_when_no_timeout() -> None:
    class _Result:
        returncode = 1

    with patch.object(bootstrap, "run_subprocess", return_value=_Result()):
        assert bootstrap._run_rg_passthrough("rg", ["ERROR", "."]) == 1
