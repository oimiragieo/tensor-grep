from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tensor_grep.cli.rg_contract import RGContractRow
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
WINDOWS_RG_DIRNAME = "ripgrep-14.1.0-x86_64-pc-windows-msvc"


@dataclass(frozen=True)
class RGParityCorpus:
    root: Path
    locations: Mapping[str, Path]
    follow_supported: bool

    def path_for(self, key: str) -> Path:
        return self.locations[key]

    def cli_targets(self, keys: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_cli_target(self.root, self.path_for(key)) for key in keys)


@dataclass(frozen=True)
class RGParityCase:
    row: RGContractRow
    pattern: str
    targets: tuple[str, ...]
    rg_args: tuple[str, ...]
    tg_args: tuple[str, ...]
    needs_follow: bool = False


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RGParityRun:
    case: RGParityCase
    rg: CommandResult
    tg: CommandResult


@dataclass(frozen=True)
class _ScenarioSpec:
    pattern: str
    targets: tuple[str, ...]
    rg_args: tuple[str, ...] | None = None
    needs_follow: bool = False


ROW_SCENARIOS: dict[str, _ScenarioSpec] = {
    "ignore-case": _ScenarioSpec("alphabeta sentinel", ("case-insensitive.txt",)),
    "invert-match": _ScenarioSpec("omit-me", ("invert.txt",)),
    "context": _ScenarioSpec("context sentinel", ("context.txt",)),
    "after-context": _ScenarioSpec("context sentinel", ("context.txt",)),
    "before-context": _ScenarioSpec("context sentinel", ("context.txt",)),
    "glob": _ScenarioSpec("glob sentinel", ("types",)),
    "files-with-matches": _ScenarioSpec(
        "files match sentinel",
        ("files-nested-match.txt", "files-miss.txt"),
    ),
    "files-without-match": _ScenarioSpec(
        "files match sentinel",
        ("files-match.txt", "files-nested-miss.txt"),
    ),
    "json": _ScenarioSpec("json sentinel", ("root",)),
    # ripgrep exposes only --json, so the parity comparator normalizes tg --ndjson
    # against rg --json match events.
    "ndjson": _ScenarioSpec("ndjson sentinel", ("root",), rg_args=("--json",)),
    "fixed-strings": _ScenarioSpec(r"literal.*chars", ("literal.txt",)),
    "word-regexp": _ScenarioSpec("word", ("words.txt",)),
    "max-count": _ScenarioSpec("max-count sentinel", ("max-count.txt",)),
    "type": _ScenarioSpec("type sentinel", ("types",)),
    "hidden": _ScenarioSpec("hidden sentinel", ("root",)),
    "follow": _ScenarioSpec("follow sentinel", ("links",), needs_follow=True),
    "smart-case": _ScenarioSpec("smartcase sentinel", ("smart-case.txt",)),
    "line-number": _ScenarioSpec("line number sentinel", ("line-number.txt",)),
    "column": _ScenarioSpec("column sentinel", ("column.txt",)),
    "count": _ScenarioSpec("count sentinel", ("count-a.txt", "count-b.txt")),
    "count-matches": _ScenarioSpec("count-match sentinel", ("count-matches.txt",)),
    "text": _ScenarioSpec("text sentinel", ("binary.bin",)),
}


def build_rg_parity_cases(rows: tuple[RGContractRow, ...]) -> tuple[RGParityCase, ...]:
    cases: list[RGParityCase] = []
    for row in rows:
        if row["parity_expectation"] == "unsupported":
            continue
        spec = ROW_SCENARIOS[row["id"]]
        cases.append(
            RGParityCase(
                row=row,
                pattern=spec.pattern,
                targets=spec.targets,
                rg_args=spec.rg_args or row["rg_args"],
                tg_args=row["tg_args"],
                needs_follow=spec.needs_follow,
            )
        )
    return tuple(cases)


