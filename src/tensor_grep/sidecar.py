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


def _format_gpu_device_ids(device_ids: Sequence[int]) -> str:
    return "[" + ", ".join(str(device_id) for device_id in device_ids) + "]"


def _detect_available_gpu_device_ids() -> list[int]:
    from tensor_grep.core.hardware.device_detect import DeviceDetector

    detector = DeviceDetector()
    if hasattr(detector, "enumerate_device_ids"):
        return [int(device_id) for device_id in detector.enumerate_device_ids()]
    if hasattr(detector, "get_device_ids"):
        return [int(device_id) for device_id in detector.get_device_ids()]
    return []


def _gpu_device_validation_error(
    *,
    requested_ids: Sequence[int],
    available_ids: Sequence[int],
) -> str:
    requested_text = _format_gpu_device_ids(requested_ids)
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return (
            "CUDA is unavailable for requested GPU device IDs "
            f"{requested_text}: CUDA_VISIBLE_DEVICES is empty, so no GPUs are visible to the sidecar.\n"
        )

    available_text = (
        _format_gpu_device_ids(available_ids) if available_ids else "none"
    )
    return (
        f"Requested GPU device IDs {requested_text} are not available to the sidecar. "
        f"Available device IDs: {available_text}.\n"
    )


def _gpu_import_error(requested_ids: Sequence[int], exc: ImportError) -> str:
    requested_text = _format_gpu_device_ids(requested_ids)
    message = (
        f"CUDA GPU backends could not be imported for requested GPU device IDs {requested_text}: "
        f"{exc}."
    )
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        message += " CUDA_VISIBLE_DEVICES is empty, so no GPUs are visible to the sidecar."
    return message + "\n"


def _gpu_runtime_error(requested_ids: Sequence[int], exc: Exception) -> str:
    requested_text = _format_gpu_device_ids(requested_ids)
    return f"GPU search failed for requested GPU device IDs {requested_text}: {exc}\n"


