"""The content-addressed AST parse cache (`_cached_ast_parse`) collapses the 2-3x duplicate Python
file parses `build_agent_capsule` does across phases -- the map-build imports/symbols pass and the
caller/blast-radius consumer scan both `ast.parse` the SAME source. Profile 2026-07-11: ~40% of
`tg agent` wall was `ast.parse` + `ast.walk` over those duplicate parses (1512 parses / ~783 files).
Keying on source TEXT (not path) keeps it staleness-free under the reused session-daemon process.
"""

import ast
from pathlib import Path

from tensor_grep.cli.repo_map import _cached_ast_parse, _python_imports_and_symbols


def test_identical_source_is_parsed_once_and_shared() -> None:
    _cached_ast_parse.cache_clear()
    src = "import os\nfrom a.b import c\n\n\ndef f():\n    return c\n"
    first = _cached_ast_parse(src)
    second = _cached_ast_parse(src)
    assert first is second  # same tree object -> parsed once, shared read-only
    info = _cached_ast_parse.cache_info()
    assert info.misses == 1 and info.hits == 1


def test_cached_parse_is_byte_identical_to_ast_parse() -> None:
    _cached_ast_parse.cache_clear()
    src = "import os\nfrom a.b import c as d\n\n\nclass K:\n    def m(self):\n        return 1\n"
    assert ast.dump(_cached_ast_parse(src)) == ast.dump(ast.parse(src))


def test_distinct_sources_get_distinct_trees() -> None:
    _cached_ast_parse.cache_clear()
    assert _cached_ast_parse("import os\n") is not _cached_ast_parse("import sys\n")


def test_reparsing_the_same_unchanged_file_hits_the_cache(tmp_path: Path) -> None:
    # The whole point: the map-build pass and the caller/blast scan both parse the same file. A
    # second extraction of identical content must be a cache HIT (no new miss), not a re-parse.
    _cached_ast_parse.cache_clear()
    module = tmp_path / "m.py"
    module.write_text(
        "import os\nfrom pkg import thing\n\n\ndef go():\n    return thing\n", encoding="utf-8"
    )
    _python_imports_and_symbols(module)
    misses_after_first = _cached_ast_parse.cache_info().misses
    _python_imports_and_symbols(module)  # same source read again
    info = _cached_ast_parse.cache_info()
    assert info.misses == misses_after_first  # NOT re-parsed
    assert info.hits >= 1
