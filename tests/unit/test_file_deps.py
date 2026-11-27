"""TDD for the #74 capability: `tg imports FILE` / `tg importers FILE [ROOT]`.

The scoped file-dependency primitive that closes the P4 benchmark gap (docs/benchmarks.md):
`tg map` alone made tg ~10x WORSE than grep on file-dependency lookups because it had no
primitive scoped to a single file. These commands answer "what does this file import"
(forward, O(1) parse) and "who imports this file" (reverse, bounded repo scan) directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import bootstrap, repo_map, session_store
from tensor_grep.cli.commands import KNOWN_COMMANDS
from tensor_grep.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------------------------
# Registration: both names wired at all 4 sites (miss one -> `tg imports x.py` greps the
# literal string "imports" via ripgrep instead of routing to the handler).
# ---------------------------------------------------------------------------------------------


def test_imports_and_importers_registered_in_known_commands() -> None:
    assert "imports" in KNOWN_COMMANDS
    assert "importers" in KNOWN_COMMANDS


def test_bootstrap_routes_imports_to_full_cli_not_rg(monkeypatch, tmp_path: Path) -> None:
    """CliRunner bypasses the bootstrap front door (bootstrap.py:285) -- this tests the ACTUAL
    router: `first_arg in _KNOWN_COMMANDS` must send `tg imports <file>` to the full Typer CLI,
    never to rg passthrough (which would search for the literal string "imports")."""
    target = tmp_path / "a.py"
    target.write_text("import os\n", encoding="utf-8")
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "imports", str(target)])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_a, **_k: pytest.fail("rg passthrough must not run for `tg imports`"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_bootstrap_routes_importers_to_full_cli_not_rg(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("import os\n", encoding="utf-8")
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "importers", str(target)])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_a, **_k: pytest.fail("rg passthrough must not run for `tg importers`"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


# ---------------------------------------------------------------------------------------------
# Forward resolution: relative, require+index-probe, external, Python dotted.
# ---------------------------------------------------------------------------------------------


def test_build_file_imports_resolves_relative_js_import(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    util_path = src / "util.js"
    util_path.write_text("export function foo() {}\n", encoding="utf-8")
    consumer = src / "consumer.js"
    consumer.write_text('import { foo } from "./util";\n', encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    assert payload["result_incomplete"] is False
    assert len(payload["imports"]) == 1
    entry = payload["imports"][0]
    assert entry["module"] == "./util"
    assert entry["line"] == 1
    assert entry["resolved"] == str(util_path.resolve())
    assert entry["external"] is False
    assert entry["resolution_confidence"] == pytest.approx(1.0)
    assert payload["resolved_files"] == [str(util_path.resolve())]
    assert payload["external_modules"] == []
    assert payload["unresolved"] == []


def test_build_file_imports_resolves_require_via_index_probe(tmp_path: Path) -> None:
    """`require('./router')` resolving to `router/index.js` -- the express-style index probe."""
    project = tmp_path / "project"
    router_dir = project / "lib" / "router"
    router_dir.mkdir(parents=True)
    index_path = router_dir / "index.js"
    index_path.write_text("module.exports = {};\n", encoding="utf-8")
    consumer = project / "lib" / "app.js"
    consumer.write_text('const router = require("./router");\n', encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    entry = payload["imports"][0]
    assert entry["module"] == "./router"
    assert entry["resolved"] == str(index_path.resolve())


def test_build_file_imports_classifies_bare_specifier_as_external(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    consumer = project / "app.js"
    consumer.write_text('import express from "express";\n', encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    entry = payload["imports"][0]
    assert entry["module"] == "express"
    assert entry["resolved"] is None
    assert entry["external"] is True
    assert payload["external_modules"] == ["express"]
    assert payload["unresolved"] == []


def test_build_file_imports_resolves_python_dotted_import(tmp_path: Path) -> None:
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text("import pkg.helpers\n", encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "pkg.helpers")
    assert entry["resolved"] == str(helpers_path.resolve())
    assert entry["external"] is False


def test_build_file_imports_resolves_python_relative_import(tmp_path: Path) -> None:
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text("from . import helpers\n", encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    entry = payload["imports"][0]
    assert entry["module"] == "helpers"
    assert entry["resolved"] == str(helpers_path.resolve())
    assert entry["provenance"] == ["relative"]


# ---------------------------------------------------------------------------------------------
# Nested-scope import STATEMENTS (function-scoped / conditional-scoped): `_python_imports_with_lines`
# used to walk only `tree.body` (module top-level statements), so a plain `import`/`from ... import`
# statement written inside a function body, an `if TYPE_CHECKING:` guard, or a `try`/`except` block
# was invisible to both `tg imports` (forward) and the `tg importers` CONFIRM step (reverse) --
# silently, with `result_incomplete` staying False. The dynamic-import-call detector
# (`_python_dynamic_import_entries`) already walked the whole tree for `__import__`/`import_module(...)`
# CALLS; this closes the same gap for plain static import STATEMENTS by switching the extractor's
# main loop from `tree.body` to `ast.walk(tree)`.
# ---------------------------------------------------------------------------------------------


def test_build_file_imports_resolves_function_scoped_import(tmp_path: Path) -> None:
    """A `from pkg.mod import x` written inside a function body (not at module level) must still
    resolve. This is the exact regression shape that motivated the fix: a nested
    `from tensor_grep.perf_guard import write_json` inside `def main()` was invisible to
    `tg imports` before it."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text(
        "def run():\n    from pkg.helpers import foo\n    return foo()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    assert payload["result_incomplete"] is False
    entry = next(current for current in payload["imports"] if current["module"] == "pkg.helpers")
    assert entry["resolved"] == str(helpers_path.resolve())
    assert entry["line"] == 2
    assert entry["external"] is False


