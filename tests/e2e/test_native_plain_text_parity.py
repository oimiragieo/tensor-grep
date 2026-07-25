"""Byte-exact `tg` vs `rg` parity for the plain-text native route.

WHY THIS FILE EXISTS -- the normalization blindness it covers
-------------------------------------------------------------
The pre-existing rg-parity oracles normalize BOTH sides into agreement before comparing, so
neither can see the two divergences that motivated it:

* ``tests/e2e/test_rg_parity_edges.py::_normalize`` does ``text.replace("\\r\\n", "\\n")``,
  which hides a CRLF divergence entirely (the native plain sink strips a trailing ``\\r``
  because nothing on that path installs a CRLF line terminator, while ``rg`` keeps it).
* ``tests/e2e/test_rg_parity_edges.py::_run`` and the matrix runner capture with
  ``encoding="utf-8", errors="replace"``, which maps a raw non-UTF-8 byte from ``rg`` to the
  SAME U+FFFD the native ``grep_searcher::sinks::Lossy`` sink already substituted -- so silent
  mojibake reads as a pass.

A green CI on those suites therefore does NOT clear a routing change that sends plain-text
searches to the native engine. This suite compares raw ``stdout``/``stderr`` BYTES with zero
normalization and no ``errors="replace"``, over a corpus built specifically from the shapes the
admitted subset has to get right or refuse: CRLF, non-UTF-8 (Latin-1), NUL/binary, an empty
file, a file with no trailing newline, a pure-ASCII file, a multi-byte-UTF-8 file, and the
empty pattern.

It does NOT replace or weaken the existing oracles -- it runs alongside them.
"""

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


# (fixture name, raw bytes). Written in binary mode so no newline translation can occur.
_CORPUS: tuple[tuple[str, bytes], ...] = (
    ("lf.txt", b"needle alpha\nplain line\nneedle omega\n"),
    ("crlf.txt", b"needle alpha\r\nplain line\r\nneedle omega\r\n"),
    ("mixed_cr.txt", b"needle alpha\r\r\nneedle beta\n"),
    ("latin1.txt", b"caf\xe9 needle here\nneedle plain\n"),
    ("utf8.txt", "needle café 日本語\nneedle plain\n".encode()),
    ("no_trailing_newline.txt", b"needle alpha\nneedle final without newline"),
    ("empty.txt", b""),
    ("binary.bin", b"needle text\n\x00binary tail\nneedle again\n"),
    ("no_match.txt", b"nothing to see here\n"),
)

# (id, extra flags, fixture, pattern)
_CASES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("plain-lf", (), "lf.txt", "needle"),
    ("plain-lf-line-number", ("-n",), "lf.txt", "needle"),
    ("plain-lf-ignore-case", ("-i",), "lf.txt", "NEEDLE"),
    ("plain-lf-fixed-strings", ("-F",), "lf.txt", "needle"),
    ("plain-lf-word-regexp", ("-w",), "lf.txt", "needle"),
    ("plain-lf-combined-short", ("-in",), "lf.txt", "NEEDLE"),
    ("crlf", (), "crlf.txt", "needle"),
    ("crlf-line-number", ("-n",), "crlf.txt", "needle"),
    ("crlf-fixed-strings", ("-F",), "crlf.txt", "needle"),
    ("mixed-cr", (), "mixed_cr.txt", "needle"),
    ("latin1", (), "latin1.txt", "needle"),
    ("latin1-ignore-case", ("-i",), "latin1.txt", "NEEDLE"),
    ("utf8-multibyte", (), "utf8.txt", "needle"),
    ("no-trailing-newline", (), "no_trailing_newline.txt", "needle"),
    ("no-trailing-newline-line-number", ("-n",), "no_trailing_newline.txt", "needle"),
    ("empty-file", (), "empty.txt", "needle"),
    ("binary", (), "binary.bin", "needle"),
    ("no-match", (), "no_match.txt", "needle"),
    ("empty-pattern", (), "lf.txt", ""),
    ("regex-pattern", (), "lf.txt", "needle (alpha|omega)"),
)


