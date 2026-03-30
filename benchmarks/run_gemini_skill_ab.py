from __future__ import annotations

import argparse
import json
import platform
import shutil
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

import run_gemini_patch_predictions as gemini_runner  # noqa: E402
from patch_runner_common import derive_patch_from_repo_changes, isolated_repo_pair  # noqa: E402

from tensor_grep.perf_guard import write_json  # noqa: E402

DEFAULT_SKILL_DIR = ROOT_DIR / ".gemini" / "skills" / "tensor-grep"
DEFAULT_CONTEXT_PATH = ROOT_DIR / "GEMINI.md"
DEFAULT_WORK_ROOT = Path(tempfile.gettempdir()) / "tensor_grep_gemini_ab"


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "gemini_skill_ab.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Gemini baseline vs Gemini + tensor-grep GEMINI.md/skill on the same task."
    )
    parser.add_argument("--input", required=True, help="Path to tensor-grep patch driver JSON.")
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--model", default="gemini-3-flash-preview")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--skill-dir", default=str(DEFAULT_SKILL_DIR))
    parser.add_argument("--context-path", default=str(DEFAULT_CONTEXT_PATH))
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--resume", action="store_true", help="Resume from an existing A/B output artifact.")
    return parser.parse_args()


def rewrite_prompt_repo_paths(prompt: str, source_repo: Path, repo_root: Path) -> str:
    source_repo_str = str(source_repo.resolve())
    repo_root_str = str(repo_root.resolve())
    rewritten = prompt.replace(source_repo_str, repo_root_str)
    rewritten = rewritten.replace(source_repo_str.replace("\\", "/"), repo_root_str.replace("\\", "/"))
    return rewritten


def install_skill_package(repo_root: Path, skill_dir: Path, context_path: Path) -> None:
    destination_skill_dir = repo_root / ".gemini" / "skills" / "tensor-grep"
    destination_skill_dir.mkdir(parents=True, exist_ok=True)
    for file_name in ("SKILL.md", "REFERENCE.md"):
        shutil.copy2(skill_dir / file_name, destination_skill_dir / file_name)
    shutil.copy2(context_path, repo_root / "GEMINI.md")


def _run_variant(
    record: dict[str, Any],
    *,
    model: str,
    timeout_seconds: int,
    use_skill: bool,
    skill_dir: Path,
    context_path: Path,
) -> dict[str, Any]:
    repo_root = Path(str(record["repo_fixture"])).resolve()
    prompt = str(record["prompt"])
    started = time.perf_counter()
    notes = ""
    patch_text = ""
    with isolated_repo_pair(repo_root) as (before_root, work_root):
        if use_skill:
            install_skill_package(work_root, skill_dir, context_path)
        rewritten_prompt = rewrite_prompt_repo_paths(prompt, repo_root, work_root)
        try:
            with gemini_runner._ephemeral_repo_instructions(work_root):
                stdout = gemini_runner._run_gemini_command(
                    work_root,
                    rewritten_prompt,
                    model=model,
                    timeout_seconds=timeout_seconds,
                )
            patch_text = gemini_runner.normalize_model_patch_text(gemini_runner._extract_response_text(stdout))
            if not gemini_runner.is_probably_patch_text(patch_text):
                patch_text = ""
        except gemini_runner.subprocess.TimeoutExpired:
            notes = f"timeout after {timeout_seconds}s"
        except gemini_runner.subprocess.CalledProcessError as exc:
            notes = (exc.stderr or exc.stdout or str(exc)).strip()
        except ValueError as exc:
            notes = str(exc)
        if not patch_text.strip():
            patch_text = derive_patch_from_repo_changes(before_root, work_root)
        changed_files = sorted(
            str(path.relative_to(work_root)).replace("\\", "/")
            for path in work_root.rglob("*")
            if path.is_file()
            and path.relative_to(work_root).parts[:1] != (".gemini-home",)
            and before_root.joinpath(path.relative_to(work_root)).exists()
            and path.read_bytes() != before_root.joinpath(path.relative_to(work_root)).read_bytes()
        )
    wall_clock_seconds = round(time.perf_counter() - started, 6)
    return {
        "instance_id": str(record["instance_id"]),
        "system": "gemini-enhanced" if use_skill else "gemini-baseline",
        "model_patch": patch_text,
        "actual_test_files": list(record.get("actual_test_files", [])),
        "actual_validation_commands": list(record.get("actual_validation_commands", [])),
        "wall_clock_seconds": wall_clock_seconds,
        "notes": notes,
        "use_skill": use_skill,
        "changed_file_count": len(changed_files),
        "changed_files": changed_files,
    }


def run_ab_record(
    record: dict[str, Any],
    *,
    model: str,
    timeout_seconds: int,
    skill_dir: Path,
    context_path: Path,
) -> list[dict[str, Any]]:
    return [
        _run_variant(
            dict(record),
            model=model,
            timeout_seconds=timeout_seconds,
            use_skill=False,
            skill_dir=skill_dir,
            context_path=context_path,
        ),
        _run_variant(
            dict(record),
            model=model,
            timeout_seconds=timeout_seconds,
            use_skill=True,
            skill_dir=skill_dir,
            context_path=context_path,
        ),
    ]


def build_partial_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "artifact": "gemini_skill_ab",
        "suite": "run_gemini_skill_ab",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "records": records,
    }


def load_existing_payload(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [dict(record) for record in list(payload.get("records", [])) if isinstance(record, dict)]


def write_checkpoint(output_path: Path, records: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, build_partial_payload(records))


def build_payload(
    driver_payload: dict[str, Any],
    *,
    model: str,
    timeout_seconds: int,
    skill_dir: Path,
    context_path: Path,
    work_root: Path,
    limit: int = 0,
    output_path: Path | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    del work_root
    records = list(driver_payload.get("records", []))
    if limit > 0:
        records = records[:limit]
    prediction_records: list[dict[str, Any]] = []
    if resume and output_path is not None:
        prediction_records = load_existing_payload(output_path)
    completed_instance_ids = {str(record.get("instance_id", "")) for record in prediction_records if record.get("use_skill") is True}
    for record in records:
        instance_id = str(record["instance_id"])
        if instance_id in completed_instance_ids:
            continue
        prediction_records.extend(
            run_ab_record(
                dict(record),
                model=model,
                timeout_seconds=timeout_seconds,
                skill_dir=skill_dir,
                context_path=context_path,
            )
        )
        completed_instance_ids.add(instance_id)
        if output_path is not None:
            write_checkpoint(output_path, prediction_records)
    return build_partial_payload(prediction_records)


def main() -> int:
    args = parse_args()
    driver_payload = gemini_runner.load_driver_payload(args.input)
    output_path = Path(args.output).expanduser().resolve()
    payload = build_payload(
        driver_payload,
        model=args.model,
        timeout_seconds=args.timeout_seconds,
        skill_dir=Path(args.skill_dir).expanduser().resolve(),
        context_path=Path(args.context_path).expanduser().resolve(),
        work_root=Path(args.work_root).expanduser().resolve(),
        limit=args.limit,
        output_path=output_path,
        resume=args.resume,
    )
    write_json(output_path, payload)
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
