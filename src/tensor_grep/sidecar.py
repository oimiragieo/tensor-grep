from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import traceback
from collections.abc import Sequence
from typing import Any


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read()
    if not raw:
        raise ValueError("No JSON request received on stdin")
    request = json.loads(raw.decode("utf-8"))
    if not isinstance(request, dict):
        raise ValueError("Sidecar request must be a JSON object")
    return request


def _extract_exit_code(exc: SystemExit) -> int:
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def _classify_lines(lines: list[str]) -> list[dict[str, Any]]:
    from tensor_grep.backends.cybert_backend import CybertBackend

    backend = CybertBackend()
    try:
        return backend.classify(lines)
    except Exception:
        results: list[dict[str, Any]] = []
        for line in lines:
            if re.search(r"\berror\b|\bfail(?:ed)?\b|\bexception\b", line, re.IGNORECASE):
                results.append({"label": "error", "confidence": 0.9})
            elif re.search(r"\bwarn(?:ing)?\b", line, re.IGNORECASE):
                results.append({"label": "warn", "confidence": 0.8})
            else:
                results.append({"label": "info", "confidence": 0.7})
        return results


def _classify_payload(args: Sequence[str], payload: dict[str, Any] | None) -> tuple[str, str, int]:
    format_type = "json"
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--format" and index + 1 < len(args):
            format_type = str(args[index + 1])
            index += 2
            continue
        if arg.startswith("--format="):
            format_type = arg.split("=", 1)[1]
            index += 1
            continue
        index += 1

    content = None
    if payload is not None:
        content = payload.get("content")

    if not isinstance(content, str):
        return _dispatch_cli("classify", list(args))

    lines = content.splitlines(keepends=True)
    if content and not lines:
        lines = [content]
    if not lines:
        return "", "", 1

    results = _classify_lines(lines)
    if format_type == "json":
        return json.dumps({"classifications": results}) + "\n", "", 0

    rendered = "".join(f"{item['label']} ({item['confidence']:.2f})\n" for item in results)
    return rendered, "", 0


def _dispatch_cli(command: str, args: list[str]) -> tuple[str, str, int]:
    from tensor_grep.cli.bootstrap import main_entry

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    original_argv = sys.argv[:]
    exit_code = 0

    try:
        sys.argv = ["tg", command, *args]
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            try:
                main_entry()
            except SystemExit as exc:
                exit_code = _extract_exit_code(exc)
    finally:
        sys.argv = original_argv

    return stdout_buffer.getvalue(), stderr_buffer.getvalue(), exit_code


def _dispatch_request(request: dict[str, Any]) -> tuple[str, str, int]:
    command = request.get("command")
    args = request.get("args") or []
    payload = request.get("payload")

    if not isinstance(command, str) or not isinstance(args, list):
        return "", "Invalid sidecar request: expected string command and list args\n", 1

    if command == "classify":
        payload_dict = payload if isinstance(payload, dict) else None
        return _classify_payload([str(arg) for arg in args], payload_dict)

    if command in {"run", "scan", "test", "new"}:
        return _dispatch_cli(command, [str(arg) for arg in args])

    return "", f"Unsupported sidecar command: {command}\n", 2


def _console_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    if os.linesep != "\n":
        normalized = normalized.replace("\n", os.linesep)
    return normalized


def main() -> int:
    pid = os.getpid()
    try:
        request = _read_request()
        stdout_text, stderr_text, exit_code = _dispatch_request(request)
    except Exception:
        stdout_text = ""
        stderr_text = traceback.format_exc()
        exit_code = 1

    response = {
        "stdout": _console_text(stdout_text),
        "stderr": _console_text(stderr_text),
        "exit_code": int(exit_code),
        "pid": pid,
    }
    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
