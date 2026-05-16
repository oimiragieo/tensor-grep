from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _helpers():
    from helpers import rg_parity

    return rg_parity


def _write_edge_corpus(root: Path) -> None:
    root.mkdir(parents=True)
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (root / "b.txt").write_text("needle beta\n", encoding="utf-8")
    (root / "a.txt").write_text("needle alpha\n", encoding="utf-8")
    (root / "c.txt").write_text("plain text\n", encoding="utf-8")
    (root / "dash.txt").write_text("-needle dash\nplain text\n", encoding="utf-8")
    (root / "pcre-z.txt").write_text("needle pcre\n", encoding="utf-8")
    (root / "multi.py").write_text(
        "# needle multiline fixture\n"
        "def create_invoice(subtotal):\n"
        "    tax = subtotal * 0.1\n"
        "    return subtotal + tax\n",
        encoding="utf-8",
    )
    nested_dir = root / "nested"
    nested_dir.mkdir()
    (nested_dir / "d.txt").write_text("needle nested\n", encoding="utf-8")
    ignored_dir = root / "ignored"
    ignored_dir.mkdir()
    (ignored_dir / "z.txt").write_text("needle ignored\n", encoding="utf-8")
    binary_path = root / "binary.bin"
    binary_path.write_bytes(b"needle\0binary tail\n")
    (root / "binary_nomatch.bin").write_bytes(b"other\0binary tail\n")
    subprocess.run(["git", "init"], cwd=root, check=False, capture_output=True, text=True)


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    stdin = subprocess.DEVNULL if input_text is None else None
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=input_text,
        stdin=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _normalize(text: str, root: Path) -> list[str]:
    normalized: list[str] = []
    for line in text.replace("\r\n", "\n").splitlines():
        if not line:
            continue
        current = line.replace(str(root), ".").replace(root.as_posix(), ".").replace("\\", "/")
        if current.startswith("./"):
            current = current[2:]
        normalized.append(current)
    return normalized


def _assert_same_rg_behavior(
    *,
    rg_args: list[str],
    tg_args: list[str],
    root: Path,
    env: dict[str, str],
    rg_binary: Path,
    compare_stdout: bool = True,
    input_text: str | None = None,
) -> None:
    rg = _run([str(rg_binary), *rg_args], cwd=root, env=env, input_text=input_text)
    tg = _run(
        [sys.executable, "-m", "tensor_grep", "search", *tg_args],
        cwd=root,
        env=env,
        input_text=input_text,
    )

    assert tg.returncode == rg.returncode, (
        f"rg exit={rg.returncode} tg exit={tg.returncode}\n"
        f"rg stderr={rg.stderr}\ntg stderr={tg.stderr}"
    )
    if compare_stdout:
        assert _normalize(tg.stdout, root) == _normalize(rg.stdout, root)


def _assert_same_rg_stdout_bytes(
    *,
    rg_args: list[str],
    tg_args: list[str],
    root: Path,
    env: dict[str, str],
    rg_binary: Path,
) -> None:
    rg = _run([str(rg_binary), *rg_args], cwd=root, env=env)
    tg = _run(
        [sys.executable, "-m", "tensor_grep", "search", *tg_args],
        cwd=root,
        env=env,
    )

    assert tg.returncode == rg.returncode, (
        f"rg exit={rg.returncode} tg exit={tg.returncode}\n"
        f"rg stderr={rg.stderr}\ntg stderr={tg.stderr}"
    )
    assert tg.stdout.replace("\r\n", "\n") == rg.stdout.replace("\r\n", "\n")


@pytest.fixture()
def edge_corpus(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path / "rg-edges"
    _write_edge_corpus(root)
    rg_parity = _helpers()
    rg_binary = rg_parity.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for edge parity coverage")
    return root, rg_binary, rg_parity.build_command_env(rg_binary)


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("rg_args", "tg_args"),
    [
        (["needle", "."], ["needle", "."]),
        (["absent", "."], ["absent", "."]),
        (["(", "."], ["(", "."]),
        (["needle", "binary.bin"], ["needle", "binary.bin"]),
    ],
    ids=["match", "no-match", "parse-error", "binary-skip"],
)
def test_rg_exit_code_edges_match(
    edge_corpus: tuple[Path, Path, dict[str, str]],
    rg_args: list[str],
    tg_args: list[str],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=rg_args,
        tg_args=tg_args,
        root=root,
        env=env,
        rg_binary=rg_binary,
        compare_stdout=False,
    )


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("rg_args", "tg_args"),
    [
        (
            ["--files-with-matches", "--sort", "path", "needle", "."],
            ["--files-with-matches", "--sort", "path", "needle", "."],
        ),
        (
            ["--files-without-match", "--sort", "path", "needle", "."],
            ["--files-without-match", "--sort", "path", "needle", "."],
        ),
        (
            ["--replace", "hit", "--sort", "path", "needle", "."],
            ["--replace", "hit", "--sort", "path", "needle", "."],
        ),
    ],
    ids=["files-with-matches-sort", "files-without-match-sort", "replace-sort"],
)
def test_rg_sorted_output_edges_match(
    edge_corpus: tuple[Path, Path, dict[str, str]],
    rg_args: list[str],
    tg_args: list[str],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=rg_args,
        tg_args=tg_args,
        root=root,
        env=env,
        rg_binary=rg_binary,
    )


