from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_log_path() -> Path:
    return _repo_root() / "artifacts" / "pytest_full.log"


def default_report_path() -> Path:
    return _repo_root() / "artifacts" / "pytest_full_report.json"


def build_pytest_command(*, timeout_s: int, extra_args: list[str] | None = None) -> list[str]:
    command = [
        "uv",
        "run",
        "pytest",
        "-q",
        "--capture=tee-sys",
        "-o",
        "console_output_style=classic",
        "-o",
        f"faulthandler_timeout={timeout_s}",
        "-o",
        "faulthandler_exit_on_timeout=true",
    ]
    if extra_args:
        command.extend(extra_args)
    return command


def _write_console_line(line: str) -> None:
    try:
        sys.stdout.write(line)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        if buffer is not None:
            buffer.write(line.encode(encoding, errors="backslashreplace"))
        else:
            fallback = line.encode(encoding, errors="backslashreplace").decode(
                encoding, errors="ignore"
            )
            sys.stdout.write(fallback)
    sys.stdout.flush()


def run_pytest_command(
    command: list[str],
    *,
    log_path: Path,
    report_path: Path,
    cwd: Path | None = None,
) -> int:
    started_at = time.time()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        command,
        cwd=str(cwd or _repo_root()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    lines: list[str] = []
    with log_path.open("w", encoding="utf-8", newline="") as handle:
        if process.stdout is not None:
            for line in process.stdout:
                handle.write(line)
                handle.flush()
                lines.append(line)
                _write_console_line(line)
        exit_code = process.wait()

    report = {
        "artifact": "pytest_full_report",
        "command": command,
        "cwd": str((cwd or _repo_root()).resolve()),
        "log_path": str(log_path.resolve()),
        "exit_code": exit_code,
        "started_at_epoch_s": started_at,
        "completed_at_epoch_s": time.time(),
        "line_count": len(lines),
        "tail": lines[-20:],
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return int(exit_code)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run pytest with stable Windows-friendly defaults and log/report artifacts."
    )
    parser.add_argument(
        "--log", default=str(default_log_path()), help="Path to write the combined pytest log."
    )
    parser.add_argument(
        "--report",
        default=str(default_report_path()),
        help="Path to write the machine-readable pytest run report.",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=120,
        help="Value forwarded to pytest's faulthandler_timeout option.",
    )
    parser.add_argument(
        "pytest_args", nargs=argparse.REMAINDER, help="Extra arguments passed through to pytest."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    extra_args = list(args.pytest_args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    command = build_pytest_command(timeout_s=max(1, int(args.timeout_s)), extra_args=extra_args)
    return run_pytest_command(
        command,
        log_path=Path(str(args.log)),
        report_path=Path(str(args.report)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