def create_rg_parity_corpus(root: Path) -> RGParityCorpus:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    locations: dict[str, Path] = {"root": root}

    (root / ".ignore").write_text("ignored/\n*.ignored\n", encoding="utf-8")

    _write_text(
        root / "case-insensitive.txt",
        "AlphaBeta sentinel\ncontrol line\n",
    )
    locations["case-insensitive.txt"] = root / "case-insensitive.txt"

    _write_text(
        root / "invert.txt",
        "keep-one\nomit-me\nkeep-two\n",
    )
    locations["invert.txt"] = root / "invert.txt"

    _write_text(
        root / "context.txt",
        "line one\nline two\ncontext sentinel\nline four\nline five\nline six\n",
    )
    locations["context.txt"] = root / "context.txt"

    types_dir = root / "types"
    types_dir.mkdir()
    _write_text(
        types_dir / "match.py", "def match_py():\n    return 'glob sentinel type sentinel'\n"
    )
    _write_text(types_dir / "skip.txt", "glob sentinel but not python\n")
    locations["types"] = types_dir

    files_dir = root / "files"
    files_dir.mkdir()
    _write_text(files_dir / "match.txt", "files match sentinel\n")
    _write_text(files_dir / "miss.txt", "plain text without the sentinel\n")
    nested_files = files_dir / "nested"
    nested_files.mkdir()
    _write_text(nested_files / "match.txt", "files match sentinel in nested file\n")
    _write_text(nested_files / "miss.txt", "still no match here\n")
    locations["files"] = files_dir
    locations["files-match.txt"] = files_dir / "match.txt"
    locations["files-miss.txt"] = files_dir / "miss.txt"
    locations["files-nested-match.txt"] = nested_files / "match.txt"
    locations["files-nested-miss.txt"] = nested_files / "miss.txt"

    nested_dir = root / "nested"
    nested_dir.mkdir()
    _write_text(
        nested_dir / "visible.txt",
        "json sentinel nested\nndjson sentinel nested\n",
    )

    _write_text(
        root / "visible.txt",
        "json sentinel visible\nndjson sentinel visible\n",
    )

    hidden_dir = root / ".hidden"
    hidden_dir.mkdir()
    _write_text(
        hidden_dir / "secret.txt",
        "hidden sentinel\njson sentinel hidden excluded\nndjson sentinel hidden excluded\n",
    )

    ignored_dir = root / "ignored"
    ignored_dir.mkdir()
    _write_text(
        ignored_dir / "ignored.txt",
        "json sentinel ignored excluded\n"
        "ndjson sentinel ignored excluded\n"
        "files match sentinel ignored excluded\n",
    )
    _write_text(root / "also.ignored", "ignored suffix file\n")

    _write_text(root / "literal.txt", "literal.*chars\nliteral123chars\n")
    locations["literal.txt"] = root / "literal.txt"

    _write_text(
        root / "words.txt",
        "word\nwordplay\nsword\nword\n",
    )
    locations["words.txt"] = root / "words.txt"

    _write_text(
        root / "max-count.txt",
        "\n".join(f"max-count sentinel {index}" for index in range(12)) + "\n",
    )
    locations["max-count.txt"] = root / "max-count.txt"

    _write_text(
        root / "smart-case.txt",
        "SmartCase Sentinel\n",
    )
    locations["smart-case.txt"] = root / "smart-case.txt"

    _write_text(
        root / "line-number.txt",
        "before\nmore before\nline number sentinel\nafter\n",
    )
    locations["line-number.txt"] = root / "line-number.txt"

    _write_text(
        root / "column.txt",
        "prefix\n    column sentinel\n",
    )
    locations["column.txt"] = root / "column.txt"

    counts_dir = root / "counts"
    counts_dir.mkdir()
    _write_text(counts_dir / "a.txt", "count sentinel\ncount sentinel\n")
    _write_text(counts_dir / "b.txt", "count sentinel\n")
    locations["counts"] = counts_dir
    locations["count-a.txt"] = counts_dir / "a.txt"
    locations["count-b.txt"] = counts_dir / "b.txt"

    _write_text(
        root / "count-matches.txt",
        "count-match sentinel count-match sentinel\ncount-match sentinel\n",
    )
    locations["count-matches.txt"] = root / "count-matches.txt"

    binary_path = root / "binary.bin"
    binary_path.write_bytes(b"text sentinel line\n\x00binary tail\n")
    locations["binary.bin"] = binary_path

    links_dir = root / "links"
    links_dir.mkdir()
    locations["links"] = links_dir
    follow_supported = _create_follow_fixture(root=root, links_dir=links_dir)

    return RGParityCorpus(root=root, locations=locations, follow_supported=follow_supported)


