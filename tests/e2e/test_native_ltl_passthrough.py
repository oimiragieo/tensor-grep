"""Full-path belt: `tg search --ltl` must reach the Python sidecar through the REAL native
release binary, not just through `CliRunner` (task #883, 2026-08-01 backlog campaign).

WHY THIS FILE EXISTS
--------------------
`--ltl` is a Python-side temporal query (``CPUBackend._search_ltl``). The native front door
(``rust_core/src/main.rs``) only forwards a search to the Python sidecar when the flag appears in
``SEARCH_PYTHON_PASSTHROUGH_FLAGS``. Before this fix, ``--ltl`` was absent from that allowlist, so
a user running the **native-frontdoor binary asset** (not the pip-installed entry point, which
intercepts through ``bootstrap.py`` before clap ever sees argv) got a clap "unrecognized flag"
rejection instead of a working search. `tests/unit/test_cli_modes.py` and
`tests/e2e/test_routing_parity.py` cannot see this: the former never touches the compiled binary at
all (CliRunner invokes the Typer app in-process), and the latter's
``_skip_if_native_binary_missing`` SKIPS whenever the binary is absent -- `test-python` never builds
one, so that suite is a check that cannot fail, pre-fix or post-fix (round-1 audit MF4).

This suite is named ``test_native_*.py`` deliberately: ``ci.yml``'s ``native-build-smoke`` job runs
that glob with ``TG_REQUIRE_RG_PARITY=1`` (the job's own established convention for "this suite
needs the real compiled ``tg`` binary" -- reused here rather than inventing a second marker,
mirroring ``test_native_renderer_file_set_invariant.py::_require_native_tg``, which also needs no
``rg`` and still gates on the same env var), which turns a missing binary from a silent skip into a
hard failure, and ``tests/unit/test_native_e2e_ci_coverage_contract.py`` asserts that coverage
cannot silently lapse for any file that references the marker.

Pre-fix, this test is RED: the native binary clap-rejects the unknown ``--ltl`` flag with a nonzero
exit and a "unexpected argument" diagnostic on stderr, never reaching the Python sidecar.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _helpers():
    spec = importlib.util.find_spec("helpers.rg_parity")
    assert spec is not None, "tests/helpers/rg_parity.py must be importable"
    return importlib.import_module("helpers.rg_parity")


def _require_native_tg():
    """Resolve the native `tg`, or SKIP -- LOUDLY when the caller demanded coverage.

    Mirrors `test_native_renderer_file_set_invariant.py::_require_native_tg` exactly: this suite
    needs only the compiled binary, no `rg`, so it reuses the same `TG_REQUIRE_RG_PARITY` marker
    the `native-build-smoke` job already sets rather than introducing a second one.
    """
    helpers = _helpers()
    required = os.environ.get("TG_REQUIRE_RG_PARITY", "").strip().lower() in {"1", "true", "yes"}
    tg_binary = helpers.resolve_native_tg_binary()
    if tg_binary is None:
        message = "--ltl native-passthrough guard needs the native tg binary (cargo build --release in rust_core/)"
        if required:
            pytest.fail(f"TG_REQUIRE_RG_PARITY=1 but {message}")
        pytest.skip(message)
    return tg_binary


def test_native_binary_routes_ltl_query_to_python_sidecar(tmp_path: Path) -> None:
    tg_binary = _require_native_tg()

    fixture = tmp_path / "sequence.log"
    fixture.write_text("open request\nmid line\nclose request\n", encoding="utf-8")

    result = subprocess.run(
        [str(tg_binary), "search", "open -> eventually close", "--ltl", str(fixture)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, (
        f"--ltl through the native binary must succeed, not clap-reject the flag\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "open request" in result.stdout, result.stdout
    assert "close request" in result.stdout, result.stdout


def test_native_binary_gives_clean_error_for_invalid_ltl_query(tmp_path: Path) -> None:
    """Same fix, over the real front door: an invalid --ltl query is a clean exit-2 error, never
    a traceback -- covers Task 3's fix through the native binary in addition to CliRunner.

    NOT A VACUOUS CHECK, ASSERTED: clap's own "unexpected argument '--ltl'" rejection ALSO exits 2
    with no traceback pre-fix (when `--ltl` is entirely absent from
    `SEARCH_PYTHON_PASSTHROUGH_FLAGS`, this query never reaches the Python sidecar at all) -- so
    `returncode == 2` alone cannot discriminate "the fix's clean-error path ran" from "clap rejected
    the flag before Python ever saw it". The stderr-shape assertions below are what discriminates:
    pre-fix stderr is clap's "unexpected argument '--ltl' found" (no mention of LTL grammar);
    post-fix stderr is `Error: Unsupported LTL query. Use: 'A -> eventually B'` (Task 3's
    `_exit_search_error`, never clap's usage banner).
    """
    tg_binary = _require_native_tg()

    fixture = tmp_path / "sequence.log"
    fixture.write_text("open request\nclose request\n", encoding="utf-8")

    result = subprocess.run(
        [str(tg_binary), "search", "not valid ltl", "--ltl", str(fixture)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2, (
        f"invalid --ltl query must exit 2 with a clean error\nstdout={result.stdout!r}\n"
        f"stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stdout and "Traceback" not in result.stderr
    assert "unexpected argument" not in result.stderr, (
        "clap rejected --ltl outright -- the query never reached the Python sidecar's "
        f"clean-error path, so this is NOT the fix under test\nstderr={result.stderr!r}"
    )
    assert "Unsupported LTL query" in result.stderr, (
        f"expected Task 3's clean-error message on stderr, got: {result.stderr!r}"
    )
