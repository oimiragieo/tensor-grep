"""No Rust JSON payload may hardcode the schema version (DC-003, 2026-08-19).

`rust_core/src/main.rs` stamps the wire-schema version into JSON payloads. Seventeen
sites do it correctly, via the `JSON_OUTPUT_VERSION` constant. One site --
the `unsupported_flag` error payload -- wrote `"version": 1` and
`"schema_version": 1` as literals.

That is the same defect class as DC-001 on the Python side, with a narrower blast
radius: the day `JSON_OUTPUT_VERSION` is bumped, seventeen payloads report the new
version and this one keeps reporting the old one, silently. An error payload is a
poor place to lose version fidelity, because it is exactly what a consumer parses
when it is already confused.

WHY THIS TEST IS PYTHON AND NOT RUST
------------------------------------
It is a source-shape rule, not a behaviour: no runtime path can observe "was this
literal or a constant?" once compiled. A Rust test would also cost a CI compile to
assert something a parse can settle. The repo already pins CI/YAML shape from
Python governance tests the same way.

WHAT THIS TEST CANNOT DO
------------------------
It is a source census, and this repo's own doctrine is that a census can be
satisfied by a comment. Comment lines are therefore stripped before matching, and
`test_detector_fires_on_a_planted_literal` proves the matcher discriminates
rather than trusting the zero.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUST_MAIN = REPO_ROOT / "rust_core" / "src" / "main.rs"

#: A JSON-payload key whose value must be the constant, never a bare integer.
_VERSION_KEY_LITERAL = re.compile(r'"(?:schema_version|version)"\s*:\s*(\d+)')
_CONSTANT_NAME = "JSON_OUTPUT_VERSION"


def _significant_lines(source: str) -> list[tuple[int, str]]:
    """Numbered lines with `//` comments stripped.

    A comment mentioning `"version": 1` while explaining this very rule must not
    fail the rule -- the documented "a grep hit can be the fix's own
    documentation" trap.
    """
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(source.splitlines(), start=1):
        code = raw.split("//", 1)[0]
        if code.strip():
            out.append((number, code))
    return out


def _literal_version_sites(source: str) -> list[tuple[int, str]]:
    return [
        (number, line.strip())
        for number, line in _significant_lines(source)
        if _VERSION_KEY_LITERAL.search(line)
    ]


def test_positive_control_the_source_is_readable_and_uses_the_constant() -> None:
    """Guard the guard: a file that failed to load, or a constant that was renamed,
    would make the emptiness check below vacuous rather than failing it."""
    assert RUST_MAIN.is_file(), f"{RUST_MAIN} missing -- run from a dev checkout"
    source = RUST_MAIN.read_text(encoding="utf-8")
    uses = source.count(_CONSTANT_NAME)
    assert uses > 5, (
        f"only {uses} references to {_CONSTANT_NAME}; the constant was probably "
        "renamed, which would silently make this whole suite meaningless."
    )


def test_detector_fires_on_a_planted_literal() -> None:
    """Mutation control. Without it, a regex that stopped matching would report
    the file clean forever."""
    planted = 'let payload = json!({\n    "version": 7,\n});\n'
    hits = _literal_version_sites(planted)
    assert len(hits) == 1 and hits[0][0] == 2, hits


def test_detector_ignores_a_literal_inside_a_comment() -> None:
    """The other direction: documenting the rule must not violate it."""
    commented = '// never write "version": 1 here; use JSON_OUTPUT_VERSION\nlet x = 1;\n'
    assert _literal_version_sites(commented) == []


def test_detector_accepts_the_constant_form() -> None:
    """The compliant shape must not be flagged, or the rule is unsatisfiable."""
    compliant = 'json!({\n    "version": JSON_OUTPUT_VERSION,\n});\n'
    assert _literal_version_sites(compliant) == []


def test_no_hardcoded_version_literals_in_rust_json_payloads() -> None:
    """The live rule."""
    sites = _literal_version_sites(RUST_MAIN.read_text(encoding="utf-8"))
    assert sites == [], (
        "hardcoded schema-version literal(s) in a Rust JSON payload -- these go "
        f"stale the day {_CONSTANT_NAME} is bumped, while every sibling payload "
        "updates:\n" + "\n".join(f"  rust_core/src/main.rs:{n}: {t}" for n, t in sites)
    )
