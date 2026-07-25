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

CONTROL ARM (the discrimination proof this suite is not vacuous): on ``origin/main``, where no
plain-text search is routed natively, ``test_verbose_reports_the_backend_that_actually_ran`` FAILS
its ``routing_reason=plain-text-native`` assertion -- the route it checks for does not exist there.
Under a forced-native falsification (``TG_DISABLE_RG=1``, which drives the same emitter through
``native_cpu_rg_unavailable``), 9 of the byte-comparison cases fail. Both arms discriminate, so a
green run here is evidence rather than an absence of evidence.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
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
    # A UTF-8 BOM file is ADMITTED (the BOM bytes are valid UTF-8, no CR, no NUL), so parity rests
    # on BOTH engines defaulting to bom_sniffing(true). That is true by reading `build_searcher`,
    # and now asserted rather than assumed.
    ("bom.txt", b"\xef\xbb\xbfneedle alpha\nneedle omega\n"),
    # Case-mixed, for the $RIPGREP_CONFIG_PATH receipt: a config containing `-i` changes WHICH
    # lines match here, so the fixture can discriminate config-applied from config-ignored.
    ("mixed_case.txt", b"needle alpha\nNEEDLE beta\nplain line\n"),
    # ~248 KB where EVERY line matches, for the early-closing-consumer test. Two constraints make
    # this size load-bearing: it must exceed any OS pipe buffer (~64 KB) so closing the read end
    # actually breaks the pipe, and it must stay under PLAIN_TEXT_NATIVE_MAX_PROBE_BYTES (512 KiB)
    # so the request is still ADMITTED to the native route. Shrink it and the test goes vacuous;
    # `test_early_closing_consumer_matches_ripgrep` asserts the size to stop that happening
    # silently.
    ("dense.txt", b"needle alpha line of text here\n" * 8000),
)

DENSE_FIXTURE_BYTES = 8000 * len(b"needle alpha line of text here\n")

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
    ("bom", (), "bom.txt", "needle"),
    ("bom-line-number", ("-n",), "bom.txt", "needle"),
    ("mixed-case", (), "mixed_case.txt", "NEEDLE"),
    ("mixed-case-ignore-case", ("-i",), "mixed_case.txt", "NEEDLE"),
    # PATTERN-level cases. Every case above uses a valid pattern, which is exactly why a whole
    # divergence class (rg rc=2 + diagnostic vs native rc=1 + silence) survived the first review
    # of this file. rg refuses a pattern that can match a line terminator or NUL; the native
    # matcher accepts it and succeeds with zero matches.
    ("pattern-newline-escape", (), "lf.txt", "needle\\n"),
    ("pattern-bare-newline-escape", (), "lf.txt", "\\n"),
    ("pattern-newline-class", (), "lf.txt", "[\\n]"),
    ("pattern-crlf-escape", (), "lf.txt", "needle\\r\\n"),
    ("pattern-literal-newline", (), "lf.txt", "needle\n"),
    ("pattern-nul-escape", (), "lf.txt", "\\x00"),
    # ... and patterns that do not COMPILE: correct exit code, but the native route would emit an
    # extra `warning: native CPU search failed, falling back to ripgrep: ...` stderr line.
    ("pattern-unclosed-class", (), "lf.txt", "["),
    ("pattern-unclosed-group", (), "lf.txt", "("),
    ("pattern-qe-escape", (), "lf.txt", "\\Qx\\E"),
    ("pattern-repetition-blowup", (), "lf.txt", "a{500}{500}{500}"),
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


def _run_with_early_close(argv: list[str], *, cwd: Path, env: dict[str, str]):
    """Run ``argv``, read ONE line, close the pipe, then reap.

    ``_run_bytes`` uses ``capture_output=True``, which drains stdout to EOF -- a harness that
    always reads everything can never produce a broken pipe, which is exactly why this class of
    divergence was invisible to every other case in this file. This models the real consumer
    (``| head -1``, ``| less``, an agent that reads N lines and stops).

    EVERY wait is bounded, including the first-line read (an earlier revision claimed this while
    leaving `proc.stderr.read()` unbounded AHEAD of the bounded `wait()`, so a hung child would
    have blocked forever and the `finally` kill would never have run). The read is bounded by a
    daemon thread with a join timeout, and stderr is drained through `communicate(timeout=...)`.
    """
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    first: list[bytes] = []

    def _read_first_line() -> None:
        first.append(proc.stdout.readline())

    try:
        reader = threading.Thread(target=_read_first_line, daemon=True)
        reader.start()
        reader.join(timeout=60)
        if reader.is_alive():
            raise AssertionError(f"{argv[0]} produced no line within 60s")

        proc.stdout.close()
        # `communicate()` must not touch the pipe we just closed; None makes it skip stdout.
        proc.stdout = None
        _, stderr = proc.communicate(timeout=60)
        returncode = proc.returncode
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    return first[0], stderr, returncode