def _gpu_search(payload: dict[str, Any]) -> tuple[str, str, int]:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.pipeline import ConfigurationError, Pipeline
    from tensor_grep.core.result import SearchResult
    from tensor_grep.io.directory_scanner import DirectoryScanner

    pattern = payload.get("pattern", "")
    raw_patterns = payload.get("patterns")
    if isinstance(raw_patterns, list) and raw_patterns:
        search_patterns = [str(item) for item in raw_patterns if str(item)]
    elif pattern:
        search_patterns = [str(pattern)]
    else:
        search_patterns = []
    if not search_patterns:
        return "", "gpu_search requires at least one non-empty pattern\n", 1
    path = payload.get("path", ".")
    gpu_device_ids = payload.get("gpu_device_ids")
    if not isinstance(gpu_device_ids, list) or not gpu_device_ids:
        return "", "gpu_search requires non-empty gpu_device_ids list\n", 1

    requested_gpu_device_ids: list[int] = []
    for raw_device_id in gpu_device_ids:
        try:
            device_id = int(raw_device_id)
        except (TypeError, ValueError):
            return (
                "",
                f"gpu_search received invalid GPU device ID {raw_device_id!r}; expected integers.\n",
                1,
            )
        if device_id < 0:
            return (
                "",
                f"gpu_search received invalid GPU device ID {raw_device_id!r}; device IDs must be non-negative.\n",
                1,
            )
        requested_gpu_device_ids.append(device_id)

    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return (
            "",
            _gpu_device_validation_error(
                requested_ids=requested_gpu_device_ids,
                available_ids=[],
            ),
            1,
        )

    available_gpu_device_ids = _detect_available_gpu_device_ids()
    if any(device_id not in set(available_gpu_device_ids) for device_id in requested_gpu_device_ids):
        return (
            "",
            _gpu_device_validation_error(
                requested_ids=requested_gpu_device_ids,
                available_ids=available_gpu_device_ids,
            ),
            1,
        )

    config = SearchConfig(
        ignore_case=bool(payload.get("ignore_case", False)),
        fixed_strings=bool(payload.get("fixed_strings", False)),
        invert_match=bool(payload.get("invert_match", False)),
        count=bool(payload.get("count", False)),
        context=payload.get("context"),
        max_count=payload.get("max_count"),
        word_regexp=bool(payload.get("word_regexp", False)),
        no_ignore=bool(payload.get("no_ignore", False)),
        gpu_device_ids=requested_gpu_device_ids,
        query_pattern=search_patterns[0],
    )

    try:
        pipeline = Pipeline(config=config)
        backend = pipeline.get_backend()
    except ConfigurationError as exc:
        return "", f"{exc}\n", 1

    try:
        scanner = DirectoryScanner(config)
        candidate_files = list(scanner.walk(path))

        all_results = SearchResult(matches=[], total_files=0, total_matches=0)
        all_results.routing_backend = getattr(
            pipeline, "selected_backend_name", backend.__class__.__name__
        )
        all_results.routing_reason = getattr(pipeline, "selected_backend_reason", "unknown")
        all_results.routing_gpu_device_ids = list(
            getattr(pipeline, "selected_gpu_device_ids", []) or []
        )
        include_pattern_metadata = len(search_patterns) > 1
        serialized_matches: list[dict[str, Any]] = []
        matched_files: set[str] = set()

        for current_file in candidate_files:
            for pattern_id, current_pattern in enumerate(search_patterns):
                result = backend.search(current_file, current_pattern, config=config)
                all_results.matches.extend(result.matches)
                all_results.total_matches += result.total_matches
                for fp, count in result.match_counts_by_file.items():
                    all_results.match_counts_by_file[fp] = (
                        all_results.match_counts_by_file.get(fp, 0) + count
                    )
                for match in result.matches:
                    matched_files.add(match.file)
                    serialized: dict[str, Any] = {
                        "file": match.file,
                        "line_number": match.line_number,
                        "text": match.text,
                    }
                    if include_pattern_metadata:
                        serialized["pattern_id"] = pattern_id
                        serialized["pattern_text"] = current_pattern
                    serialized_matches.append(serialized)
        all_results.total_files = len(matched_files)
    except ImportError as exc:
        return "", _gpu_import_error(requested_gpu_device_ids, exc), 1
    except Exception as exc:
        return "", _gpu_runtime_error(requested_gpu_device_ids, exc), 1

    if payload.get("json"):
        import json as json_mod

        response = {
            "total_matches": all_results.total_matches,
            "total_files": all_results.total_files,
            "routing_backend": all_results.routing_backend,
            "routing_reason": all_results.routing_reason,
            "routing_gpu_device_ids": all_results.routing_gpu_device_ids,
            "matches": serialized_matches,
        }
        return json_mod.dumps(response) + "\n", "", 0

    lines: list[str] = []
    if config.count:
        for file_path, count in all_results.match_counts_by_file.items():
            lines.append(f"{file_path}:{count}")
    else:
        seen_lines: set[tuple[str, int, str]] = set()
        for serialized_match in serialized_matches:
            key = (
                str(serialized_match["file"]),
                int(serialized_match["line_number"]),
                str(serialized_match["text"]),
            )
            if key in seen_lines:
                continue
            seen_lines.add(key)
            lines.append(f"{key[0]}:{key[1]}:{key[2]}")
    return "\n".join(lines) + "\n" if lines else "", "", 0


def _dispatch_request(request: dict[str, Any]) -> tuple[str, str, int]:
    command = request.get("command")
    args = request.get("args") or []
    payload = request.get("payload")

    if not isinstance(command, str) or not isinstance(args, list):
        return "", "Invalid sidecar request: expected string command and list args\n", 1

    if command == "classify":
        payload_dict = payload if isinstance(payload, dict) else None
        return _classify_payload([str(arg) for arg in args], payload_dict)

    if command == "gpu_search":
        if not isinstance(payload, dict):
            return "", "gpu_search requires a JSON payload\n", 1
        return _gpu_search(payload)

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
