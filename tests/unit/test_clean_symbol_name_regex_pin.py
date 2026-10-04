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


@pytest.mark.parametrize("name", _MODULES)
@pytest.mark.parametrize("ident", ["größe", "Café", "名前", "_x", "$x", "a1"])
def test_unicode_and_ascii_identifiers_accepted(name: str, ident: str) -> None:
    assert _mod(name)._CLEAN_SYMBOL_NAME_RE.match(ident) is not None


@pytest.mark.parametrize("name", _MODULES)
@pytest.mark.parametrize("ident", ["1abc", "a-b", "a b", "", "a.b", "a::b", "٣abc"])
def test_non_identifiers_rejected(name: str, ident: str) -> None:
    assert _mod(name)._CLEAN_SYMBOL_NAME_RE.match(ident) is None


@pytest.mark.parametrize("ident", ["²abc", "½abc", "٣abc", "1abc", ""])
def test_shared_predicate_rejects_non_xid_start(ident: str) -> None:
    # council wave-2a r4: `[^\W\d]` admits No-category numerics (², ½); the predicate does not
    from tensor_grep.cli import lang_registry

    assert lang_registry.is_clean_symbol_name(ident) is False


@pytest.mark.parametrize("ident", ["größe", "Café", "名前", "_x", "$x", "a1"])
def test_shared_predicate_accepts_identifiers(ident: str) -> None:
    from tensor_grep.cli import lang_registry

    assert lang_registry.is_clean_symbol_name(ident) is True


@pytest.mark.parametrize("name", _MODULES)
@pytest.mark.parametrize("ident", ["²abc", "½abc"])
def test_every_module_wrapper_rejects_non_xid_start(name: str, ident: str) -> None:
    # every per-module `_is_clean_symbol_name` must route through the shared predicate
    if not hasattr(_mod(name), "_is_clean_symbol_name"):
        pytest.skip("module has no wrapper")
    assert _mod(name)._is_clean_symbol_name(ident) is False


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


def test_regex_fallback_does_not_truncate_non_ascii_function_names(tmp_path: Path) -> None:
    target = tmp_path / "a.js"
    target.write_text("function café() {\n  return 1;\n}\n", encoding="utf-8")
    _imports, symbols = repo_map._regex_imports_and_symbols(target)
    assert [s["name"] for s in symbols] == ["café"]


def test_regex_fallback_rejects_non_xid_start_and_decomposed_names(tmp_path: Path) -> None:
    # explicit escapes so the precomposed / decomposed fixtures cannot look identical (r13)
    bad_numeric = tmp_path / "b.js"
    bad_numeric.write_text("function ²abc() {}\n", encoding="utf-8")
    _i, symbols = repo_map._regex_imports_and_symbols(bad_numeric)
    assert symbols == []

    decomposed = tmp_path / "c.js"  # `e` + U+0301 COMBINING ACUTE ACCENT
    decomposed.write_text("function café() {}\n", encoding="utf-8")
    _i, symbols = repo_map._regex_imports_and_symbols(decomposed)
    assert symbols == []  # never the truncated `cafe`

    precomposed = tmp_path / "d.js"  # U+00E9
    precomposed.write_text("function café() {}\n", encoding="utf-8")
    _i, symbols = repo_map._regex_imports_and_symbols(precomposed)
    assert [s["name"] for s in symbols] == ["café"]


def test_regex_fallback_comment_glued_to_name_still_yields_the_name(tmp_path: Path) -> None:
    target = tmp_path / "e.js"
    target.write_text("function foo/*c*/() {}\n", encoding="utf-8")
    _i, symbols = repo_map._regex_imports_and_symbols(target)
    assert [s["name"] for s in symbols] == ["foo"]