@pytest.fixture(scope="module")
def native_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("native-plain-text-parity")
    for name, payload in _CORPUS:
        (root / name).write_bytes(payload)
    return root


def _run_bytes(argv: list[str], *, cwd: Path, env: dict[str, str]):
    """Run without text mode: `capture_output` returns raw bytes, so nothing is transcoded."""
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("flags", "fixture", "pattern"),
    [case[1:] for case in _CASES],
    ids=[case[0] for case in _CASES],
)
def test_native_plain_text_stdout_is_byte_identical_to_ripgrep(
    native_corpus: Path,
    flags: tuple[str, ...],
    fixture: str,
    pattern: str,
) -> None:
    helpers = _helpers()
    rg_binary = helpers.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for byte-parity coverage")
    tg_binary = helpers.resolve_native_tg_binary()
    if tg_binary is None:
        pytest.skip("native tg binary not built; this suite must exercise the real front door")

    env = helpers.build_command_env(rg_binary)
    target = str(native_corpus / fixture)

    rg = _run_bytes([str(rg_binary), *flags, pattern, target], cwd=native_corpus, env=env)
    tg = _run_bytes(
        [str(tg_binary), "search", *flags, pattern, target],
        cwd=native_corpus,
        env=env,
    )

    assert tg.returncode == rg.returncode, (
        f"exit-code mismatch for {flags} {pattern!r} {fixture}\n"
        f"rg={rg.returncode} tg={tg.returncode}\n"
        f"rg stderr={rg.stderr!r}\ntg stderr={tg.stderr!r}"
    )
    assert tg.stdout == rg.stdout, (
        f"stdout BYTES differ for {flags} {pattern!r} {fixture}\nrg={rg.stdout!r}\ntg={tg.stdout!r}"
    )


@pytest.mark.characterization
def test_native_plain_text_route_emits_no_extra_stderr(native_corpus: Path) -> None:
    """The rg fallback net prints a `warning: native CPU search failed...` line on a native
    error. An admitted request must never trip it, and a REFUSED request must not either --
    the empty-pattern case is exactly the shape that did before it was excluded."""
    helpers = _helpers()
    rg_binary = helpers.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for byte-parity coverage")
    tg_binary = helpers.resolve_native_tg_binary()
    if tg_binary is None:
        pytest.skip("native tg binary not built; this suite must exercise the real front door")

    env = helpers.build_command_env(rg_binary)
    for fixture, pattern in (("lf.txt", "needle"), ("lf.txt", ""), ("crlf.txt", "needle")):
        tg = _run_bytes(
            [str(tg_binary), "search", pattern, str(native_corpus / fixture)],
            cwd=native_corpus,
            env=env,
        )
        assert b"falling back to ripgrep" not in tg.stderr, (
            f"native fallback warning leaked for {pattern!r} {fixture}: {tg.stderr!r}"
        )


@pytest.mark.characterization
def test_verbose_reports_the_backend_that_actually_ran(native_corpus: Path) -> None:
    """`--verbose` is allow-listed precisely because it reports the routing decision. An
    admitted request must report the native route; a refused one must still report rg."""
    helpers = _helpers()
    rg_binary = helpers.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for byte-parity coverage")
    tg_binary = helpers.resolve_native_tg_binary()
    if tg_binary is None:
        pytest.skip("native tg binary not built; this suite must exercise the real front door")

    env = helpers.build_command_env(rg_binary)

    admitted = _run_bytes(
        [str(tg_binary), "search", "--verbose", "needle", str(native_corpus / "lf.txt")],
        cwd=native_corpus,
        env=env,
    )
    assert b"routing_reason=plain-text-native" in admitted.stderr, admitted.stderr

    # A CRLF file is refused by the predicate and must keep the ripgrep passthrough.
    refused = _run_bytes(
        [str(tg_binary), "search", "--verbose", "needle", str(native_corpus / "crlf.txt")],
        cwd=native_corpus,
        env=env,
    )
    assert b"routing_reason=rg_passthrough" in refused.stderr, refused.stderr