def resolve_pinned_rg_binary() -> Path | None:
    resolved = resolve_ripgrep_binary()
    if resolved is not None:
        return resolved

    if not sys.platform.startswith("win"):
        return None

    benchmarks_dir = REPO_ROOT / "benchmarks"
    dev_candidate = benchmarks_dir / WINDOWS_RG_DIRNAME / "rg.exe"
    if dev_candidate.is_file():
        return dev_candidate.resolve()

    archive = benchmarks_dir / "rg.zip"
    if not archive.is_file():
        return None

    with zipfile.ZipFile(archive) as bundle:
        member = next((name for name in bundle.namelist() if name.endswith("/rg.exe")), None)
        if member is None:
            return None
        bundle.extractall(benchmarks_dir)

    if dev_candidate.is_file():
        return dev_candidate.resolve()
    return None


def run_parity_case(*, case: RGParityCase, corpus: RGParityCorpus, rg_binary: Path) -> RGParityRun:
    env = build_command_env(rg_binary)
    rg_argv, tg_argv = build_case_commands(case=case, corpus=corpus, rg_binary=rg_binary)

    return RGParityRun(
        case=case,
        rg=_run_command(rg_argv, cwd=corpus.root, env=env),
        tg=_run_command(tg_argv, cwd=corpus.root, env=env),
    )


def skip_reason_for_case(case: RGParityCase) -> str | None:
    if "--ndjson" in case.tg_args and resolve_native_tg_binary() is None:
        return "--ndjson parity requires the native tg binary"
    return None


def build_case_commands(
    *,
    case: RGParityCase,
    corpus: RGParityCorpus,
    rg_binary: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    cli_targets = corpus.cli_targets(case.targets)
    rg_argv = (str(rg_binary), *case.rg_args, case.pattern, *cli_targets)
    tg_argv = (
        sys.executable,
        "-m",
        "tensor_grep",
        "search",
        *case.tg_args,
        case.pattern,
        *cli_targets,
    )
    return rg_argv, tg_argv


def build_command_env(rg_binary: Path) -> dict[str, str]:
    return _command_env(rg_binary)


def normalize_output(
    output: str,
    *,
    case: RGParityCase,
    tool: str,
    corpus: RGParityCorpus,
):
    mode = case.row["output_mode"]
    if mode in {"json", "ndjson"}:
        return _normalize_machine_output(output, tool=tool, corpus=corpus)
    if mode == "count":
        return tuple(sorted(_normalize_plain_lines(output, corpus=corpus)))
    return tuple(_normalize_plain_lines(output, corpus=corpus))


def normalize_stderr(stderr: str, *, corpus: RGParityCorpus) -> tuple[str, ...]:
    return tuple(_normalize_plain_lines(stderr, corpus=corpus))


def format_parity_mismatch(
    *,
    result: RGParityRun,
    corpus: RGParityCorpus,
    detail: str,
) -> str:
    return (
        f"row={result.case.row['id']} {detail}\n"
        f"rg argv: {' '.join(result.rg.argv)}\n"
        f"tg argv: {' '.join(result.tg.argv)}\n"
        f"rg exit={result.rg.returncode} tg exit={result.tg.returncode}\n"
        f"rg stdout:\n{_display_blob(result.rg.stdout, corpus=corpus)}\n"
        f"tg stdout:\n{_display_blob(result.tg.stdout, corpus=corpus)}\n"
        f"rg stderr:\n{_display_blob(result.rg.stderr, corpus=corpus)}\n"
        f"tg stderr:\n{_display_blob(result.tg.stderr, corpus=corpus)}"
    )


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _create_follow_fixture(*, root: Path, links_dir: Path) -> bool:
    target = root / "follow-target.txt"
    _write_text(target, "follow sentinel\n")
    link_path = links_dir / "visible-link.txt"
    try:
        link_path.symlink_to(target)
    except OSError:
        return False
    return True


def _command_env(rg_binary: Path) -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_entries = [str(SRC_DIR)]
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        pythonpath_entries.extend(
            entry
            for entry in existing_pythonpath.split(os.pathsep)
            if entry and entry != str(SRC_DIR)
        )
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    env["TG_RG_PATH"] = str(rg_binary)
    native_tg = resolve_native_tg_binary()
    if native_tg is not None:
        env["TG_NATIVE_TG_BINARY"] = str(native_tg)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def resolve_native_tg_binary() -> Path | None:
    exe_name = "tg.exe" if sys.platform == "win32" else "tg"
    for worktree_root in _candidate_repo_roots():
        release_path = worktree_root / "rust_core" / "target" / "release" / exe_name
        if release_path.exists():
            return release_path.resolve()
        debug_path = worktree_root / "rust_core" / "target" / "debug" / exe_name
        if debug_path.exists():
            return debug_path.resolve()
    return None


def _candidate_repo_roots() -> tuple[Path, ...]:
    roots: list[Path] = [REPO_ROOT]
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
        )
    except OSError:
        return tuple(roots)
    if result.returncode != 0:
        return tuple(roots)
    for line in result.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        candidate = Path(line.removeprefix("worktree ").strip())
        if candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


