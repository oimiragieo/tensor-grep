import importlib
import re
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map

_MODULES = [
    "lang_c",
    "lang_cpp",
    "lang_csharp",
    "lang_go",
    "lang_java",
    "lang_php",
    "lang_registry",
    "repo_map",
]
_CLI = Path(repo_map.__file__).parent


def _mod(name: str):
    return importlib.import_module(f"tensor_grep.cli.{name}")


def test_every_copy_is_discovered_and_byte_identical() -> None:
    found = sorted(
        p.stem
        for p in _CLI.glob("*.py")
        if re.search(r"^_CLEAN_SYMBOL_NAME_RE = ", p.read_text(encoding="utf-8"), re.M)
    )
    assert found == sorted(_MODULES)  # a new copy must be added to the pin
    patterns = {
        (_mod(n)._CLEAN_SYMBOL_NAME_RE.pattern, _mod(n)._CLEAN_SYMBOL_NAME_RE.flags)
        for n in _MODULES
    }
    assert len(patterns) == 1


# Explicit escapes so precomposed / decomposed / non-ASCII fixtures cannot look identical.
_VALID = [
    "gr\u00f6\u00dfe",
    "Caf\u00e9",
    "cafe\u0301",  # e + U+0301 COMBINING ACUTE ACCENT: Mn is XID_Continue
    "\u2118x",  # U+2118 SCRIPT CAPITAL P: Other_ID_Start, category Sm (not \w)
    "x\u2160",  # U+2160 ROMAN NUMERAL ONE: Nl is XID_Continue
    "\u540d\u524d",
    "_x",
    "$x",
    "a$b",
    "a1",
]
_INVALID = [
    "a\u00b2",  # U+00B2 SUPERSCRIPT TWO: \w matches it, but it is not XID_Continue
    "\u00b2abc",
    "\u00bdabc",
    "\u0663abc",
    "1abc",
    "",
    "a-b",
    "a b",
    "a.b",
    "a::b",
]


@pytest.mark.parametrize("name", _MODULES)
@pytest.mark.parametrize("ident", _VALID)
def test_regex_prefilter_accepts_valid_identifiers(name: str, ident: str) -> None:
    assert _mod(name)._CLEAN_SYMBOL_NAME_RE.match(ident) is not None


@pytest.mark.parametrize("name", _MODULES)
@pytest.mark.parametrize("ident", ["", "a-b", "a b", "a.b", "a::b", "a\tb"])
def test_regex_prefilter_rejects_structural_non_names(name: str, ident: str) -> None:
    assert _mod(name)._CLEAN_SYMBOL_NAME_RE.match(ident) is None


@pytest.mark.parametrize("ident", _VALID)
def test_shared_predicate_accepts_valid_identifiers(ident: str) -> None:
    from tensor_grep.cli import lang_registry

    assert lang_registry.is_clean_symbol_name(ident) is True


@pytest.mark.parametrize("ident", _INVALID)
def test_shared_predicate_rejects_invalid_identifiers(ident: str) -> None:
    from tensor_grep.cli import lang_registry

    assert lang_registry.is_clean_symbol_name(ident) is False


@pytest.mark.parametrize("ident", [*_VALID, *_INVALID])
def test_shared_predicate_agrees_with_str_isidentifier(ident: str) -> None:
    # `$` is the one language-allowed extra character; everything else is Python's XID rule.
    from tensor_grep.cli import lang_registry

    expected = bool(ident) and ident.replace("$", "_").isidentifier()
    assert lang_registry.is_clean_symbol_name(ident) is expected


@pytest.mark.parametrize("name", _MODULES)
@pytest.mark.parametrize("ident", [*_VALID, *_INVALID])
def test_every_module_wrapper_matches_the_shared_predicate(name: str, ident: str) -> None:
    from tensor_grep.cli import lang_registry

    module = _mod(name)
    if not hasattr(module, "_is_clean_symbol_name"):
        pytest.skip("module has no wrapper")
    assert module._is_clean_symbol_name(ident) is lang_registry.is_clean_symbol_name(ident)


@pytest.mark.parametrize(
    ("filename", "source", "symbol"),
    [
        ("u.c", "void größe(void) {}\n", "größe"),
        ("u.cpp", "void größe() {}\n", "größe"),
        ("U.java", "public class Café { void größe() {} }\n", "größe"),
        ("u.go", "package p\n\nfunc größe() {}\n", "größe"),
        ("u.cs", "class Café { void Größe() {} }\n", "Größe"),
        ("u.php", "<?php\nfunction größe() {}\n", "größe"),
        ("u.js", "function größe() {}\n", "größe"),
    ],
)
def test_non_ascii_symbol_defs_resolve(
    tmp_path: Path, filename: str, source: str, symbol: str
) -> None:
    target = tmp_path / filename
    target.write_text(source, encoding="utf-8")
    payload = repo_map.build_symbol_defs(symbol, str(target))
    assert len(payload["definitions"]) == 1


def _fallback_names(tmp_path: Path, source: str, suffix: str = ".js") -> list[str]:
    target = tmp_path / f"fixture{suffix}"
    target.write_text(source, encoding="utf-8")
    _imports, symbols = repo_map._regex_imports_and_symbols(target)
    return [s["name"] for s in symbols]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("function caf\u00e9() {}\n", ["caf\u00e9"]),  # precomposed U+00E9
        ("function cafe\u0301() {}\n", ["cafe\u0301"]),  # decomposed: whole name, never `cafe`
        ("function \u2118x() {}\n", ["\u2118x"]),
        ("function x\u2160() {}\n", ["x\u2160"]),
        ("class \u540d\u524d {}\n", ["\u540d\u524d"]),
        ("function a\u00b2() {}\n", []),  # invalid continuation: no symbol, not `a`
        ("function \u00b2abc() {}\n", []),
        ("function foo/*c*/() {}\n", ["foo"]),
        ("function foo_bar1() {}\n", ["foo_bar1"]),  # ASCII control: unchanged
        ("const handler = async () => 1;\n", ["handler"]),
    ],
)
def test_regex_fallback_extracts_exact_whole_names(
    tmp_path: Path, source: str, expected: list[str]
) -> None:
    assert _fallback_names(tmp_path, source) == expected


def test_regex_fallback_rust_whole_names_and_delimiters(tmp_path: Path) -> None:
    source = "pub fn caf\u00e9<T>(x: T) {}\nstruct Foo;\nfn a\u00b2() {}\n"
    assert _fallback_names(tmp_path, source, ".rs") == ["caf\u00e9", "Foo"]
