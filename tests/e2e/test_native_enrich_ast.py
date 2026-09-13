"""Full-path belt: `tg search --enrich-ast` must reach the Python sidecar through the REAL native
release binary, not just through `CliRunner` (HUNT-4, docs/BACKLOG.md, 2026-09-13).

WHY THIS FILE EXISTS
--------------------
`--enrich-ast` is a Python-only search flag (`cli/main.py`, consumed at build-capsule time) and is
listed in `bootstrap._TG_ONLY_SEARCH_FLAGS` specifically to force full-Python dispatch through the
pip-installed entry point. But it was entirely absent from `rust_core/src/main.rs` /
`search_flag_registry.rs`'s `SEARCH_PYTHON_PASSTHROUGH_FLAGS`: not a native clap field, not in the
passthrough allowlist, not in the sibling `--ast`/`--files` branch either (that branch is a
different mechanism -- `--ast` being a name-prefix of `--enrich-ast` is a coincidence, not a
reason). Reproduced against the installed `tg.exe`: `tg search "hello" . --enrich-ast` ->
clap `unexpected argument '--enrich-ast' found`, exit 2.

This suite is named `test_native_*.py` deliberately, mirroring `test_native_ltl_passthrough.py`:
`ci.yml`'s `native-build-smoke` job runs that glob with `TG_REQUIRE_RG_PARITY=1`, turning a missing
binary from a silent skip into a hard failure, and
`tests/unit/test_native_e2e_ci_coverage_contract.py` asserts coverage cannot silently lapse for any
file referencing the marker.

Pre-fix, this test is RED: the native binary clap-rejects the unknown `--enrich-ast` flag.
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

    Mirrors `test_native_ltl_passthrough.py::_require_native_tg`: this suite needs only the
    compiled binary, no `rg`, so it reuses the same `TG_REQUIRE_RG_PARITY` marker the
    `native-build-smoke` job already sets rather than introducing a second one.
    """
    helpers = _helpers()
    required = os.environ.get("TG_REQUIRE_RG_PARITY", "").strip().lower() in {"1", "true", "yes"}
    tg_binary = helpers.resolve_native_tg_binary()
    if tg_binary is None:
        message = "--enrich-ast native-passthrough guard needs the native tg binary (cargo build --release in rust_core/)"
        if required:
            pytest.fail(f"TG_REQUIRE_RG_PARITY=1 but {message}")
        pytest.skip(message)
    return tg_binary


def test_native_binary_routes_enrich_ast_to_python_sidecar(tmp_path: Path) -> None:
    tg_binary = _require_native_tg()

    fixture = tmp_path / "sample.py"
    fixture.write_text("def hello():\n    return 'hello'\n", encoding="utf-8")

    result = subprocess.run(
        [str(tg_binary), "search", "hello", str(tmp_path), "--enrich-ast"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    # THE PROPERTY UNDER TEST IS ROUTING, NOT EVALUATION -- the native front door must no longer
    # clap-reject `--enrich-ast`; it must forward to the Python sidecar. Whether that sidecar can
    # then fully evaluate the query is out of scope here (mirrors test_native_ltl_passthrough.py's
    # reasoning for why this split matters).
    assert "unexpected argument" not in result.stderr, (
        "the native front door clap-REJECTED --enrich-ast -- exactly the HUNT-4 defect\n"
        f"stderr={result.stderr!r}"
    )
    assert result.returncode != 2 or "unexpected argument" not in result.stderr, (
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