def _run_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
) -> CommandResult:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return CommandResult(
        argv=argv,
        returncode=int(completed.returncode),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _cli_target(root: Path, path: Path) -> str:
    if path == root:
        return "."
    return path.relative_to(root).as_posix()


def _normalize_plain_lines(text: str, *, corpus: RGParityCorpus) -> list[str]:
    if not text:
        return []
    return [
        _normalize_line(line, corpus=corpus)
        for line in text.replace("\r\n", "\n").split("\n")
        if line
    ]


def _normalize_machine_output(
    text: str,
    *,
    tool: str,
    corpus: RGParityCorpus,
) -> dict[str, list[tuple[str, int, str]]]:
    if not text.strip():
        return {"matches": []}

    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    matches: list[tuple[str, int, str]] = []

    if tool == "tg":
        if len(rows) == 1 and isinstance(rows[0], dict) and "matches" in rows[0]:
            for match in rows[0]["matches"]:
                matches.append(
                    (
                        _normalize_path(str(match["file"]), corpus=corpus),
                        int(match.get("line", match.get("line_number"))),
                        str(match["text"]).rstrip("\r\n"),
                    )
                )
            return {"matches": sorted(matches)}

        for row in rows:
            matches.append(
                (
                    _normalize_path(str(row["file"]), corpus=corpus),
                    int(row.get("line", row.get("line_number"))),
                    str(row["text"]).rstrip("\r\n"),
                )
            )
        return {"matches": sorted(matches)}

    for row in rows:
        if row.get("type") != "match":
            continue
        data = row["data"]
        matches.append(
            (
                _normalize_path(str(data["path"]["text"]), corpus=corpus),
                int(data["line_number"]),
                str(data["lines"]["text"]).rstrip("\r\n"),
            )
        )
    return {"matches": sorted(matches)}


def _normalize_line(line: str, *, corpus: RGParityCorpus) -> str:
    normalized = line.replace(str(corpus.root), ".")
    normalized = normalized.replace(corpus.root.as_posix(), ".")
    normalized = normalized.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _normalize_path(path: str, *, corpus: RGParityCorpus) -> str:
    normalized = path.replace(str(corpus.root), ".")
    normalized = normalized.replace(corpus.root.as_posix(), ".")
    normalized = normalized.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _display_blob(text: str, *, corpus: RGParityCorpus) -> str:
    normalized_lines = _normalize_plain_lines(text, corpus=corpus)
    if not normalized_lines:
        return "<empty>"
    return "\n".join(normalized_lines)
