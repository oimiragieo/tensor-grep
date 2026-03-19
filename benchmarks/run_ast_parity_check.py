from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BENCHMARKS_DIR = Path(__file__).resolve().parent
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

from gen_corpus import AST_PARITY_CASES, generate_ast_parity_corpus  # noqa: E402

PARITY_CASES = AST_PARITY_CASES
TG_MATCH_RE = re.compile(r"^(?P<file>.+):(?P<line>\d+):(?P<text>.*)$")


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "ast_parity_report.json"


def resolve_ast_parity_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_AST_PARITY_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "ast_parity_corpus"


def ensure_ast_parity_corpus(output_dir: Path) -> dict[str, object]:
    return generate_ast_parity_corpus(output_dir)


def resolve_tg_binary(binary: str | None = None) -> Path:
    return Path(binary).expanduser().resolve() if binary else default_binary_path()


def resolve_ast_grep_binary() -> Path | None:
    env_override = os.environ.get("AST_GREP_BINARY")
    if env_override:
        candidate = Path(env_override).expanduser().resolve()
        if candidate.exists():
            return candidate
        return None

    for candidate in ("sg", "sg.exe", "ast-grep", "ast-grep.exe"):
        if found := shutil.which(candidate):
            return Path(found)

    for local_name in ("sg.exe", "sg.cmd", "ast-grep.exe", "ast-grep.cmd"):
        local = BENCHMARKS_DIR / local_name
        if local.exists():
            return local

    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare tg.exe native AST matches against native sg across 40 parity cases."
    )
    parser.add_argument("--binary", default=str(default_binary_path()), help="Path to tg.exe.")
    parser.add_argument(
        "--output",
        default=str(default_output_path()),
        help="Machine-readable parity report path.",
    )
    return parser.parse_args()


def _relative_to_corpus(file_path: Path, corpus_dir: Path) -> str:
    try:
        return file_path.resolve().relative_to(corpus_dir.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix().replace("\\", "/")


def parse_tg_matches(stdout: str, corpus_dir: Path) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        matched = TG_MATCH_RE.match(raw_line.rstrip("\r"))
        if not matched:
            raise ValueError(f"Unable to parse tg match line: {raw_line!r}")
        file_path = Path(matched.group("file"))
        matches.append({
            "file": _relative_to_corpus(file_path, corpus_dir),
            "line": int(matched.group("line")),
            "text": matched.group("text").rstrip("\r"),
        })
    return sorted(matches, key=lambda row: (row["file"], row["line"], row["text"]))


def parse_sg_matches(stdout: str, corpus_dir: Path) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        file_path = Path(payload["file"])
        matches.append({
            "file": _relative_to_corpus(file_path, corpus_dir),
            "line": int(payload["range"]["start"]["line"]) + 1,
            "text": payload["text"].rstrip("\r\n"),
        })
    return sorted(matches, key=lambda row: (row["file"], row["line"], row["text"]))


def run_parity_case(
    case: dict[str, str],
    *,
    tg_binary: Path,
    sg_binary: Path,
    corpus_dir: Path,
) -> dict[str, object]:
    search_dir = corpus_dir / case["language"]
    tg_result = subprocess.run(
        [str(tg_binary), "run", "--lang", case["language"], case["pattern"], str(search_dir)],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    sg_result = subprocess.run(
        [
            str(sg_binary),
            "run",
            "--lang",
            case["language"],
            "--json=stream",
            "-p",
            case["pattern"],
            str(search_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )

    if tg_result.returncode != 0:
        return {
            "case_id": case["id"],
            "language": case["language"],
            "pattern": case["pattern"],
            "passed": False,
            "divergence": [f"tg exited {tg_result.returncode}: {tg_result.stderr.strip()}"],
        }
    if sg_result.returncode != 0:
        return {
            "case_id": case["id"],
            "language": case["language"],
            "pattern": case["pattern"],
            "passed": False,
            "divergence": [f"sg exited {sg_result.returncode}: {sg_result.stderr.strip()}"],
        }

    tg_matches = parse_tg_matches(tg_result.stdout, corpus_dir)
    sg_matches = parse_sg_matches(sg_result.stdout, corpus_dir)

    tg_set = {json.dumps(item, sort_keys=True) for item in tg_matches}
    sg_set = {json.dumps(item, sort_keys=True) for item in sg_matches}
    only_tg = [json.loads(item) for item in sorted(tg_set - sg_set)]
    only_sg = [json.loads(item) for item in sorted(sg_set - tg_set)]
    passed = not only_tg and not only_sg

    return {
        "case_id": case["id"],
        "language": case["language"],
        "pattern": case["pattern"],
        "passed": passed,
        "tg_count": len(tg_matches),
        "sg_count": len(sg_matches),
        "divergence": [{"only_tg": only_tg, "only_sg": only_sg}] if not passed else [],
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    corpus_info = ensure_ast_parity_corpus(resolve_ast_parity_dir())
    corpus_dir = Path(corpus_info["corpus_dir"])
    tg_binary = resolve_tg_binary(args.binary)
    sg_binary = resolve_ast_grep_binary()

    payload: dict[str, object] = {
        "suite": "ast_parity_check",
        "generated_at_epoch_s": time.time(),
        "total_cases": len(PARITY_CASES),
        "passed_cases": 0,
        "failed_cases": len(PARITY_CASES),
        "status": "FAIL",
        "corpus_dir": str(corpus_dir),
        "manifest_path": str(corpus_info["manifest_path"]),
        "cases": [],
    }

    if not tg_binary.exists():
        payload["error"] = f"tg binary not found: {tg_binary}"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(payload["error"], file=sys.stderr)
        return 1

    if sg_binary is None:
        payload["error"] = (
            "ast-grep binary not found — install via: cargo install ast-grep --version 0.41.1"
        )
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(payload["error"], file=sys.stderr)
        return 1

    cases = [
        run_parity_case(case, tg_binary=tg_binary, sg_binary=sg_binary, corpus_dir=corpus_dir)
        for case in PARITY_CASES
    ]
    passed_cases = sum(1 for case in cases if case["passed"])
    failed_cases = len(cases) - passed_cases
    payload.update({
        "tg_binary": str(tg_binary),
        "sg_binary": str(sg_binary),
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "status": "PASS" if failed_cases == 0 else "FAIL",
        "cases": cases,
    })
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"AST parity: {passed_cases}/{len(cases)} PASS")
    return 0 if failed_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
