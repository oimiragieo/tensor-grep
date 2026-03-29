from __future__ import annotations

import argparse
import contextlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from patch_runner_common import derive_patch_from_repo_changes  # noqa: E402

from tensor_grep.perf_guard import write_json  # noqa: E402

DEFAULT_SKILL_DIR = ROOT_DIR / ".claude" / "skills" / "tensor-grep"
DEFAULT_WORK_ROOT = Path(tempfile.gettempdir()) / "tensor_grep_claude_ab"


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "claude_skill_ab.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Claude baseline vs Claude + tensor-grep skill on the same task."
    )
    parser.add_argument("--input", required=True, help="Path to tensor-grep patch driver JSON.")
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--model", default="")
    parser.add_argument("--permission-mode", default="bypassPermissions")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--skill-dir", default=str(DEFAULT_SKILL_DIR))
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    return parser.parse_args()


def resolve_claude_binary() -> str:
    binary = shutil.which("claude")
    if binary:
        return binary
    raise FileNotFoundError("claude binary not found on PATH")


def load_driver_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("driver payload missing records list")
    return payload


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    with contextlib.suppress(Exception):
        if platform.system().lower().startswith("win"):
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


def _ephemeral_repo_instructions(repo_root: Path) -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def _manager() -> Any:
        instructions_path = repo_root / "AGENTS.md"
        if instructions_path.exists():
            yield
            return
        instructions_path.write_text(
            "\n".join(
                [
                    "# Evaluation Instructions",
                    "",
                    "You are running inside an automated patch evaluation harness.",
                    "Analyze this repository directly.",
                    "Return only a unified diff patch that can be applied with git apply.",
                    "Do not include markdown fences or explanations.",
                    "Do not ask clarifying questions or ask for confirmation in print mode.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            yield
        finally:
            instructions_path.unlink(missing_ok=True)

    return _manager()


def _build_claude_prompt(prompt: str) -> str:
    prefix = (
        "If file-editing tools are available, edit the repository files directly instead of printing a patch. "
        "If you edit files directly, do not print a summary or any extra text."
    )
    return f"{prefix}\n\n{prompt}".strip()


def build_system_prompt(prompt: str, *, use_skill: bool) -> str:
    rendered = _build_claude_prompt(prompt)
    if not use_skill:
        return rendered
    skill_prefix = (
        "Use the tensor-grep project skill and follow its workflow for this task. "
        "Use tg against the current repository before editing when it helps target the right file or span."
    )
    return f"{skill_prefix}\n\n{rendered}".strip()


def rewrite_prompt_repo_paths(prompt: str, source_repo: Path, repo_root: Path) -> str:
    source_repo_str = str(source_repo.resolve())
    repo_root_str = str(repo_root.resolve())
    rewritten = prompt.replace(source_repo_str, repo_root_str)
    rewritten = rewritten.replace(source_repo_str.replace("\\", "/"), repo_root_str.replace("\\", "/"))
    return rewritten


def install_skill_package(repo_root: Path, skill_dir: Path) -> Path:
    enhanced_skill_dir = repo_root / ".claude" / "skills" / "tensor-grep"
    enhanced_skill_dir.mkdir(parents=True, exist_ok=True)
    for file_name in ("SKILL.md", "REFERENCE.md"):
        shutil.copy2(skill_dir / file_name, enhanced_skill_dir / file_name)
    return enhanced_skill_dir


def write_claude_md(repo_root: Path) -> Path:
    guidance_path = repo_root / "CLAUDE.md"
    guidance_path.write_text(
        "\n".join(
            [
                "# Claude Instructions",
                "",
                "Use the tensor-grep project skill for repository search, symbol lookup, blast-radius planning, and edit targeting.",
                "When the tensor-grep skill is available, use it before editing if it will help identify the right file or span.",
                "In non-interactive mode, do not ask for confirmation; make the change directly.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return guidance_path


def prepare_persistent_repo_copy(
    source_repo: Path,
    work_root: Path,
    instance_id: str,
    system_name: str,
) -> tuple[Path, Path]:
    run_root = work_root / instance_id / system_name
    before_root = run_root / "a"
    repo_root = run_root / "b"
    if run_root.exists():
        shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_repo, before_root)
    shutil.copytree(source_repo, repo_root)
    return before_root, repo_root


def _run_claude_command(
    repo_root: Path,
    prompt: str,
    *,
    model: str,
    permission_mode: str,
    timeout_seconds: int,
) -> str:
    command = [
        resolve_claude_binary(),
        "-p",
    ]
    if model:
        command.extend(["--model", model])
    if permission_mode == "bypassPermissions":
        command.append("--dangerously-skip-permissions")
    else:
        command.extend(["--permission-mode", permission_mode])
    command.extend(["--add-dir", str(repo_root), "--", prompt])
    popen_kwargs: dict[str, Any] = {
        "cwd": str(repo_root),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if platform.system().lower().startswith("win"):
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(proc)
        raise subprocess.TimeoutExpired(command, timeout_seconds, output=exc.output, stderr=exc.stderr) from None
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, command, output=stdout, stderr=stderr)
    return stdout


def run_ab_record(
    record: dict[str, Any],
    *,
    model: str,
    permission_mode: str,
    timeout_seconds: int,
    skill_dir: Path,
    work_root: Path,
) -> list[dict[str, Any]]:
    source_repo = Path(str(record["repo_fixture"])).resolve()
    systems: list[tuple[str, bool]] = [("claude-baseline", False), ("claude-enhanced", True)]
    rows: list[dict[str, Any]] = []
    for system_name, use_skill in systems:
        started = time.perf_counter()
        notes = ""
        before_root, repo_root = prepare_persistent_repo_copy(
            source_repo,
            work_root,
            str(record["instance_id"]),
            system_name,
        )
        prompt = build_system_prompt(
            rewrite_prompt_repo_paths(str(record["prompt"]), source_repo, repo_root)
            ,
            use_skill=use_skill,
        )
        if use_skill:
            install_skill_package(repo_root, skill_dir)
            write_claude_md(repo_root)
        try:
            with _ephemeral_repo_instructions(repo_root):
                stdout = _run_claude_command(
                    repo_root,
                    prompt,
                    model=model,
                    permission_mode=permission_mode,
                    timeout_seconds=timeout_seconds,
                )
            notes = stdout.strip()
        except subprocess.TimeoutExpired:
            notes = f"timeout after {timeout_seconds}s"
        except subprocess.CalledProcessError as exc:
            notes = (exc.stderr or exc.output or str(exc)).strip()
        patch_text = derive_patch_from_repo_changes(before_root, repo_root)
        wall_clock_seconds = round(time.perf_counter() - started, 6)
        rows.append(
            {
                "instance_id": str(record["instance_id"]),
                "system": system_name,
                "model_patch": patch_text,
                "actual_test_files": list(record.get("actual_test_files", [])),
                "actual_validation_commands": list(record.get("actual_validation_commands", [])),
                "wall_clock_seconds": wall_clock_seconds,
                "notes": notes,
            }
        )
    return rows


def build_payload(
    driver_payload: dict[str, Any],
    *,
    model: str,
    permission_mode: str,
    timeout_seconds: int,
    skill_dir: Path,
    work_root: Path,
    limit: int = 0,
) -> dict[str, Any]:
    records = list(driver_payload.get("records", []))
    if limit > 0:
        records = records[:limit]
    prediction_records: list[dict[str, Any]] = []
    work_root.mkdir(parents=True, exist_ok=True)
    for record in records:
        prediction_records.extend(
            run_ab_record(
                dict(record),
                model=model,
                permission_mode=permission_mode,
                timeout_seconds=timeout_seconds,
                skill_dir=skill_dir,
                work_root=work_root,
            )
        )
    return {
        "artifact": "claude_skill_ab",
        "suite": "run_claude_skill_ab",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "records": prediction_records,
    }


def main() -> int:
    args = parse_args()
    driver_payload = load_driver_payload(args.input)
    payload = build_payload(
        driver_payload,
        model=args.model,
        permission_mode=args.permission_mode,
        timeout_seconds=args.timeout_seconds,
        skill_dir=Path(args.skill_dir).expanduser().resolve(),
        work_root=Path(args.work_root).expanduser().resolve(),
        limit=args.limit,
    )
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, payload)
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
