from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers.byte_parity import assert_bytes_equal, decode_for_display, run_bytes  # noqa: E402


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
    # Repo bootstrap only -- not part of any rg-vs-tg comparison, so text=True here cannot
    # hide a divergence between the two engines under test.
    subprocess.run(["git", "init"], cwd=root, check=False, capture_output=True, text=True)


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    # Raw bytes only -- no text=/encoding=/errors=. See tests/helpers/byte_parity.py for why:
    # `text=True` would silently translate a real `\r\n` in captured stdout to `\n` on
    # Windows before either arm below is ever compared (task #262).
    return run_bytes(argv, cwd=cwd, env=env, input_bytes=input_bytes)


def _normalize(data: bytes, root: Path) -> list[bytes]:
    r"""Normalize ONLY the two things that are genuinely non-contractual across platforms:
    the absolute tmp-dir prefix (a pytest-generated path, never part of either engine's real
    output contract) and the path separator (both rg and tg accept '/' and '\\' as
    equivalent on Windows). Everything else -- including any embedded '\r' -- is compared
    verbatim. Deliberately NOT the old blanket `data.replace(b"\r\n", b"\n")`: that collapse
    erased a genuine CRLF divergence between the two engines before comparison ever ran (see
    PR #742's independent gate finding, task #262).

    KNOWN, ACKNOWLEDGED LIMIT (not closed here, independent-gate follow-up): the trailing
    `.replace(b"\\", b"/")` below runs against the WHOLE line, not just a parsed-out path
    prefix, so it is still a both-arms-lossy transform in the same shape this function
    otherwise removes -- a real backslash-vs-forward-slash divergence inside MATCHED TEXT
    content (not the file-path prefix) would cancel out identically on both arms. This
    module's fixtures are plain ASCII sentinel words with no backslashes in their content,
    so it has not been observed to mask a real failure, but it is a structural gap.
    """
    root_bytes = str(root).encode("utf-8")
    root_posix_bytes = root.as_posix().encode("utf-8")
    normalized: list[bytes] = []
    for line in data.split(b"\n"):
        if not line:
            continue
        current = (
            line.replace(root_bytes, b".").replace(root_posix_bytes, b".").replace(b"\\", b"/")
        )
        if current.startswith(b"./"):
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
    input_bytes: bytes | None = None,
) -> None:
    rg = _run([str(rg_binary), *rg_args], cwd=root, env=env, input_bytes=input_bytes)
    tg = _run(
        [sys.executable, "-m", "tensor_grep", "search", *tg_args],
        cwd=root,
        env=env,
        input_bytes=input_bytes,
    )

    assert tg.returncode == rg.returncode, (
        f"rg exit={rg.returncode} tg exit={tg.returncode}\n"
        f"rg stderr={decode_for_display(rg.stderr)}\ntg stderr={decode_for_display(tg.stderr)}"
    )
    if compare_stdout:
        assert _normalize(tg.stdout, root) == _normalize(rg.stdout, root), (
            f"rg stdout:\n{decode_for_display(rg.stdout)}\n"
            f"tg stdout:\n{decode_for_display(tg.stdout)}"
        )


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
        f"rg stderr={decode_for_display(rg.stderr)}\ntg stderr={decode_for_display(tg.stderr)}"
    )
    # Byte-exact: no path-prefix or separator normalization here at all (that is the whole
    # point of this comparator's name -- see test_rg_files_mode_preserves_rg_path_prefixes).
    assert_bytes_equal(tg.stdout, rg.stdout, label="rg-vs-tg --files stdout")


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
        input_bytes=b"stdin needle\nstdin other\n",
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
        input_bytes=b"stdin needle\n",
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

    assert tg.returncode == 0, decode_for_display(tg.stderr)
    normalized = _normalize(tg.stdout, root)
    assert normalized == [b"c.txt"]
    assert b"binary.bin" not in normalized
    assert b"binary_nomatch.bin" not in normalized
    assert b"ignored/z.txt" not in normalized


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
