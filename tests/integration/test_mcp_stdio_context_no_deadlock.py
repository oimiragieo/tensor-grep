from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
_RESPONSE_TIMEOUT_SECONDS = 5.0


def _git(repo: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10.0,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr}"
        )


def _create_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "alpha.py").write_text(
        "def compute_score(value: int) -> int:\n    return value + 1\n",
        encoding="utf-8",
    )
    (repo / "beta.py").write_text(
        "from alpha import compute_score\n\n"
        "def run_pipeline(items: list[int]) -> list[int]:\n"
        "    return [compute_score(x) for x in items]\n",
        encoding="utf-8",
    )
    (repo / "test_alpha.py").write_text(
        "from alpha import compute_score\n\n"
        "def test_compute_score() -> None:\n"
        "    assert compute_score(1) == 2\n",
        encoding="utf-8",
    )
    _git(repo, "add", "alpha.py", "beta.py", "test_alpha.py")
    _git(repo, "commit", "-m", "initial commit")


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        if os.name == "nt" and shutil.which("taskkill"):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=5.0,
                check=False,
            )
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            pass
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


@pytest.mark.integration
def test_mcp_stdio_tg_context_actions_no_deadlock_in_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "git_repo"
    _create_git_repo(repo)

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{_SRC_ROOT}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(_SRC_ROOT)
    )
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "tensor_grep", "mcp"],
        cwd=str(repo),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    stdout_queue: queue.Queue[dict[str, object] | BaseException | None] = queue.Queue()
    stderr_lines: list[str] = []

    def _read_stdout() -> None:
        assert proc.stdout is not None
        try:
            for raw_line in iter(proc.stdout.readline, b""):
                text = raw_line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    stdout_queue.put(json.loads(text))
                except json.JSONDecodeError as exc:
                    stdout_queue.put(
                        AssertionError(f"Non-JSON stdout line from MCP server: {text!r} ({exc})")
                    )
                    return
        except BaseException as exc:  # pragma: no cover - defensive reader guard
            stdout_queue.put(exc)
        finally:
            stdout_queue.put(None)

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        try:
            for raw_line in iter(proc.stderr.readline, b""):
                stderr_lines.append(raw_line.decode("utf-8", errors="replace"))
        except OSError:
            pass

    stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    def _send(payload: dict[str, object]) -> None:
        assert proc.stdin is not None
        encoded = (json.dumps(payload) + "\n").encode("utf-8")
        proc.stdin.write(encoded)
        proc.stdin.flush()

    def _recv_for_id(expected_id: int) -> dict[str, object]:
        deadline = time.monotonic() + _RESPONSE_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting >{_RESPONSE_TIMEOUT_SECONDS}s for MCP response id={expected_id}. "
                    f"stderr={''.join(stderr_lines)!r}"
                )
            try:
                item = stdout_queue.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"Timed out waiting >{_RESPONSE_TIMEOUT_SECONDS}s for MCP response id={expected_id}. "
                    f"stderr={''.join(stderr_lines)!r}"
                ) from exc
            if item is None:
                raise AssertionError(
                    f"MCP server closed stdout before replying to id={expected_id}. "
                    f"returncode={proc.poll()} stderr={''.join(stderr_lines)!r}"
                )
            if isinstance(item, BaseException):
                raise item
            if item.get("id") == expected_id:
                return item

    try:
        _send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest-stdio-client", "version": "1.0.0"},
            },
        })
        init_resp = _recv_for_id(1)
        assert "result" in init_resp, f"Unexpected initialize response: {init_resp}"

        _send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })

        for req_id, action in enumerate(("pack", "edit_plan", "render", "capsule"), start=2):
            started = time.monotonic()
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {
                    "name": "tg_context",
                    "arguments": {
                        "action": action,
                        "query": "compute_score",
                        "path": str(repo),
                    },
                },
            })
            resp = _recv_for_id(req_id)
            elapsed = time.monotonic() - started
            assert elapsed < _RESPONSE_TIMEOUT_SECONDS, (
                f"tg_context(action={action!r}) took {elapsed:.2f}s"
            )
            assert "result" in resp, f"Missing result for action={action!r}: {resp}"
            result = resp["result"]
            assert isinstance(result, dict), (
                f"Unexpected result type for action={action!r}: {result}"
            )
            assert result.get("isError") is False, (
                f"tg_context(action={action!r}) returned isError={result.get('isError')!r}: {result}"
            )
            content = result.get("content")
            assert isinstance(content, list) and content, (
                f"Empty content for action={action!r}: {result}"
            )
            first_text = content[0].get("text")
            assert isinstance(first_text, str) and first_text, (
                f"Missing text payload for action={action!r}: {content}"
            )
            parsed = json.loads(first_text)
            assert isinstance(parsed, dict), (
                f"Expected JSON object payload for action={action!r}, got {type(parsed).__name__}"
            )
    finally:
        _terminate_process_tree(proc)
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)