@pytest.mark.characterization
@pytest.mark.parametrize(
    "path_arg",
    [".", "./nested"],
    ids=["dot-root", "dot-slash-subdir"],
)
def test_rg_files_mode_preserves_rg_path_prefixes(
    edge_corpus: tuple[Path, Path, dict[str, str]],
    path_arg: str,
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_stdout_bytes(
        rg_args=["--files", "--sort", "path", path_arg],
        tg_args=["--files", "--sort", "path", path_arg],
        root=root,
        env=env,
        rg_binary=rg_binary,
    )


@pytest.mark.characterization
def test_rg_pcre2_sorted_output_matches(
    edge_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=["--pcre2", "--sort", "path", r"need(le|ful)", "."],
        tg_args=["--pcre2", "--sort", "path", r"need(le|ful)", "."],
        root=root,
        env=env,
        rg_binary=rg_binary,
    )


@pytest.mark.characterization
def test_rg_dash_leading_regexp_pattern_matches(
    edge_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=["-e", "-needle", "--sort", "path", "."],
        tg_args=["-e", "-needle", "--sort", "path", "."],
        root=root,
        env=env,
        rg_binary=rg_binary,
    )


@pytest.mark.characterization
def test_rg_multiple_regexp_patterns_match(
    edge_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=["-e", "-needle", "-e", "plain", "--sort", "path", "."],
        tg_args=["-e", "-needle", "-e", "plain", "--sort", "path", "."],
        root=root,
        env=env,
        rg_binary=rg_binary,
    )


@pytest.mark.characterization
def test_rg_no_path_searches_piped_stdin(
    edge_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=["needle"],
        tg_args=["needle"],
        root=root,
        env=env,
        rg_binary=rg_binary,
        input_text="stdin needle\nstdin other\n",
    )


@pytest.mark.characterization
def test_rg_no_stdin_default_path_still_searches_cwd(
    edge_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=["--sort", "path", "needle", "."],
        tg_args=["--sort", "path", "needle"],
        root=root,
        env=env,
        rg_binary=rg_binary,
    )


@pytest.mark.characterization
def test_rg_explicit_path_ignores_piped_stdin(
    edge_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=["needle", "a.txt"],
        tg_args=["needle", "a.txt"],
        root=root,
        env=env,
        rg_binary=rg_binary,
        input_text="stdin needle\n",
    )


@pytest.mark.characterization
def test_rg_multiline_output_matches(
    edge_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=["--multiline", r"create_invoice[\s\S]*return", "."],
        tg_args=["--multiline", r"create_invoice[\s\S]*return", "."],
        root=root,
        env=env,
        rg_binary=rg_binary,
    )


def test_files_without_match_sort_excludes_binary_and_ignored_paths(
    edge_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    root, _rg_binary, env = edge_corpus

    tg = _run(
        [
            sys.executable,
            "-m",
            "tensor_grep",
            "search",
            "--files-without-match",
            "--sort",
            "path",
            "needle",
            ".",
        ],
        cwd=root,
        env=env,
    )

    assert tg.returncode == 0, tg.stderr
    normalized = _normalize(tg.stdout, root)
    assert normalized == ["c.txt"]
    assert "binary.bin" not in normalized
    assert "binary_nomatch.bin" not in normalized
    assert "ignored/z.txt" not in normalized


def test_files_without_match_text_mode_includes_binary_paths(
    edge_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    root, rg_binary, env = edge_corpus

    _assert_same_rg_behavior(
        rg_args=["--files-without-match", "--text", "--sort", "path", "needle", "."],
        tg_args=["--files-without-match", "--text", "--sort", "path", "needle", "."],
        root=root,
        env=env,
        rg_binary=rg_binary,
    )
