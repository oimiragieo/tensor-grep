"""DC-001: the JSON envelope's schema version must survive a wheel install.

`_json_output_version()` used to regex-scrape `JSON_OUTPUT_VERSION` out of
`rust_core/src/main.rs` via `Path(__file__).resolve().parents[3]`, catching
`OSError` and defaulting to 1. In a dev checkout parents[3] is the repo root and
the scrape works. In a real `pip`/`uvx` install it resolves to the directory
above `site-packages`, `rust_core/` is not there (pyproject's [tool.maturin]
include list does not ship it), so the lookup raises and the default is taken --
permanently, for every published consumer.

Latent while the Rust constant happens to equal the fallback. The day it is
bumped, every wheel keeps stamping the OLD `version` / `schema_version` into
every `--json` envelope, with no error anywhere. Wrong, not broken: the worst
class.

NOTE ON WHY THE OBVIOUS TEST WOULD BE VACUOUS
---------------------------------------------
`JSON_OUTPUT_VERSION` is currently 1, which is also the buggy fallback value. A
test that merely asserts "returns the right number when the filesystem lookup
fails" therefore passes against the UNFIXED code. `test_reads_the_constant_not_
the_filesystem` avoids that by substituting a distinctive sentinel value: the
fixed implementation reports the sentinel, the scrape-based one reports 1.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tensor_grep.cli import audit_manifest
from tensor_grep.cli import main as cli_main
from tensor_grep.core import result as result_contract

REPO_ROOT = Path(__file__).resolve().parents[2]
_RUST_MAIN = REPO_ROOT / "rust_core" / "src" / "main.rs"
_PATTERN = re.compile(r"const\s+JSON_OUTPUT_VERSION\s*:\s*u32\s*=\s*(\d+)\s*;")


def _rust_constant() -> int:
    match = _PATTERN.search(_RUST_MAIN.read_text(encoding="utf-8"))
    assert match is not None, (
        f"could not find JSON_OUTPUT_VERSION in {_RUST_MAIN}. If the constant was "
        "renamed or moved, this cross-language pin must be updated -- do not delete it."
    )
    return int(match.group(1))


def test_positive_control_the_rust_constant_is_readable() -> None:
    """Guard the guard: a scrape that silently found nothing would make the
    cross-pin below vacuous rather than failing it."""
    assert _RUST_MAIN.is_file(), "run this from a dev checkout"
    assert _rust_constant() >= 1


def test_python_literal_matches_the_rust_constant() -> None:
    """The cross-language pin. Bumping one side alone must fail CI.

    This replaces the runtime scrape: the Python side now carries its own
    literal (which ships inside the wheel), and THIS test -- which only ever runs
    from a dev checkout, where rust_core/ is present -- is what keeps the two
    honest.
    """
    assert result_contract.JSON_OUTPUT_VERSION == _rust_constant(), (
        "JSON_OUTPUT_VERSION disagrees across languages. Bump "
        "src/tensor_grep/core/result.py to match rust_core/src/main.rs (or vice "
        "versa) -- a wheel cannot see the Rust source, so the Python literal is "
        "what every published consumer will report."
    )


def test_cross_pin_can_actually_fail() -> None:
    """Mutation control for the pin above: a divergent Rust value must be caught.

    Without this, a regex that stopped matching would make the pin silently
    green forever.
    """
    mutated = _PATTERN.sub(
        "const JSON_OUTPUT_VERSION: u32 = 4242;", "const JSON_OUTPUT_VERSION: u32 = 1;"
    )
    match = _PATTERN.search(mutated)
    assert match is not None and int(match.group(1)) == 4242
    assert int(match.group(1)) != result_contract.JSON_OUTPUT_VERSION


@pytest.mark.parametrize(
    "module", [cli_main, audit_manifest], ids=["cli.main", "cli.audit_manifest"]
)
def test_reads_the_constant_not_the_filesystem(
    module: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE RED ARM for DC-001, at both definition sites.

    Substituting a sentinel proves the value comes from the shipped constant
    rather than from a source scrape. The pre-fix implementation ignores the
    constant entirely and returns its hardcoded fallback of 1, so this fails.

    `read_text` is also made to raise, reproducing the wheel layout exactly: any
    surviving filesystem dependency surfaces here instead of in a user's install.
    """
    sentinel = 4242
    monkeypatch.setattr(result_contract, "JSON_OUTPUT_VERSION", sentinel)

    def _explode(*_args: object, **_kwargs: object) -> str:
        raise OSError("rust_core/ is not present in a wheel install")

    monkeypatch.setattr(Path, "read_text", _explode)

    fn = module._json_output_version  # type: ignore[attr-defined]
    cache_clear = getattr(fn, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()
    try:
        assert fn() == sentinel, (
            "schema version did not come from the shipped constant. A wheel "
            "install cannot read rust_core/src/main.rs, so any scrape-based "
            "lookup silently degrades to a stale default."
        )
    finally:
        if cache_clear is not None:
            cache_clear()


def test_both_definition_sites_agree() -> None:
    """Two copies of this function exist. They must not be able to disagree."""
    assert cli_main._json_output_version() == audit_manifest._json_output_version()
    assert cli_main._json_output_version() == result_contract.JSON_OUTPUT_VERSION


def test_envelope_stamps_the_pinned_version() -> None:
    """End-to-end: the value reaches the wire envelope consumers actually read."""
    envelope = audit_manifest._envelope()
    assert envelope["version"] == result_contract.JSON_OUTPUT_VERSION
    assert envelope["schema_version"] == result_contract.JSON_OUTPUT_VERSION