def test_build_file_imports_resolves_conditional_type_checking_import(tmp_path: Path) -> None:
    """`if TYPE_CHECKING: import X` (a common type-only-import pattern) lives inside an `If`
    block, not module top-level -- must resolve like any other import."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("class Helper:\n    pass\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text(
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from pkg.helpers import Helper\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    assert payload["result_incomplete"] is False
    entry = next(current for current in payload["imports"] if current["module"] == "pkg.helpers")
    assert entry["resolved"] == str(helpers_path.resolve())
    assert entry["line"] == 4


def test_build_file_imports_resolves_import_inside_try_except_block(tmp_path: Path) -> None:
    """A conditional `try:/except ImportError:` guarded import -- another common real-world
    scope-nesting shape distinct from a function body or `if TYPE_CHECKING:`."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text(
        "try:\n    from pkg.helpers import foo\nexcept ImportError:\n    foo = None\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "pkg.helpers")
    assert entry["resolved"] == str(helpers_path.resolve())
    assert entry["line"] == 2


def test_build_file_imports_module_top_level_and_nested_both_resolve_no_regression(
    tmp_path: Path,
) -> None:
    """Regression guard: a module-top-level import and a function-scoped import of a DIFFERENT
    target in the SAME file must both resolve -- fixing the nested-scope gap must not disturb
    the pre-existing top-level extraction."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    top_target = pkg / "top_helper.py"
    top_target.write_text("def top():\n    return 1\n", encoding="utf-8")
    nested_target = pkg / "nested_helper.py"
    nested_target.write_text("def nested():\n    return 2\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text(
        "from pkg.top_helper import top\n\n"
        "def run():\n"
        "    from pkg.nested_helper import nested\n"
        "    return top() + nested()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    modules = {current["module"] for current in payload["imports"]}
    assert modules == {"pkg.top_helper", "pkg.nested_helper"}
    top_entry = next(c for c in payload["imports"] if c["module"] == "pkg.top_helper")
    nested_entry = next(c for c in payload["imports"] if c["module"] == "pkg.nested_helper")
    assert top_entry["resolved"] == str(top_target.resolve())
    assert top_entry["line"] == 1
    assert nested_entry["resolved"] == str(nested_target.resolve())
    assert nested_entry["line"] == 4


def test_build_file_importers_confirm_step_finds_nested_import_when_prefiltered(
    tmp_path: Path,
) -> None:
    """`tg importers`' CONFIRM step (`_confirm_import_edges`) re-parses each PREFILTERED
    candidate via the same extractor `tg imports` uses. Once a candidate is already in the
    prefilter (here, via its OWN top-level `import pkg.helpers`), a nested import of the same
    target inside a function must ALSO be confirmed as a second edge, not just the top-level
    one -- proving the forward-extractor fix widens the reverse CONFIRM step's recall too."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = pkg / "helpers.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    # Top-level `import pkg.helpers` puts main.py in the alias prefilter for helpers.py; the
    # NESTED `from pkg.helpers import foo` inside run() is the entry this fix newly confirms.
    consumer.write_text(
        "import pkg.helpers\n\ndef run():\n    from pkg.helpers import foo\n    return foo()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_importers(target, project)

    importer_files = set(payload["importer_files"])
    assert str(consumer.resolve()) in importer_files
    lines = sorted(
        int(edge["line"])
        for edge in payload["importers"]
        if edge["file"] == str(consumer.resolve())
    )
    # Both the top-level (line 1) AND the nested (line 4) import must confirm as edges.
    assert lines == [1, 4]


def test_build_file_importers_prefilter_discovers_importer_with_only_nested_import(
    tmp_path: Path,
) -> None:
    """The PREFILTER (`_reverse_importers`, fed by `_python_imports_and_symbols`'s alias-graph
    list) used to build its candidate set from module-top-level imports only -- a file whose
    ONLY import of the target is scope-nested (no top-level import at all) never became a
    CANDIDATE, so the precise CONFIRM step never even got a chance to look at it. This is the
    real-world shape that motivated the fix: a lazy, function-scoped import (e.g. to avoid a
    circular import or defer an expensive load) with no companion top-level import of the same
    package."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = pkg / "helpers.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    # NO top-level import of pkg.helpers anywhere -- the only reference is nested.
    consumer.write_text(
        "def run():\n    from pkg.helpers import foo\n    return foo()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_importers(target, project)

    importer_files = set(payload["importer_files"])
    assert str(consumer.resolve()) in importer_files
    edge = next(
        current for current in payload["importers"] if current["file"] == str(consumer.resolve())
    )
    assert edge["line"] == 2
    assert edge["module"] == "pkg.helpers"


# ---------------------------------------------------------------------------------------------
# Reverse EXACTNESS: 1 real importer + 2 precision traps, both excluded.
# ---------------------------------------------------------------------------------------------


def test_build_file_importers_confirms_real_importer_and_excludes_precision_traps(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    target = src / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")

    real_importer = src / "consumer.js"
    real_importer.write_text('import { foo } from "./util";\n', encoding="utf-8")

    # Trap A: a file that merely MENTIONS the target's stem in a comment/string -- it has no
    # actual import statement, so it must never appear as an importer.
    comment_trap = src / "mentions_stem_only.js"
    comment_trap.write_text(
        '// unrelated to util.js, just says the word here\nexport const label = "util";\n',
        encoding="utf-8",
    )

    # Trap B (the Case-4 false-edge / prefilter over-match bug): imports a DIFFERENT module
    # whose name merely CONTAINS the target's "util" stem as a word fragment. The alias-
    # substring prefilter (`_reverse_importers`) will flag this file as a CANDIDATE, but the
    # precise per-candidate matcher (`_js_ts_module_matches_definition`) must reject it because
    # "./util-helpers" resolves to a different file than "./util".
    (src / "util-helpers.js").write_text("export function helper() {}\n", encoding="utf-8")
    fragment_trap = src / "fragment_trap.js"
    fragment_trap.write_text('import { helper } from "./util-helpers";\n', encoding="utf-8")

    # Trap C: a same-named module ("util.js") from a DIFFERENT directory.
    other_dir = project / "other"
    other_dir.mkdir()
    (other_dir / "util.js").write_text("export function bar() {}\n", encoding="utf-8")
    same_name_trap = other_dir / "consumer.js"
    same_name_trap.write_text('import { bar } from "./util";\n', encoding="utf-8")

    payload = repo_map.build_file_importers(target, project)

    importer_files = set(payload["importer_files"])
    assert importer_files == {str(real_importer.resolve())}
    assert payload["importer_count"] == 1
    assert str(comment_trap.resolve()) not in importer_files
    assert str(fragment_trap.resolve()) not in importer_files
    assert str(same_name_trap.resolve()) not in importer_files

    edge = payload["importers"][0]
    assert edge["file"] == str(real_importer.resolve())
    assert edge["line"] == 1
    assert edge["module"] == "./util"
    assert edge["edge_kind"] == "reverse-import"
    assert edge["kind"] == "import-consumer"


# ---------------------------------------------------------------------------------------------
# Reverse directory-index recall (express@4.21.1 dogfood bug): a bare relative specifier that
# names a DIRECTORY -- `require('./router')` -- resolves, by Node's own directory-index
# convention, to `router/index.js` (the forward `tg imports` side already proves this via
# test_build_file_imports_resolves_require_via_index_probe above). The reverse side used to miss
# it entirely: the coarse alias PREFILTER (`_reverse_importers`, built from
# `_module_aliases_for_path`) never gave `router/index.js` a "router" alias -- every alias it
# generated was anchored on the file's OWN stem ("index"), never its PARENT directory name -- so
# a bare-specifier importer never became a prefilter CANDIDATE and never reached the precise
# per-candidate CONFIRM step (`_js_ts_module_matches_definition`) that would have resolved it
# correctly. `tg importers lib/router/index.js` on express@4.21.1 returned `importer_count: 0`
# despite `lib/application.js`/`lib/express.js` both doing `require('./router')`.
# ---------------------------------------------------------------------------------------------


def test_build_file_importers_finds_directory_index_bare_require_specifier(
    tmp_path: Path,
) -> None:
    """The express@4.21.1 repro: `require('./router')` must be a confirmed importer of
    `router/index.js`, not just an unresolved/invisible reference."""
    project = tmp_path / "project"
    router_dir = project / "lib" / "router"
    router_dir.mkdir(parents=True)
    target = router_dir / "index.js"
    target.write_text("module.exports = {};\n", encoding="utf-8")
    consumer = project / "lib" / "app.js"
    consumer.write_text('var Router = require("./router");\n', encoding="utf-8")

    payload = repo_map.build_file_importers(target, project)

    importer_files = set(payload["importer_files"])
    assert str(consumer.resolve()) in importer_files
    assert payload["importer_count"] == 1
    edge = payload["importers"][0]
    assert edge["file"] == str(consumer.resolve())
    assert edge["line"] == 1
    assert edge["module"] == "./router"
    assert edge["edge_kind"] == "reverse-import"


def test_build_file_importers_finds_directory_index_bare_esm_import_specifier(
    tmp_path: Path,
) -> None:
    """Same directory-index gap, ESM form (`import Router from './router'`) -- the bug report's
    alternate phrasing; proves the fix is not require()-specific."""
    project = tmp_path / "project"
    router_dir = project / "lib" / "router"
    router_dir.mkdir(parents=True)
    target = router_dir / "index.js"
    target.write_text("export default {};\n", encoding="utf-8")
    consumer = project / "lib" / "app.js"
    consumer.write_text('import Router from "./router";\n', encoding="utf-8")

    payload = repo_map.build_file_importers(target, project)

    assert str(consumer.resolve()) in set(payload["importer_files"])


def test_build_file_importers_directory_index_rejects_prefix_false_match(
    tmp_path: Path,
) -> None:
    """Guardrail: `require('./routerX')` must NOT be counted as an importer of
    `router/index.js` -- the new parent-directory alias ("router") must not substring/prefix-
    match a sibling directory/file whose name merely starts with the same characters
    ("routerX" normalizes to "routerx", never "router")."""
    project = tmp_path / "project"
    lib_dir = project / "lib"
    router_dir = lib_dir / "router"
    router_dir.mkdir(parents=True)
    target = router_dir / "index.js"
    target.write_text("module.exports = {};\n", encoding="utf-8")

    # A REAL, distinct decoy file one character away from the directory name.
    decoy = lib_dir / "routerX.js"
    decoy.write_text("module.exports = { decoy: true };\n", encoding="utf-8")
    decoy_consumer = lib_dir / "appx.js"
    decoy_consumer.write_text('var RouterX = require("./routerX");\n', encoding="utf-8")

    payload = repo_map.build_file_importers(target, project)

    assert str(decoy_consumer.resolve()) not in set(payload["importer_files"])
    assert payload["importer_count"] == 0

    # Sanity: the decoy importer's OWN forward resolution still correctly targets routerX.js,
    # not router/index.js -- proving this is a genuine two-different-targets negative, not an
    # accidental "nothing resolves" false negative.
    decoy_imports = repo_map.build_file_imports(decoy_consumer)
    assert decoy_imports["imports"][0]["resolved"] == str(decoy.resolve())


def test_build_file_importers_directory_index_no_double_count_two_specifier_forms(
    tmp_path: Path,
) -> None:
    """Guardrail: a single file that imports the SAME directory two different ways (bare
    `require('./router')` and explicit `require('./router/index')`) must be counted once in
    `importer_files` and produce exactly one edge PER real statement (2), never deduplicated to
    fewer or multiplied by cross-matching aliases to more."""
    project = tmp_path / "project"
    router_dir = project / "lib" / "router"
    router_dir.mkdir(parents=True)
    target = router_dir / "index.js"
    target.write_text("module.exports = {};\n", encoding="utf-8")
    consumer = project / "lib" / "app.js"
    consumer.write_text(
        'var Router = require("./router");\nvar RouterAgain = require("./router/index");\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_importers(target, project)

    assert payload["importer_files"] == [str(consumer.resolve())]
    assert payload["importer_count"] == 2
    lines = sorted(int(edge["line"]) for edge in payload["importers"])
    assert lines == [1, 2]


def test_build_file_importers_bare_specifier_matches_local_directory_index_package(
    tmp_path: Path,
) -> None:
    """DOCUMENTED accepted-heuristic behavior (adversarial-review finding A, 2026-07-16): the
    directory-index parent-dir alias also lets a BARE (non-relative) specifier that happens to
    match a LOCAL directory's name be reported as an importer -- `import { x } from 'react'` when
    the repo has `src/react/index.ts`. The reverse CONFIRM step's bare-specifier arm
    (`_module_path_matches_definition`) is a path-SUFFIX compare that strips the index magic name,
    so this matches at the pre-existing low "partial-resolution" confidence.

    This is CORRECT in pnpm/yarn workspace monorepos (where `react` is a real local package dir)
    and a deliberate false-positive for the rare npm-package-name-vs-local-dir collision -- NOT an
    exact edge. Pinned so the behavior is INTENTIONAL and reviewed, not a silent surprise. (The
    parent-dir alias is confined to the reverse-importers prefilter, so this heuristic does NOT
    leak into ranking or blast-radius -- see the confinement + non-inflation tests below.)"""
    project = tmp_path / "project"
    react_dir = project / "src" / "react"
    react_dir.mkdir(parents=True)
    target = react_dir / "index.ts"
    target.write_text("export const x = 1;\n", encoding="utf-8")
    consumer = project / "src" / "app.ts"
    consumer.write_text("import { x } from 'react';\n", encoding="utf-8")

    payload = repo_map.build_file_importers(target, project)

    # The bare specifier IS reported (the accepted workspace-monorepo heuristic).
    assert str(consumer.resolve()) in set(payload["importer_files"])
    edge = next(
        current for current in payload["importers"] if current["file"] == str(consumer.resolve())
    )
    assert edge["module"] == "react"


def test_build_file_importers_finds_tsconfig_alias_importer(tmp_path: Path) -> None:
    """Prefilter recall: a tsconfig path-alias importer must still be found (not just plain
    relative imports)."""
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (project / "tsconfig.json").write_text(
        json.dumps({
            "compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["src/*"]}},
        }),
        encoding="utf-8",
    )
    target = src / "util.ts"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    importer = src / "consumer.ts"
    importer.write_text('import { foo } from "@app/util";\n', encoding="utf-8")

    payload = repo_map.build_file_importers(target, project)

    assert str(importer.resolve()) in set(payload["importer_files"])
    edge = next(
        current for current in payload["importers"] if current["file"] == str(importer.resolve())
    )
    assert edge["module"] == "@app/util"


# ---------------------------------------------------------------------------------------------
# Python reverse precision + recall (#74 review fix): the reverse confirm step used to suffix-
# match Python imports (`_module_path_matches_definition`) with NO directory/resolution
# context, unlike the already-precise JS/TS/Rust branches -- two files sharing a basename
# (e.g. `app/config.py` and `tools/config.py`) produced a phantom importer edge on the wrong
# one. The fix makes the Python confirm step resolve-then-compare via the SAME precise
# resolver the forward `tg imports` uses (`_python_module_candidates`). A companion recall gap
# (`from . import X` was silently dropped from the reverse alias graph because it has no
# dotted `node.module` text) is fixed alongside it.
# ---------------------------------------------------------------------------------------------


def test_build_file_importers_python_excludes_duplicate_basename_false_edge(
    tmp_path: Path,
) -> None:
    """The Case-4-style false edge, but for Python: `import config` in tools/run.py resolves
    (per Python's own import semantics -- the importer's own directory is searched) to
    tools/config.py, NOT app/config.py, even though both end with the same basename."""
    project = tmp_path / "project"
    app_dir = project / "app"
    tools_dir = project / "tools"
    app_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)

    app_config = app_dir / "config.py"
    app_config.write_text("APP_SETTING = 1\n", encoding="utf-8")
    tools_config = tools_dir / "config.py"
    tools_config.write_text("TOOLS_SETTING = 2\n", encoding="utf-8")

    run_py = tools_dir / "run.py"
    run_py.write_text("import config\n", encoding="utf-8")

    app_payload = repo_map.build_file_importers(app_config, project)
    tools_payload = repo_map.build_file_importers(tools_config, project)

    assert str(run_py.resolve()) not in set(app_payload["importer_files"])
    assert app_payload["importer_count"] == 0

    assert str(run_py.resolve()) in set(tools_payload["importer_files"])
    edge = next(
        current
        for current in tools_payload["importers"]
        if current["file"] == str(run_py.resolve())
    )
    assert edge["module"] == "config"
    # #155 fix companion: a normal (non-sys.path-hacked) Python edge must NOT gain the
    # "path_provenance" key at all -- same payload-bloat-avoidance rule already applied to
    # dynamic/dynamic_unresolved (only stamp a key when it says something non-default).
    assert "path_provenance" not in edge


def test_build_file_importers_python_recalls_from_dot_import(tmp_path: Path) -> None:
    """`from . import helpers` has no dotted `node.module` text -- the reverse-import alias
    graph used to silently DROP it, so a sibling `from . import X` importer was invisible to
    `tg importers` entirely (never even reached the confirm step), not merely over-counted."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    main_path = pkg / "main.py"
    main_path.write_text("from . import helpers\n", encoding="utf-8")

    payload = repo_map.build_file_importers(helpers_path, project)

    assert str(main_path.resolve()) in set(payload["importer_files"])
    edge = next(
        current for current in payload["importers"] if current["file"] == str(main_path.resolve())
    )
    assert edge["module"] == "helpers"
    assert edge["line"] == 1


def test_build_file_importers_python_dotted_import_precision(tmp_path: Path) -> None:
    """A dotted absolute import (`from app.config import X`) must confirm against the file it
    actually names, not a same-basename sibling under a different top-level package."""
    project = tmp_path / "project"
    app_dir = project / "app"
    tools_dir = project / "tools"
    app_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")

    app_config = app_dir / "config.py"
    app_config.write_text("APP_SETTING = 1\n", encoding="utf-8")
    tools_config = tools_dir / "config.py"
    tools_config.write_text("TOOLS_SETTING = 2\n", encoding="utf-8")

    consumer = project / "consumer.py"
    consumer.write_text("from app.config import APP_SETTING\n", encoding="utf-8")

    app_payload = repo_map.build_file_importers(app_config, project)
    tools_payload = repo_map.build_file_importers(tools_config, project)

    assert str(consumer.resolve()) in set(app_payload["importer_files"])
    assert str(consumer.resolve()) not in set(tools_payload["importer_files"])


def test_build_file_importers_finds_directory_index_python_package_bare_from_import(
    tmp_path: Path,
) -> None:
    """Python symmetric case of the express directory-index bug: `from . import router` where
    `router` is a SUBPACKAGE (`router/__init__.py`), not a plain sibling module. The reverse
    alias prefilter must give the package's `__init__.py` a "router" alias (its parent directory
    name), the same way it now does for JS/TS `index.js` -- `_module_aliases_for_path` backs
    both languages' reverse prefilter identically."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    router_pkg = pkg / "router"
    router_pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = router_pkg / "__init__.py"
    target.write_text("class Router:\n    pass\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text("from . import router\n", encoding="utf-8")

    payload = repo_map.build_file_importers(target, project)

    assert str(consumer.resolve()) in set(payload["importer_files"])
    edge = next(
        current for current in payload["importers"] if current["file"] == str(consumer.resolve())
    )
    assert edge["module"] == "router"
    assert edge["line"] == 1


def test_build_file_importers_directory_index_python_rejects_prefix_false_match(
    tmp_path: Path,
) -> None:
    """Guardrail, Python side: `from . import routerX` (a real sibling MODULE, not the
    `router` subpackage) must never be counted as an importer of `router/__init__.py`."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    router_pkg = pkg / "router"
    router_pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = router_pkg / "__init__.py"
    target.write_text("class Router:\n    pass\n", encoding="utf-8")

    (pkg / "routerX.py").write_text("DECOY = True\n", encoding="utf-8")
    decoy_consumer = pkg / "mainx.py"
    decoy_consumer.write_text("from . import routerX\n", encoding="utf-8")

    payload = repo_map.build_file_importers(target, project)

    assert str(decoy_consumer.resolve()) not in set(payload["importer_files"])
    assert payload["importer_count"] == 0


# ---------------------------------------------------------------------------------------------
# Shared-helper confinement (adversarial-review finding B, 2026-07-16): the directory-index
# parent-dir alias must live ONLY in the reverse-importers prefilter, NOT in the SHARED
# `_module_aliases_for_path` (which also feeds substring/exact ranking + blast-radius scope
# expansion + the test-coverage gate). A top-level `pkg/__init__.py` gaining a bare "pkg" alias
# in the shared helper would make editing that (often empty) init pull most of the repo into
# `tg blast-radius` and mark nearly every test covered.
# ---------------------------------------------------------------------------------------------


def test_directory_index_parent_alias_confined_to_reverse_importers() -> None:
    """The SHARED `_module_aliases_for_path` must NOT emit a directory-index file's parent-dir
    (bare-package) alias; that alias lives ONLY in the reverse-importers-only helper."""
    # Shared helper: byte-stable -- no bare parent-dir alias for either language's index file.
    assert "pkg" not in repo_map._module_aliases_for_path("pkg/__init__.py")
    assert "router" not in repo_map._module_aliases_for_path("lib/router/index.js")
    # Confined helper: emits exactly the parent-dir alias, and only for index/__init__ stems.
    assert repo_map._reverse_importer_extra_aliases("pkg/__init__.py") == frozenset({"pkg"})
    assert repo_map._reverse_importer_extra_aliases("lib/router/index.js") == frozenset({"router"})
    assert repo_map._reverse_importer_extra_aliases("lib/router/index.ts") == frozenset({"router"})
    # A non-index file earns no extra alias (it already resolves by its own stem).
    assert repo_map._reverse_importer_extra_aliases("lib/router/route.js") == frozenset()
    assert repo_map._reverse_importer_extra_aliases("pkg/service.py") == frozenset()
    # A bare top-level index with no parent directory earns nothing (no parent to alias).
    assert repo_map._reverse_importer_extra_aliases("index.js") == frozenset()


def test_edit_plan_blast_radius_scope_not_inflated_by_top_level_init(tmp_path: Path) -> None:
    """Finding B, at the EXACT flagged vector: `_scoped_repo_map_for_edit_plan_blast_radius` (the
    `tg edit-plan` blast-radius scoping) expands scope by intersecting each selected file's
    `_module_aliases_for_path` with importers' alias candidates
    (`definition_aliases & imported_aliases`). A top-level `pkg/__init__.py` must NOT alias to
    bare "pkg" in that shared helper, or EDITING it would drag every unrelated `import pkg.*` file
    into the edit-plan radius (the gate's worst case). With the parent-dir alias confined to the
    reverse-importers prefilter, the shared helper stays byte-stable and the scope does not
    inflate. RED on the first fix (which put "pkg" in `_module_aliases_for_path`)."""
    root = tmp_path / "repo"
    init_file = str(root / "pkg" / "__init__.py")
    service = str(root / "pkg" / "service.py")
    other = str(root / "pkg" / "other.py")
    helpers = str(root / "pkg" / "helpers.py")
    map_data = {
        "path": str(root),
        "files": [init_file, service, other, helpers],
        "symbols": [{"name": "top_level_init_symbol", "file": init_file, "kind": "function"}],
        # service.py / other.py import a DIFFERENT submodule of the package, never the init symbol.
        "imports": [
            {"file": service, "imports": ["pkg.helpers"]},
            {"file": other, "imports": ["pkg.helpers"]},
        ],
        "tests": [],
    }
    payload = {"files": [init_file], "tests": []}

    scoped = repo_map._scoped_repo_map_for_edit_plan_blast_radius(
        map_data, payload, "top_level_init_symbol", max_files=5
    )

    scoped_files = set(scoped["files"])
    assert init_file in scoped_files  # the edited file itself is always in scope
    # The unrelated `import pkg.helpers` files must NOT be scope-expanded into the radius.
    assert service not in scoped_files
    assert other not in scoped_files
    assert scoped["edit_plan_blast_radius_scope"]["scoped_file_count"] == 1


# ---------------------------------------------------------------------------------------------
# Token economy: the reverse payload must be far smaller than a whole-repo `tg map`.
# ---------------------------------------------------------------------------------------------


def test_importers_payload_is_far_smaller_than_map(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    target = src / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    (src / "consumer0.js").write_text('import { foo } from "./util";\n', encoding="utf-8")
    for index in range(1, 25):
        (src / f"other{index}.js").write_text(
            f"export function fn{index}() {{ return {index}; }}\n", encoding="utf-8"
        )

    importers_payload = repo_map.build_file_importers(target, project)
    map_payload = repo_map.build_repo_map(project)

    # Both payloads carry the SAME shared `_envelope()` self-description block verbatim
    # (version/schema_version/routing_backend/routing_reason/sidecar_used/coverage/path) --
    # fixed metadata about tg's own capabilities, not DATA about this particular query. Its size
    # legitimately grows over time (#733 made `coverage.language_scope`/`symbol_navigation`
    # honest and derived live from the language registry, ~4x longer than the stale 4-language
    # literal they replaced) with zero change to either payload's actual data volume. Comparing
    # raw total serialized bytes conflates that identical-in-both-payloads fixed cost with real
    # data; since the importers payload is intentionally tiny (one reverse edge), the fixed cost
    # is a much larger FRACTION of its total than of the far-bigger map payload -- enough for an
    # honest envelope-field growth to tip a total-bytes ratio over threshold with no data-volume
    # regression at all (exactly what #733 did). Strip the shared envelope from BOTH payloads
    # before comparing so the assertion proves what it actually claims -- the reverse EDGE DATA is
    # far smaller than the whole-repo inventory DATA -- and stays robust to future envelope growth
    # (any field, not just today's `coverage`) instead of re-breaking on the next honesty fix.
    envelope_keys = set(repo_map._envelope(project))
    importers_data = {k: v for k, v in importers_payload.items() if k not in envelope_keys}
    map_data = {k: v for k, v in map_payload.items() if k not in envelope_keys}

    importers_size = len(json.dumps(importers_data))
    map_size = len(json.dumps(map_data))

    assert importers_size < 0.1 * map_size, (
        f"importers payload data ({importers_size}B) is not <0.1x the map payload data "
        f"({map_size}B), excluding the shared envelope keys {sorted(envelope_keys)}"
    )


# ---------------------------------------------------------------------------------------------
# Honesty: over-cap -> exit2 (never a clean empty), missing -> exit1, --max-repo-files 1 ->
# truncated exit2, session == cold edges.
# ---------------------------------------------------------------------------------------------


def test_imports_missing_file_exits_1() -> None:
    result = runner.invoke(app, ["imports", "does/not/exist.py"])
    assert result.exit_code == 1


def test_importers_missing_file_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["importers", str(tmp_path / "nope.py"), str(tmp_path)])
    assert result.exit_code == 1


def test_imports_over_parse_cap_exits_2_never_clean_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "big.py"
    target.write_text("import os\n", encoding="utf-8")
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "1")

    result = runner.invoke(app, ["imports", str(target), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["result_incomplete"] is True
    assert payload["imports"] == []
    assert "incomplete_reason" in payload


def test_importers_max_repo_files_truncation_exits_2(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    target = src / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    (src / "consumer.js").write_text('import { foo } from "./util";\n', encoding="utf-8")
    (src / "other1.js").write_text("export const a = 1;\n", encoding="utf-8")
    (src / "other2.js").write_text("export const b = 2;\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["importers", str(target), str(project), "--max-repo-files", "1", "--json"],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    scan_limit = payload.get("scan_limit")
    assert isinstance(scan_limit, dict) and scan_limit.get("possibly_truncated")
    assert payload.get("result_incomplete") is True


def test_session_importers_matches_cold_importers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    target = src / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    (src / "consumer.js").write_text('import { foo } from "./util";\n', encoding="utf-8")

    session_id = session_store.open_session(str(project)).session_id

    cold = repo_map.build_file_importers(target, project)
    warm = session_store.session_file_importers(session_id, str(target), str(project))

    assert set(warm["importer_files"]) == set(cold["importer_files"])
    assert warm["importer_count"] == cold["importer_count"]
    assert warm["importer_count"] > 0


# ---------------------------------------------------------------------------------------------
# Dogfood #104 : `tg importers` FILE-relative-to-ROOT path DOUBLING.
#
# `build_file_importers_from_map` used to assume ANY non-absolute FILE arg was meant relative to
# ROOT (`repo_root / resolved_file`). From a PARENT cwd, a FILE arg typed relative to CWD (the
# normal shell convention -- and therefore naturally prefixed with ROOT's own directory name,
# e.g. `myrepo/src/util.js` when ROOT is `myrepo`) got joined onto ROOT a SECOND time, producing
# a doubled, nonexistent path (`myrepo/myrepo/src/util.js`) and a false "not found". FILE must
# resolve independently against cwd -- exactly like `tg imports FILE` (which takes no ROOT arg
# at all, and was therefore never subject to this bug) -- while ROOT stays only the scan
# boundary, never a prefix FILE gets joined onto.
# ---------------------------------------------------------------------------------------------
