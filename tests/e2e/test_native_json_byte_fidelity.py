"""Byte-fidelity tests for the native `tg search --json` walk emitter (task #266).

WHY THIS FILE EXISTS
---------------------
`tests/e2e/test_native_plain_text_parity.py` already proved (and documents) that the SHARED
native-search emitter (`rust_core/src/native_search.rs`) diverges from real `rg` on CRLF content
(a matched line's own trailing `\r` was stripped) and non-UTF-8 content (silently replaced with
U+FFFD). That file is scoped to the plain-text native route, which is gated by
`native_can_serve_plain_text`'s full-content probe -- a probe that REFUSES exactly the CRLF/
non-UTF-8/binary shapes that trigger the bug, so a normal directory search never actually hits
the buggy code on that route (it falls back to spawning real `rg` instead).

`--json`/`--ndjson` directory search has NO such probe: `bootstrap.py` unconditionally delegates
any search carrying `--json`/`--ndjson`/`--cpu`/`--force-cpu`/`--gpu-device-ids` to the native
binary, and MCP (`mcp_server.py`) builds every command it runs with `--json`. So this is the
route where the shared emitter's CRLF/non-UTF-8 defects are LIVE today, not merely latent --
this file targets exactly that route directly, one directory level below any Python routing
logic (it invokes the compiled native `tg` binary the same way `test_native_plain_text_parity.py`
does).

Real `rg --json`'s own JSON protocol was used as the ground truth for what "correct" looks like
here (verified directly via hexdump against `rg.exe` 15.1.0): a valid-UTF-8 matched line keeps
its own trailing `\r` intact in the `text` field, and an invalid-UTF-8 line is reported via a
`bytes` (base64) fallback instead of a lossily-corrupted `text` field. `tg`'s own `--json` schema
is NOT byte-identical to `rg --json` (different envelope entirely -- `routing_backend`,
`matched_file_paths`, etc.), so this file compares tg's own `matches[].text`/`matches[].bytes`
against the FIXTURE's raw source bytes (ground truth) rather than against rg's JSON wire format.

FAILS ON THE PRE-FIX EMITTER (confirmed by reading, not executing -- this session was CPU-SAFE
constrained and could not compile the Rust extension to run these tests itself; GitHub CI is the
oracle that must confirm this, per this repo's own change-control discipline for anything that
genuinely compiles):
* `test_native_json_preserves_crlf_trailing_cr` -- `search_plain_streaming`/
  `search_file_collect_matches_with_searcher`'s `Lossy` sink closures did
  `line.trim_end_matches(['\n', '\r'])`, eating the source line's own trailing `\r` before
  `NativeSearchMatch.text` was ever built. Pre-fix `text` would read `"needle crlf"`, not
  `"needle crlf\r"`.
* `test_native_json_preserves_invalid_utf8_losslessly` -- the same `Lossy` sink internally calls
  `String::from_utf8_lossy`, so the invalid `\xe9` byte in `latin1.txt` became U+FFFD
  (`\xef\xbf\xbd` when the resulting string round-trips through JSON) before
  `NativeSearchMatch.text` was ever built, with no way to recover the original byte. Pre-fix,
  this test's `"bytes" in match` assertion would fail outright (the field did not exist).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers import rg_parity  # noqa: E402

# Deliberately NOT a curated subtree of this repo (docs/routing_policy.md's own #266 investigation
# found a parity test built from `src/` shows zero divergences while `docs/` shows 6106 -- a real
# corpus can silently fail to exercise the defect at all). Two lines, written in binary mode so no
# newline translation can occur: one with a known CR (CRLF terminator), one with a known invalid
# UTF-8 byte.
CRLF_LINE = b"needle crlf\r\n"
LATIN1_LINE = b"caf\xe9 needle\n"


# Mirrors tensor_grep.cli.incompleteness.INCOMPLETENESS_MARKERS. Duplicated DELIBERATELY: this is a
# byte-fidelity e2e that exercises the SHIPPED binary, so importing the package under test would let
# a bug in that module mask itself here. Keep the two in sync -- see #313.
_E2E_INCOMPLETENESS_MARKERS = (
    "result_incomplete",
    "incomplete_reason_class",
    "keeping partial results",
)


def _disclosed_incomplete(stdout: object, stderr: object) -> bool:
    haystack = f"{stdout or ''} {stderr or ''}"
    return any(marker in haystack for marker in _E2E_INCOMPLETENESS_MARKERS)


def _require_native_tg_binary() -> Path:
    """Resolve the compiled native `tg` binary, or SKIP -- LOUDLY when the caller demanded
    coverage. Mirrors `test_native_plain_text_parity.py::_require_binaries`'s
    `TG_REQUIRE_RG_PARITY` contract: CI sets this on the job that builds the native binary, so a
    runner without one can never masquerade as passing coverage.
    """
    required = os.environ.get("TG_REQUIRE_RG_PARITY", "").strip().lower() in {"1", "true", "yes"}
    tg_binary = rg_parity.resolve_native_tg_binary()
    if tg_binary is None:
        message = "native tg binary not built (cargo build --release in rust_core/)"
        if required:
            pytest.fail(f"TG_REQUIRE_RG_PARITY=1 but {message}")
        pytest.skip(message)
    return tg_binary


@pytest.fixture()
def json_fidelity_corpus(tmp_path: Path) -> Path:
    (tmp_path / "crlf.txt").write_bytes(CRLF_LINE)
    (tmp_path / "latin1.txt").write_bytes(LATIN1_LINE)
    return tmp_path


def _run_native_json_search(tg_binary: Path, corpus: Path) -> list[dict]:
    proc = subprocess.run(
        [str(tg_binary), "search", "--json", "needle", str(corpus)],
        cwd=corpus,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    # Task #276 slice C0. Exit 2 is acceptable ONLY when the run disclosed an incomplete scan --
    # this fixture is a clean corpus so it should be 0 today, but once slice C lands a stray
    # unreadable path must not red this CI gate on an otherwise byte-correct payload.
    assert proc.returncode == 0 or (
        proc.returncode == 2 and _disclosed_incomplete(proc.stdout, proc.stderr)
    ), (
        f"native --json search failed: rc={proc.returncode} stderr={proc.stderr!r} "
        f"stdout={proc.stdout!r}"
    )
    payload = json.loads(proc.stdout)
    return payload["matches"]


def test_native_json_preserves_crlf_trailing_cr(json_fidelity_corpus: Path) -> None:
    """A CRLF source line's own trailing `\r` must survive into the JSON `text` field
    byte-for-byte -- matching real `rg --json`'s `lines.text` field for the identical fixture
    (verified via hexdump against `rg.exe` 15.1.0: `{"text":"needle crlf\r\n"}`, i.e. rg keeps
    even its own trailing `\n` there; this engine's schema strips only the `\n`, so the expected
    value here is `"needle crlf\r"`).
    """
    tg_binary = _require_native_tg_binary()
    matches = _run_native_json_search(tg_binary, json_fidelity_corpus)
    crlf_matches = [m for m in matches if m["file"].endswith("crlf.txt")]
    assert len(crlf_matches) == 1, f"expected exactly one crlf.txt match, got {crlf_matches}"
    assert crlf_matches[0]["text"] == "needle crlf\r", (
        f"expected the source line's trailing \\r preserved, got {crlf_matches[0]['text']!r}"
    )


def test_native_json_preserves_invalid_utf8_losslessly(json_fidelity_corpus: Path) -> None:
    """A source line that is not valid UTF-8 must reach `--json` output losslessly -- never
    silently replaced with U+FFFD (`\\xef\\xbf\\xbd`). The fixed emitter reports it via a
    base64 `bytes` fallback field instead of `text`, mirroring real `rg --json`'s own
    `lines.bytes` fallback for exactly this case (verified via hexdump against `rg.exe`
    15.1.0: `{"bytes":"Y2Fm6SBuZWVkbGUK"}`, which base64-decodes to this fixture's own raw
    bytes, `caf\xe9 needle\n`).
    """
    tg_binary = _require_native_tg_binary()
    matches = _run_native_json_search(tg_binary, json_fidelity_corpus)
    latin1_matches = [m for m in matches if m["file"].endswith("latin1.txt")]
    assert len(latin1_matches) == 1, f"expected exactly one latin1.txt match, got {latin1_matches}"
    match = latin1_matches[0]

    assert not match.get("text"), (
        f"invalid-UTF-8 content must not be reported via a `text` string (that would require "
        f"lossy re-encoding); got text={match.get('text')!r}"
    )
    assert match.get("bytes"), f"expected a base64 `bytes` fallback field, got {match}"

    decoded = base64.b64decode(match["bytes"])
    assert decoded == b"caf\xe9 needle", (
        f"decoded bytes must exactly match the source line's raw content (terminating \\n "
        f"stripped, matching the `text` field's own convention), got {decoded!r}"
    )
    # The corrupting substitution this fix removes: U+FFFD's own UTF-8 encoding must never
    # appear anywhere in the recovered bytes.
    assert b"\xef\xbf\xbd" not in decoded


# --- Multi-pattern coverage (`-e PAT -e PAT`) -----------------------------------------------
#
# `tg search -e A -e B --json PATH` is a DIFFERENT code path from the single-pattern searches
# above: `collect_native_multi_pattern_matches` (main.rs), reached directly from a normal user
# invocation whenever more than one `-e`/`--regexp` pattern is given -- confirmed by reading
# `main.rs`'s `BackendSelection::NativeCpu` branch, which calls it unconditionally whenever
# `request.patterns.len() > 1`, no experimental flag or GPU path required. It has two internal
# branches, both of which used to build a `SearchMatchJson.text: String` directly from a lossy
# or (post this PR's rename) mismatched-type source:
#   * the FAST path (`--fixed-strings` + a handful of other narrow preconditions), sourced from
#     `NativeMultiPatternMatch` -- previously fed `SearchMatchJson.text` via
#     `String::from_utf8_lossy`, the exact same defect class as the single-pattern emitter.
#   * the SLOW path (the default -- no `--fixed-strings`), sourced from `NativeSearchMatch` via
#     `execute_native_search` -- this is the exact site that failed to compile once
#     `NativeSearchMatch.text: String` became `raw: Vec<u8>` elsewhere in this fix, which is what
#     surfaced this whole gap: `SearchMatchJson` needed the identical `text`/`bytes` treatment,
#     not just a type-coercing wrapper.
# Both are covered below, over the identical CRLF/invalid-UTF-8 fixture the single-pattern tests
# use, so a regression in either branch is caught the same way.


def _run_native_json_multi_pattern_search(
    tg_binary: Path, corpus: Path, *, fixed_strings: bool
) -> list[dict]:
    argv = [str(tg_binary), "search", "--json"]
    if fixed_strings:
        argv.append("--fixed-strings")
    argv.extend(["-e", "needle", "-e", "zzz_never_matches_zzz", str(corpus)])
    proc = subprocess.run(
        argv,
        cwd=corpus,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"native multi-pattern --json search failed: rc={proc.returncode} "
        f"stderr={proc.stderr!r} stdout={proc.stdout!r}"
    )
    payload = json.loads(proc.stdout)
    return payload["matches"]


@pytest.mark.parametrize("fixed_strings", [False, True], ids=["slow-path", "fast-path"])
def test_native_json_multi_pattern_preserves_crlf_trailing_cr(
    json_fidelity_corpus: Path, fixed_strings: bool
) -> None:
    """Same assertion as `test_native_json_preserves_crlf_trailing_cr`, but through the
    multi-pattern (`-e`/`-e`) dispatch -- `fixed_strings=False` exercises
    `collect_native_multi_pattern_matches`'s SLOW branch (`NativeSearchMatch`, the site that
    failed to compile), `fixed_strings=True` exercises its FAST branch (`NativeMultiPatternMatch`).
    """
    tg_binary = _require_native_tg_binary()
    matches = _run_native_json_multi_pattern_search(
        tg_binary, json_fidelity_corpus, fixed_strings=fixed_strings
    )
    crlf_matches = [m for m in matches if m["file"].endswith("crlf.txt")]
    assert len(crlf_matches) == 1, f"expected exactly one crlf.txt match, got {crlf_matches}"
    assert crlf_matches[0]["text"] == "needle crlf\r", (
        f"expected the source line's trailing \\r preserved, got {crlf_matches[0]['text']!r}"
    )


@pytest.mark.parametrize("fixed_strings", [False, True], ids=["slow-path", "fast-path"])
def test_native_json_multi_pattern_preserves_invalid_utf8_losslessly(
    json_fidelity_corpus: Path, fixed_strings: bool
) -> None:
    """Same assertion as `test_native_json_preserves_invalid_utf8_losslessly`, but through the
    multi-pattern (`-e`/`-e`) dispatch. See
    `test_native_json_multi_pattern_preserves_crlf_trailing_cr` for which branch each
    `fixed_strings` value exercises.
    """
    tg_binary = _require_native_tg_binary()
    matches = _run_native_json_multi_pattern_search(
        tg_binary, json_fidelity_corpus, fixed_strings=fixed_strings
    )
    latin1_matches = [m for m in matches if m["file"].endswith("latin1.txt")]
    assert len(latin1_matches) == 1, f"expected exactly one latin1.txt match, got {latin1_matches}"
    match = latin1_matches[0]

    assert not match.get("text"), (
        f"invalid-UTF-8 content must not be reported via a `text` string (that would require "
        f"lossy re-encoding); got text={match.get('text')!r}"
    )
    assert match.get("bytes"), f"expected a base64 `bytes` fallback field, got {match}"

    decoded = base64.b64decode(match["bytes"])
    assert decoded == b"caf\xe9 needle", (
        f"decoded bytes must exactly match the source line's raw content, got {decoded!r}"
    )
    assert b"\xef\xbf\xbd" not in decoded