def _require_binaries():
    """Resolve `rg` and the native `tg`, or SKIP -- LOUDLY when the caller demanded coverage.

    A silent skip is how a gate stops gating. CI sets ``TG_REQUIRE_RG_PARITY=1`` on the job that
    builds the native binary, which turns "not available" from a skip into a failure, so a runner
    without ripgrep (the macOS legs have no bundled darwin binary in-tree -- only
    ``benchmarks/rg.zip``, which is Windows-only) can never masquerade as passing coverage.
    """
    helpers = _helpers()
    required = os.environ.get("TG_REQUIRE_RG_PARITY", "").strip().lower() in {"1", "true", "yes"}

    rg_binary = helpers.resolve_pinned_rg_binary()
    tg_binary = helpers.resolve_native_tg_binary()
    missing = []
    if rg_binary is None:
        missing.append("ripgrep (install it, or set TG_RG_PATH)")
    if tg_binary is None:
        missing.append("native tg binary (cargo build --release in rust_core/)")

    if missing:
        message = f"byte-parity oracle cannot run; missing: {', '.join(missing)}"
        if required:
            pytest.fail(f"TG_REQUIRE_RG_PARITY=1 but {message}")
        pytest.skip(message)

    return helpers, rg_binary, tg_binary


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
    helpers, rg_binary, tg_binary = _require_binaries()

    env = helpers.build_command_env(rg_binary)
    target = str(native_corpus / fixture)

    rg = _run_bytes([str(rg_binary), *flags, pattern, target], cwd=native_corpus, env=env)
    tg = _run_bytes(
        [str(tg_binary), "search", *flags, pattern, target],
        cwd=native_corpus,
        env=env,
    )

    context = f"{flags} {pattern!r} {fixture}"
    assert tg.returncode == rg.returncode, (
        f"exit-code mismatch for {context}\n"
        f"rg={rg.returncode} tg={tg.returncode}\n"
        f"rg stderr={rg.stderr!r}\ntg stderr={tg.stderr!r}"
    )
    assert tg.stdout == rg.stdout, (
        f"stdout BYTES differ for {context}\nrg={rg.stdout!r}\ntg={tg.stdout!r}"
    )
    # STDERR is asserted too, not just stdout: a refused request reaches `rg` through
    # `Stdio::inherit()` so its diagnostics must pass through byte-identically, and an admitted
    # request must not GAIN a line (the `warning: native CPU search failed...` class).
    assert tg.stderr == rg.stderr, (
        f"stderr BYTES differ for {context}\nrg={rg.stderr!r}\ntg={tg.stderr!r}"
    )


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("variant", "argv_template"),
    [
        ("dash-e", ["-e", "needle"]),
        ("dash-e-with-flag", ["-e", "needle", "-w"]),
        ("end-of-options", ["--", "needle"]),
        ("combined-short", ["-in", "NEEDLE"]),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_argv_spelling_variants_are_byte_identical(
    native_corpus: Path,
    variant: str,
    argv_template: list[str],
) -> None:
    """`-e PATTERN`, the `--` sentinel and combined short clusters reach the admitted subset by a
    different argv route than a bare positional pattern. The Rust units cover the routing decision
    for these; nothing covered the resulting BYTES until now."""
    helpers, rg_binary, tg_binary = _require_binaries()

    env = helpers.build_command_env(rg_binary)
    target = str(native_corpus / "lf.txt")

    rg = _run_bytes([str(rg_binary), *argv_template, target], cwd=native_corpus, env=env)
    tg = _run_bytes(
        [str(tg_binary), "search", *argv_template, target],
        cwd=native_corpus,
        env=env,
    )

    assert tg.returncode == rg.returncode, f"{variant}: rg={rg.returncode} tg={tg.returncode}"
    assert tg.stdout == rg.stdout, f"{variant} stdout: rg={rg.stdout!r} tg={tg.stdout!r}"
    assert tg.stderr == rg.stderr, f"{variant} stderr: rg={rg.stderr!r} tg={tg.stderr!r}"


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("variant", "config_body"),
    [
        ("ignore-case", "-i\n"),
        ("vimgrep", "--vimgrep\n"),
        ("color-always", "--color=always\n"),
        ("word-regexp", "-w\n"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_ripgrep_config_env_is_honored(
    native_corpus: Path,
    tmp_path: Path,
    variant: str,
    config_body: str,
) -> None:
    """$RIPGREP_CONFIG_PATH is rg's env config surface, and the in-process native engine reads NO
    rg config. `execute_ripgrep_search` never clears the environment and only sends `--no-config`
    when the user asks, so a plain-text search routed to rg applies the user's config today.

    This is the class no other oracle can see: CI never sets the variable and the shared parity
    helpers copy `os.environ` verbatim. Without the `rg_config_env_present` refusal clause, the
    canonical admitted shape (no flags, one pattern, one clean file, piped stdout) returns
    SILENTLY WRONG results here -- `-i` alone changes which lines match.
    """
    helpers, rg_binary, tg_binary = _require_binaries()

    config = tmp_path / f"rgrc-{variant}"
    config.write_text(config_body, encoding="utf-8")
    env = helpers.build_command_env(rg_binary)
    env["RIPGREP_CONFIG_PATH"] = str(config)
    target = str(native_corpus / "mixed_case.txt")

    rg = _run_bytes([str(rg_binary), "NEEDLE", target], cwd=native_corpus, env=env)
    tg = _run_bytes([str(tg_binary), "search", "NEEDLE", target], cwd=native_corpus, env=env)

    assert tg.returncode == rg.returncode, f"{variant}: rg={rg.returncode} tg={tg.returncode}"
    assert tg.stdout == rg.stdout, f"{variant} stdout: rg={rg.stdout!r} tg={tg.stdout!r}"
    assert tg.stderr == rg.stderr, f"{variant} stderr: rg={rg.stderr!r} tg={tg.stderr!r}"


@pytest.mark.characterization
def test_ripgrep_config_env_edge_values(native_corpus: Path, tmp_path: Path) -> None:
    """An EMPTY value is ignored by rg, so it must not disqualify; a DANGLING path makes rg emit a
    read-failure diagnostic the native route would omit, so it must."""
    helpers, rg_binary, tg_binary = _require_binaries()

    target = str(native_corpus / "mixed_case.txt")
    for value in ("", str(tmp_path / "does-not-exist-rgrc")):
        env = helpers.build_command_env(rg_binary)
        env["RIPGREP_CONFIG_PATH"] = value

        rg = _run_bytes([str(rg_binary), "NEEDLE", target], cwd=native_corpus, env=env)
        tg = _run_bytes([str(tg_binary), "search", "NEEDLE", target], cwd=native_corpus, env=env)

        assert tg.returncode == rg.returncode, f"{value!r}: rg={rg.returncode} tg={tg.returncode}"
        assert tg.stdout == rg.stdout, f"{value!r} stdout: rg={rg.stdout!r} tg={tg.stdout!r}"
        assert tg.stderr == rg.stderr, f"{value!r} stderr: rg={rg.stderr!r} tg={tg.stderr!r}"


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("variant", "argv_tail"),
    [
        ("bare-dash", ["needle", "-"]),
        ("end-of-options-dash", ["--", "needle", "-"]),
        ("dash-e-dash", ["-e", "needle", "-"]),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_dash_path_operand_reads_stdin_like_ripgrep(
    tmp_path: Path,
    variant: str,
    argv_tail: list[str],
) -> None:
    """`-` is the one PATH operand ripgrep gives a special meaning: read STDIN.

    The predicate modelled it as an ordinary path, and `Path::new("-").is_file()` is TRUE whenever
    a file literally named `-` exists in cwd -- so the native route searched that FILE while rg
    searched STDIN. rc=0, no stderr, plausible output, wrong data source. Measured before the fix::

        rg 15.1.0 / shipped tg 1.98.3 -> 'needle from STDIN'
        native emitter               -> 'needle in dashfile'

    A dedicated cwd is used so the file named `-` cannot leak into the shared corpus.
    """
    helpers, rg_binary, tg_binary = _require_binaries()

    workdir = tmp_path / f"dash-{variant}"
    workdir.mkdir()
    (workdir / "-").write_bytes(b"needle in dashfile\n")
    env = helpers.build_command_env(rg_binary)
    piped = b"needle from STDIN\n"

    rg = subprocess.run(
        [str(rg_binary), *argv_tail], cwd=workdir, env=env, input=piped, capture_output=True
    )
    tg = subprocess.run(
        [str(tg_binary), "search", *argv_tail],
        cwd=workdir,
        env=env,
        input=piped,
        capture_output=True,
    )

    assert b"dashfile" not in tg.stdout, (
        f"{variant}: `-` must read STDIN, not the file named '-': {tg.stdout!r}"
    )
    assert tg.stdout == rg.stdout, f"{variant} stdout: rg={rg.stdout!r} tg={tg.stdout!r}"
    assert tg.stderr == rg.stderr, f"{variant} stderr: rg={rg.stderr!r} tg={tg.stderr!r}"
    assert tg.returncode == rg.returncode, f"{variant}: rg={rg.returncode} tg={tg.returncode}"


@pytest.mark.characterization
def test_early_closing_consumer_matches_ripgrep(native_corpus: Path) -> None:
    """A consumer that closes the pipe early must terminate tg exactly the way it terminates rg.

    This route moves ownership of the WRITE LOOP out of an ``Stdio::inherit()`` subprocess and
    into tg's own process, and the two react oppositely to EPIPE: rg absorbs it, while the native
    sink surfaces it as an error that would print a `warning: native CPU search failed...` line
    and then RE-RUN the whole search into the already-closed pipe.

    Measured on the shipped v1.98.3 before the fix (Windows, rg 15.1.0, 3/3 runs)::

        rg        needle dense.txt | head -1  -> rc=1, stderr ''
        tg-native needle dense.txt | head -1  -> rc=2, stderr 'native standard output search ...'

    rg is the CONTROL ARM: the assertions compare tg against whatever rg does on this platform
    rather than against a hard-coded exit code, so a platform whose rg disagrees surfaces as a real
    finding instead of a Windows-only number frozen into the test.
    """
    helpers, rg_binary, tg_binary = _require_binaries()

    env = helpers.build_command_env(rg_binary)
    target = str(native_corpus / "dense.txt")

    # Guard against the test going vacuous: the output must dwarf any OS pipe buffer (~64 KB), or
    # a single buffer absorbs everything and the pipe never breaks.
    drained = _run_bytes([str(rg_binary), "needle", target], cwd=native_corpus, env=env)
    assert len(drained.stdout) == DENSE_FIXTURE_BYTES, (
        f"dense fixture must emit every line; got {len(drained.stdout)} bytes"
    )
    assert len(drained.stdout) > 200_000, "fixture too small to break a pipe reliably"

    rg_line, rg_stderr, rg_rc = _run_with_early_close(
        [str(rg_binary), "needle", target], cwd=native_corpus, env=env
    )
    tg_line, tg_stderr, tg_rc = _run_with_early_close(
        [str(tg_binary), "search", "needle", target], cwd=native_corpus, env=env
    )

    assert tg_line == rg_line, f"first line differs: rg={rg_line!r} tg={tg_line!r}"
    assert b"falling back to ripgrep" not in tg_stderr, (
        f"a closed consumer must not trip the native fallback warning: {tg_stderr!r}"
    )
    assert tg_stderr == rg_stderr, f"stderr differs: rg={rg_stderr!r} tg={tg_stderr!r}"
    assert tg_rc == rg_rc, f"exit code differs: rg={rg_rc} tg={tg_rc}"


@pytest.mark.characterization
def test_native_plain_text_route_emits_no_extra_stderr(native_corpus: Path) -> None:
    """The rg fallback net prints a `warning: native CPU search failed...` line on a native
    error. An admitted request must never trip it, and a REFUSED request must not either --
    the empty-pattern case is exactly the shape that did before it was excluded."""
    helpers, rg_binary, tg_binary = _require_binaries()

    env = helpers.build_command_env(rg_binary)
    probes = (
        ("lf.txt", "needle"),
        ("lf.txt", ""),
        ("crlf.txt", "needle"),
        ("latin1.txt", "needle"),
        ("lf.txt", "["),
        ("lf.txt", "needle\n"),
        ("lf.txt", "a{500}{500}{500}"),
    )
    for fixture, pattern in probes:
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
    helpers, rg_binary, tg_binary = _require_binaries()

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
