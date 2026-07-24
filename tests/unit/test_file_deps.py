"""TDD for the #74 moat: `tg imports FILE` / `tg importers FILE [ROOT]`.

The scoped file-dependency primitive that closes the P4 benchmark gap (docs/benchmarks.md):
`tg map` alone made tg ~10x WORSE than grep on file-dependency lookups because it had no
primitive scoped to a single file. These commands answer "what does this file import"
(forward, O(1) parse) and "who imports this file" (reverse, bounded repo scan) directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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
# Dogfood #104 (P0, CEO 1.54.6/WSL2): `tg importers` FILE-relative-to-ROOT path DOUBLING.
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


def _make_js_importer_fixture(base: Path) -> tuple[Path, Path, Path]:
    """A tiny repo: BASE/repo/src/{util.js, consumer.js}; consumer imports util. Returns
    (repo_dir, target_file, importer_file)."""
    repo_dir = base / "repo"
    src = repo_dir / "src"
    src.mkdir(parents=True)
    target = src / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    importer = src / "consumer.js"
    importer.write_text('import { foo } from "./util";\n', encoding="utf-8")
    return repo_dir, target, importer


def test_build_file_importers_relative_file_and_root_from_parent_cwd_no_doubling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact #104 bug shape: FILE and ROOT both given relative to a PARENT cwd, so FILE is
    naturally prefixed with ROOT's own directory name. Must resolve to the real file, not
    `root/root/...`-doubled."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _repo_dir, target, importer = _make_js_importer_fixture(workspace)
    monkeypatch.chdir(workspace)

    payload = repo_map.build_file_importers("repo/src/util.js", "repo")

    assert payload["file"] == str(target.resolve())
    assert payload["importer_count"] == 1
    assert str(importer.resolve()) in set(payload["importer_files"])


def test_build_file_importers_file_relative_to_root_from_inside_repo_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FILE relative to ROOT, invoked from INSIDE the repo (ROOT='.'): the common case, must
    keep working exactly as before."""
    repo_dir, target, importer = _make_js_importer_fixture(tmp_path)
    monkeypatch.chdir(repo_dir)

    payload = repo_map.build_file_importers("src/util.js", ".")

    assert payload["file"] == str(target.resolve())
    assert payload["importer_count"] == 1
    assert str(importer.resolve()) in set(payload["importer_files"])


def test_build_file_importers_absolute_file_and_root_cwd_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absolute FILE + absolute ROOT must resolve identically regardless of cwd."""
    repo_dir, target, importer = _make_js_importer_fixture(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    payload = repo_map.build_file_importers(str(target), str(repo_dir))

    assert payload["file"] == str(target.resolve())
    assert payload["importer_count"] == 1
    assert str(importer.resolve()) in set(payload["importer_files"])


def test_build_file_importers_absolute_file_relative_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absolute FILE + relative ROOT (from the parent cwd) must resolve identically too --
    this combination already worked pre-fix (the join was skipped for an absolute FILE), so
    this pins it stays correct."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _repo_dir, target, importer = _make_js_importer_fixture(workspace)
    monkeypatch.chdir(workspace)

    payload = repo_map.build_file_importers(str(target), "repo")

    assert payload["file"] == str(target.resolve())
    assert payload["importer_count"] == 1
    assert str(importer.resolve()) in set(payload["importer_files"])


def test_build_file_importers_outside_root_file_returns_no_importers_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that genuinely exists but lives OUTSIDE root must resolve to its real (correct)
    path, and honestly report zero importers -- not crash, and not silently claim a false
    importer via a coincidentally-real doubled path (the confinement/not_found contract)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_js_importer_fixture(workspace)
    outside_dir = workspace / "other"
    outside_dir.mkdir()
    outside_file = outside_dir / "thing.js"
    outside_file.write_text("export function bar() {}\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    payload = repo_map.build_file_importers("other/thing.js", "repo")

    assert payload["file"] == str(outside_file.resolve())
    assert payload["importer_count"] == 0
    assert payload["importer_files"] == []
    # Dogfood honesty fix (published v1.69.2 wheel): a valid FILE outside the scanned ROOT must
    # be flagged so `importer_count: 0` here is never confused with a genuine unimported-in-ROOT
    # answer -- an agent shelling out from the wrong CWD needs to see this, not a look-alike zero.
    assert payload["file_outside_root"] is True
    remediation = payload["scan_remediation"]
    assert isinstance(remediation, str) and remediation
    assert "ROOT" in remediation


def test_build_file_importers_from_map_outside_root_stamps_honest_signal(
    tmp_path: Path,
) -> None:
    """Direct `build_file_importers_from_map` unit coverage of the same honesty fix: a repo_map
    scoped to dirA, queried for a FILE that genuinely exists in a separate dirB, must stamp
    `file_outside_root: True` plus a non-empty `scan_remediation` naming the mismatch -- instead
    of a bare `importer_count: 0` that is indistinguishable from a real no-importers answer."""
    dir_a = tmp_path / "dirA"
    dir_a.mkdir()
    (dir_a / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    dir_b = tmp_path / "dirB"
    dir_b.mkdir()
    outside_file = dir_b / "other.py"
    outside_file.write_text("VALUE = 2\n", encoding="utf-8")

    repo_map_payload = repo_map.build_repo_map(dir_a)
    payload = repo_map.build_file_importers_from_map(repo_map_payload, str(outside_file))

    assert payload["importer_count"] == 0
    assert payload["file_outside_root"] is True
    remediation = payload["scan_remediation"]
    assert isinstance(remediation, str) and remediation
    assert "ROOT" in remediation
    assert str(dir_a.resolve()) in remediation


def test_build_file_importers_from_map_inside_root_unimported_is_not_flagged_outside(
    tmp_path: Path,
) -> None:
    """Regression guard: a FILE genuinely inside ROOT but unimported is a LEGIT empty result --
    must not be mistaken for (or ever stamped with) the outside-root signal."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    lonely = repo_dir / "lonely.py"
    lonely.write_text("VALUE = 1\n", encoding="utf-8")

    repo_map_payload = repo_map.build_repo_map(repo_dir)
    payload = repo_map.build_file_importers_from_map(repo_map_payload, str(lonely))

    assert payload["importer_count"] == 0
    assert payload["file_outside_root"] is False
    # `scan_remediation` is only ever populated (as a dict-copy sibling of `scan_limit`) when the
    # repo-map scan itself was max-repo-files-capped; a plain `build_repo_map(repo_dir)` call
    # (no cap) never sets the key at all -- `.get()` tolerates that, `is None` would KeyError.
    assert not payload.get("scan_remediation")


def test_build_file_importers_from_map_inside_root_with_importers_is_not_flagged_outside(
    tmp_path: Path,
) -> None:
    """Regression guard: a FILE inside ROOT WITH real importers must report them normally and
    never carry the outside-root signal."""
    repo_dir, target, importer = _make_js_importer_fixture(tmp_path)

    repo_map_payload = repo_map.build_repo_map(repo_dir)
    payload = repo_map.build_file_importers_from_map(repo_map_payload, str(target))

    assert payload["file_outside_root"] is False
    assert payload["importer_count"] == 1
    assert str(importer.resolve()) in set(payload["importer_files"])


def test_build_file_importers_from_map_relative_file_stays_in_root_daemon_convention(
    tmp_path: Path,
) -> None:
    """Safety constraint: the daemon/session convention passes a FILE relative to the repo_map's
    OWN root (session_file_importers / the raw daemon-socket `file_importers` command both do
    this). That join (`repo_root / resolved_file`) always lands under repo_root, so the new
    containment check must never fire a false positive for this calling convention."""
    repo_dir, target, _importer = _make_js_importer_fixture(tmp_path)

    repo_map_payload = repo_map.build_repo_map(repo_dir)
    payload = repo_map.build_file_importers_from_map(repo_map_payload, "src/util.js")

    assert payload["file_outside_root"] is False
    assert payload["file"] == str(target.resolve())
    assert payload["importer_count"] == 1


def test_importers_cli_relative_file_and_root_from_parent_cwd_no_doubling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI-level repro of the literal reported command:
    `tg importers repo/src/util.js repo --json` run from the parent directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _repo_dir, target, importer = _make_js_importer_fixture(workspace)
    monkeypatch.chdir(workspace)

    result = runner.invoke(app, ["importers", "repo/src/util.js", "repo", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["not_found"] is False
    assert payload["file"] == str(target.resolve())
    assert payload["importer_count"] == 1
    assert str(importer.resolve()) in set(payload["importer_files"])


def test_importers_cli_outside_root_file_exits_1_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI-level confinement/not_found contract: a real file outside ROOT reports honestly
    (exit 1, not_found:true, correct `file` path) instead of crashing on a doubled path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_js_importer_fixture(workspace)
    outside_dir = workspace / "other"
    outside_dir.mkdir()
    outside_file = outside_dir / "thing.js"
    outside_file.write_text("export function bar() {}\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    result = runner.invoke(app, ["importers", "other/thing.js", "repo", "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["not_found"] is True
    assert payload["importer_count"] == 0
    assert payload["file"] == str(outside_file.resolve())
    # Dogfood honesty fix: the CLI-level payload must carry the same outside-root signal as the
    # underlying builder -- an agent reading `not_found: true` + `importer_count: 0` alone cannot
    # tell "wrong ROOT" from "really unimported"; this is additive only (exit code unchanged).
    assert payload["file_outside_root"] is True
    assert isinstance(payload["scan_remediation"], str) and payload["scan_remediation"]


def test_imports_relative_file_from_parent_cwd_already_resolves_correctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reference behavior: `tg imports` takes no ROOT arg at all, so a cwd-relative FILE
    (prefixed with what would be a sibling ROOT's directory name) was never subject to the
    doubling bug -- pin it as the correct baseline `tg importers` must match."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _repo_dir, target, _importer = _make_js_importer_fixture(workspace)
    monkeypatch.chdir(workspace)

    payload = repo_map.build_file_imports("repo/src/util.js")

    assert payload["file"] == str(target.resolve())
    assert payload["result_incomplete"] is False


# ---------------------------------------------------------------------------------------------
# Dynamic-import awareness (#93 SUB-1): `importlib.import_module(...)` / `__import__(...)` /
# bare `import_module(...)` (Python) and dynamic `import(...)` call-form / bare-or-non-literal
# `require(...)` (JS/TS) are invisible to the static-only extractors -- these are `ast.Call`
# nodes (Python) or call-form expressions (JS/TS) that can appear ANYWHERE (inside a function, a
# conditional), not just as a top-level import statement. Recall fix on both directions: forward
# (`tg imports`, `build_file_imports`) AND reverse (`tg importers`, `build_file_importers`), since
# the reverse primitive's prefilter (`_reverse_importers`) depends on the SAME per-file imports
# list the forward primitive's summary is built from.
# ---------------------------------------------------------------------------------------------


def test_build_file_imports_detects_dynamic_importlib_import_module(tmp_path: Path) -> None:
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text(
        'import importlib\n\ndef load():\n    return importlib.import_module("pkg.helpers")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "pkg.helpers")
    assert entry["dynamic"] is True
    assert entry["dynamic_unresolved"] is False
    assert entry["resolved"] == str(helpers_path.resolve())
    assert entry["external"] is False
    assert entry["line"] == 4


def test_build_file_imports_detects_dunder_import(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    consumer = project / "app.py"
    consumer.write_text('mod = __import__("json")\n', encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "json")
    assert entry["dynamic"] is True
    assert entry["dynamic_unresolved"] is False
    assert entry["external"] is True


def test_build_file_imports_detects_bare_import_module_call(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    consumer = project / "app.py"
    consumer.write_text(
        'from importlib import import_module\nmod = import_module("json")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    dynamic_entries = [
        current
        for current in payload["imports"]
        if current["module"] == "json" and current.get("dynamic")
    ]
    assert len(dynamic_entries) == 1
    assert dynamic_entries[0]["dynamic_unresolved"] is False


def test_build_file_imports_marks_non_literal_python_dynamic_import_as_unresolved(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    consumer = project / "app.py"
    consumer.write_text(
        "import importlib\n\ndef load(name):\n    return importlib.import_module(name)\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    dynamic_entries = [current for current in payload["imports"] if current.get("dynamic")]
    assert len(dynamic_entries) == 1
    entry = dynamic_entries[0]
    assert entry["dynamic_unresolved"] is True
    assert entry["module"] == ""
    assert entry["resolved"] is None
    assert entry["external"] is False
    # never fabricate a module name in the flat `unresolved` summary for an import whose name
    # we don't actually know
    assert "" not in payload["unresolved"]


def test_build_file_imports_detects_dynamic_import_call_literal(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    util_path = src / "util.js"
    util_path.write_text("export function foo() {}\n", encoding="utf-8")
    consumer = src / "consumer.js"
    consumer.write_text(
        'async function load() {\n  return import("./util");\n}\n', encoding="utf-8"
    )

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "./util")
    assert entry["dynamic"] is True
    assert entry["dynamic_unresolved"] is False
    assert entry["resolved"] == str(util_path.resolve())
    assert entry["line"] == 2


def test_build_file_imports_marks_non_literal_dynamic_import_call_as_unresolved(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    consumer = src / "consumer.js"
    consumer.write_text(
        "async function load(name) {\n  return import(name);\n}\n", encoding="utf-8"
    )

    payload = repo_map.build_file_imports(consumer)

    dynamic_entries = [current for current in payload["imports"] if current.get("dynamic")]
    assert len(dynamic_entries) == 1
    entry = dynamic_entries[0]
    assert entry["dynamic_unresolved"] is True
    assert entry["module"] == ""
    assert entry["resolved"] is None
    assert "" not in payload["unresolved"]


def test_build_file_imports_detects_bare_require_without_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    util_path = src / "util.js"
    util_path.write_text("module.exports = {};\n", encoding="utf-8")
    consumer = src / "consumer.js"
    consumer.write_text('require("./util");\n', encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "./util")
    assert entry["resolved"] == str(util_path.resolve())
    assert entry["dynamic"] is True
    assert entry["dynamic_unresolved"] is False


def test_build_file_imports_marks_non_literal_require_as_unresolved(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    consumer = src / "consumer.js"
    consumer.write_text("function load(name) {\n  return require(name);\n}\n", encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    dynamic_entries = [current for current in payload["imports"] if current.get("dynamic")]
    assert len(dynamic_entries) == 1
    entry = dynamic_entries[0]
    assert entry["dynamic_unresolved"] is True
    assert entry["module"] == ""


def test_build_file_imports_static_require_unaffected_by_dynamic_detection(
    tmp_path: Path,
) -> None:
    """The pre-existing assignment-anchored `require(...)` regex path is untouched -- a normal
    `const x = require("y")` line must still yield exactly ONE entry (not two, from also
    matching the new broader dynamic-require fallback)."""
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    util_path = src / "util.js"
    util_path.write_text("module.exports = {};\n", encoding="utf-8")
    consumer = src / "consumer.js"
    consumer.write_text('const util = require("./util");\n', encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    matches = [current for current in payload["imports"] if current["module"] == "./util"]
    assert len(matches) == 1
    # Payload-bloat fix: a static entry omits the "dynamic"/"dynamic_unresolved" keys entirely
    # rather than stamping always-False markers (see test_importers_payload_is_far_smaller_than_map).
    assert "dynamic" not in matches[0]
    assert "dynamic_unresolved" not in matches[0]
    assert matches[0]["resolved"] == str(util_path.resolve())


def test_build_file_importers_recalls_dynamic_importlib_import_module(tmp_path: Path) -> None:
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = pkg / "helpers.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "loader.py"
    consumer.write_text(
        'import importlib\n\ndef load():\n    return importlib.import_module("pkg.helpers")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_importers(target, project)

    importer_files = set(payload["importer_files"])
    assert importer_files == {str(consumer.resolve())}
    edge = payload["importers"][0]
    assert edge["dynamic"] is True
    assert edge["dynamic_unresolved"] is False
    assert edge["module"] == "pkg.helpers"


def test_build_file_importers_recalls_dynamic_import_call(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    target = src / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    consumer = src / "consumer.js"
    consumer.write_text(
        'async function load() {\n  return import("./util");\n}\n', encoding="utf-8"
    )

    payload = repo_map.build_file_importers(target, project)

    importer_files = set(payload["importer_files"])
    assert importer_files == {str(consumer.resolve())}
    edge = payload["importers"][0]
    assert edge["dynamic"] is True
    assert edge["dynamic_unresolved"] is False
    assert edge["module"] == "./util"


def test_build_file_importers_never_asserts_edge_for_unresolved_dynamic_import(
    tmp_path: Path,
) -> None:
    """Precision: an `importlib.import_module(name)` call whose argument is a variable must
    NEVER be reported as a confirmed importer of any file -- there is no literal module name to
    compare, so asserting an edge here would be a fabricated (over-reported) result."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = pkg / "helpers.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "loader.py"
    consumer.write_text(
        "import importlib\n\ndef load(name):\n    return importlib.import_module(name)\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_importers(target, project)

    assert payload["importer_files"] == []


# ---------------------------------------------------------------------------------------------
# #152 fix (CEO v1.69.3 dogfood, 2 HIGH): `sys.path.insert`/`sys.path.append` path-hacked
# modules. Before this fix, a file that made a sibling/vendored directory importable via a
# same-repo path hack (e.g. `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))`
# then `from mymod import x`) was left `external`/`resolved=None` on the forward side
# (`build_file_imports`) and its importer was invisible to the reverse side
# (`build_file_importers` reported no importer at all, the CLI's `not_found: true`).
# ---------------------------------------------------------------------------------------------


def test_build_file_imports_resolves_sys_path_insert_hacked_module(tmp_path: Path) -> None:
    """The exact CEO dogfood repro shape: `sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "lib"))` then `from mymod import x` must resolve `mymod` to the
    real file under `lib/`, not stay external."""
    project = tmp_path / "project"
    lib_dir = project / "lib"
    lib_dir.mkdir(parents=True)
    mymod_path = lib_dir / "mymod.py"
    mymod_path.write_text("def x():\n    return 1\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys, os\n"
        'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))\n'
        "from mymod import x\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(app_py)

    entry = next(current for current in payload["imports"] if current["module"] == "mymod")
    assert entry["resolved"] == str(mymod_path.resolve())
    assert entry["external"] is False
    assert entry["provenance"] == ["sys-path-insert"]
    assert payload["resolved_files"] == [str(mymod_path.resolve())]
    assert str(mymod_path.resolve()) not in payload["external_modules"]


def test_build_file_importers_finds_sys_path_insert_hacked_importer(tmp_path: Path) -> None:
    """The reverse direction of the same fixture: `tg importers lib/mymod.py <root>` must find
    `app.py` as an importer (not report zero importers / `not_found`)."""
    project = tmp_path / "project"
    lib_dir = project / "lib"
    lib_dir.mkdir(parents=True)
    mymod_path = lib_dir / "mymod.py"
    mymod_path.write_text("def x():\n    return 1\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys, os\n"
        'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))\n'
        "from mymod import x\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_importers(mymod_path, project)

    assert payload["importer_count"] >= 1
    assert str(app_py.resolve()) in set(payload["importer_files"])
    edge = next(
        current for current in payload["importers"] if current["file"] == str(app_py.resolve())
    )
    assert edge["module"] == "mymod"
    assert edge["line"] == 3
    # #155 fix: the reverse edge must honestly report the sys.path-hack provenance (mirrors the
    # forward `tg imports` side, which already tags this "sys-path-insert" -- see
    # test_build_file_imports_resolves_sys_path_insert_hacked_module above) instead of silently
    # collapsing it into the generic "parser-backed" label. `provenance` itself stays
    # "parser-backed" (drives resolution_confidence; this edge IS still exactly as
    # parser-confirmed as any other Python edge) -- the hack is surfaced as a separate field.
    assert edge["provenance"] == "parser-backed"
    assert edge["path_provenance"] == "sys-path-insert"


def test_sys_path_hack_present_stdlib_import_stays_external(tmp_path: Path) -> None:
    """Regression guard (a): a genuinely-external stdlib import in the SAME file as a sys.path
    hack must stay external -- the hack must not make everything in the file resolve."""
    project = tmp_path / "project"
    lib_dir = project / "lib"
    lib_dir.mkdir(parents=True)
    (lib_dir / "mymod.py").write_text("def x():\n    return 1\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys, os\n"
        'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))\n'
        "from mymod import x\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(app_py)

    os_entry = next(current for current in payload["imports"] if current["module"] == "os")
    assert os_entry["external"] is True
    assert os_entry["resolved"] is None
    sys_entry = next(current for current in payload["imports"] if current["module"] == "sys")
    assert sys_entry["external"] is True
    assert sys_entry["resolved"] is None


def test_dynamic_sys_path_insert_leaves_module_external(tmp_path: Path) -> None:
    """Regression guard (b): a DYNAMIC sys.path argument (a computed variable, not a literal)
    must leave the module external -- never guess at a directory we can't statically prove."""
    project = tmp_path / "project"
    lib_dir = project / "lib"
    lib_dir.mkdir(parents=True)
    (lib_dir / "mymod.py").write_text("def x():\n    return 1\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys\n"
        "extra_dir = compute_extra_dir()\n"
        "sys.path.insert(0, extra_dir)\n"
        "from mymod import x\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(app_py)

    entry = next(current for current in payload["imports"] if current["module"] == "mymod")
    assert entry["external"] is True
    assert entry["resolved"] is None
    assert "mymod" in payload["external_modules"]


def test_sys_path_insert_escape_outside_root_does_not_resolve(tmp_path: Path) -> None:
    """Regression guard (c): a `sys.path.insert(0, os.path.join(os.path.dirname(__file__),
    "..", "outside_lib"))`-style escape must NOT resolve to a module outside the scanned repo
    root, even though the literal-join idiom is otherwise identical to the working case."""
    outside_dir = tmp_path / "outside_lib"
    outside_dir.mkdir()
    (outside_dir / "mymod.py").write_text("def x():\n    return 99\n", encoding="utf-8")

    project = tmp_path / "project"
    project.mkdir()
    # Pin `_infer_project_root`'s walk to stop exactly HERE (a marker file makes `project/` the
    # very first candidate that matches) -- otherwise it keeps walking up looking for a project-
    # root marker and could pick a much broader ancestor, which would make `outside_lib` no
    # longer an actual escape relative to whatever (wider) root gets inferred.
    (project / "pyproject.toml").write_text("", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys, os\n"
        "sys.path.insert(\n"
        '    0, os.path.join(os.path.dirname(__file__), "..", "outside_lib")\n'
        ")\n"
        "from mymod import x\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(app_py)

    entry = next(current for current in payload["imports"] if current["module"] == "mymod")
    assert entry["external"] is True
    assert entry["resolved"] is None


def test_relative_import_still_works_in_file_with_sys_path_insert(tmp_path: Path) -> None:
    """Regression guard (d): a normal relative import (`from . import sibling`) in a file that
    ALSO does a sys.path hack must still resolve exactly as before -- adding extra search roots
    must not disturb the existing relative-import resolution path."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    sibling_path = pkg / "sibling.py"
    sibling_path.write_text("def y():\n    return 2\n", encoding="utf-8")
    vendor_dir = pkg / "vendor"
    vendor_dir.mkdir()
    vendor_mod = vendor_dir / "mymod.py"
    vendor_mod.write_text("def x():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text(
        "import sys, os\n"
        'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor"))\n'
        "from mymod import x\n"
        "from . import sibling\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    sibling_entry = next(c for c in payload["imports"] if c["module"] == "sibling")
    assert sibling_entry["resolved"] == str(sibling_path.resolve())
    assert sibling_entry["provenance"] == ["relative"]
    mymod_entry = next(c for c in payload["imports"] if c["module"] == "mymod")
    assert mymod_entry["resolved"] == str(vendor_mod.resolve())
    assert mymod_entry["external"] is False


def test_build_file_imports_sys_path_append_bare_literal_resolves_with_provenance(
    tmp_path: Path,
) -> None:
    """`sys.path.append("extra")` (append, not insert; a bare string literal, not an
    `os.path.join`) is a distinct idiom from the main fix test -- must also resolve, and must
    carry the "sys-path-insert" provenance tag."""
    project = tmp_path / "project"
    extra_dir = project / "extra"
    extra_dir.mkdir(parents=True)
    extra_mod = extra_dir / "extramod.py"
    extra_mod.write_text("def q():\n    return 6\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        'import sys\nsys.path.append("extra")\nfrom extramod import q\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(app_py)

    entry = next(current for current in payload["imports"] if current["module"] == "extramod")
    assert entry["resolved"] == str(extra_mod.resolve())
    assert entry["external"] is False
    assert entry["provenance"] == ["sys-path-insert"]


def test_build_file_imports_sys_path_insert_dirname_abspath_variant_resolves(
    tmp_path: Path,
) -> None:
    """`os.path.dirname(os.path.abspath(__file__))` (instead of the bare `os.path.dirname(
    __file__)`) is an equally common same-repo vendoring idiom -- must resolve the same way."""
    project = tmp_path / "project"
    lib_dir = project / "lib2"
    lib_dir.mkdir(parents=True)
    lib_mod = lib_dir / "absmod.py"
    lib_mod.write_text("def v():\n    return 5\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys, os\n"
        "sys.path.insert(\n"
        '    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib2")\n'
        ")\n"
        "from absmod import v\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(app_py)

    entry = next(current for current in payload["imports"] if current["module"] == "absmod")
    assert entry["resolved"] == str(lib_mod.resolve())
    assert entry["external"] is False


def test_build_file_imports_sys_path_insert_pathlib_parent_variant_resolves(
    tmp_path: Path,
) -> None:
    """`str(Path(__file__).parent / "vendor")` -- the pathlib-style idiom -- must resolve the
    same way as the `os.path.join` idiom."""
    project = tmp_path / "project"
    vendor_dir = project / "vendor"
    vendor_dir.mkdir(parents=True)
    vendor_mod = vendor_dir / "vendored_mod.py"
    vendor_mod.write_text("def z():\n    return 3\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        'sys.path.insert(0, str(Path(__file__).parent / "vendor"))\n'
        "from vendored_mod import z\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(app_py)

    entry = next(current for current in payload["imports"] if current["module"] == "vendored_mod")
    assert entry["resolved"] == str(vendor_mod.resolve())
    assert entry["external"] is False


def test_build_file_imports_sys_path_insert_here_alias_resolves(tmp_path: Path) -> None:
    """Optional bullet #5: `HERE = os.path.dirname(__file__)` assigned earlier in the module,
    then `os.path.join(HERE, "lib")` -- must resolve the same as spelling the dirname expression
    out inline."""
    project = tmp_path / "project"
    lib_dir = project / "lib"
    lib_dir.mkdir(parents=True)
    lib_mod = lib_dir / "aliasmod.py"
    lib_mod.write_text("def w():\n    return 4\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys, os\n"
        "HERE = os.path.dirname(__file__)\n"
        'sys.path.insert(0, os.path.join(HERE, "lib"))\n'
        "from aliasmod import w\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(app_py)

    entry = next(current for current in payload["imports"] if current["module"] == "aliasmod")
    assert entry["resolved"] == str(lib_mod.resolve())
    assert entry["external"] is False


# ---------------------------------------------------------------------------------------------
# CEO dogfood feature 6 -- the dynamic-import LITERAL slice. The base recall (#93 SUB-1, block
# above) already turns a literal `importlib.import_module("x")` / bare `import_module("x")` /
# `__import__("x")` call into a resolvable edge with `dynamic: true`. This block closes three
# real gaps found by re-verifying that base feature against the ACTUAL code (not just reading
# it): two are PROVEN FALSE-EDGE bugs (relative-form literals silently mis-resolved as absolute),
# fixed in `_python_dynamic_import_entries`/`_python_dynamic_import_call_is_relative`; the rest
# are regression-lock coverage for behavior that was already correct by composition (the dynamic
# entries flow through the exact same `_resolve_raw_import_entry`/`_confirm_import_edges` ->
# `_python_module_candidates` path as static imports) but had no dedicated test.
# ---------------------------------------------------------------------------------------------


def test_build_file_imports_relative_import_module_literal_stays_external_no_false_edge(
    tmp_path: Path,
) -> None:
    """The false-edge bug this slice fixes: `import_module(".sibling", package="pkg.subpkg")`
    is a RELATIVE literal (leading dot). Naively resolving it through the absolute-module path
    (`_python_module_parts` strips the leading empty component from `".sibling".split(".")`)
    would search for it as if it were the ABSOLUTE module "sibling" -- proven here by planting an
    UNRELATED top-level `sibling.py` decoy that must NEVER be reported as this call's target.
    `package` is a literal string too, but this slice does not attempt the chained
    package-to-directory resolution that would be needed to resolve it correctly -- it must fail
    closed (external/unresolved), not guess."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    subpkg = pkg / "subpkg"
    subpkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (subpkg / "__init__.py").write_text("", encoding="utf-8")
    decoy = project / "sibling.py"
    decoy.write_text("DECOY = True\n", encoding="utf-8")
    consumer = subpkg / "loader.py"
    consumer.write_text(
        "from importlib import import_module\n\n"
        "def load():\n"
        '    return import_module(".sibling", package="pkg.subpkg")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    dynamic_entries = [current for current in payload["imports"] if current.get("dynamic")]
    assert len(dynamic_entries) == 1
    entry = dynamic_entries[0]
    assert entry["module"] == ".sibling"  # the literal text, not fabricated/blanked
    assert entry["dynamic_unresolved"] is True
    assert entry["resolved"] is None
    assert entry["resolved"] != str(decoy.resolve())
    assert str(decoy.resolve()) not in payload["resolved_files"]


def test_build_file_importers_relative_import_module_literal_asserts_no_edge(
    tmp_path: Path,
) -> None:
    """Reverse direction of the same false-edge bug: `tg importers` on the decoy file must NOT
    report `loader.py` as a confirmed importer just because its relative dynamic literal shares a
    bare name with the decoy."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    subpkg = pkg / "subpkg"
    subpkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (subpkg / "__init__.py").write_text("", encoding="utf-8")
    decoy = project / "sibling.py"
    decoy.write_text("DECOY = True\n", encoding="utf-8")
    consumer = subpkg / "loader.py"
    consumer.write_text(
        "from importlib import import_module\n\n"
        "def load():\n"
        '    return import_module(".sibling", package="pkg.subpkg")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_importers(decoy, project)

    assert payload["importer_files"] == []
    assert str(consumer.resolve()) not in payload["importer_files"]


def test_build_file_imports_dunder_import_explicit_level_keyword_stays_external(
    tmp_path: Path,
) -> None:
    """`__import__`'s relative marker is its `level` INTEGER argument, not a leading dot in the
    name -- `__import__("sibling", level=1)` is relative even though `"sibling"` itself has no
    dot. Same false-edge risk as the `import_module` case above if `level` is ignored: proven via
    the same unrelated-decoy-file pattern."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    subpkg = pkg / "subpkg"
    subpkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (subpkg / "__init__.py").write_text("", encoding="utf-8")
    decoy = project / "sibling.py"
    decoy.write_text("DECOY = True\n", encoding="utf-8")
    consumer = subpkg / "loader.py"
    consumer.write_text(
        'def load():\n    return __import__("sibling", level=1)\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    dynamic_entries = [current for current in payload["imports"] if current.get("dynamic")]
    assert len(dynamic_entries) == 1
    entry = dynamic_entries[0]
    assert entry["dynamic_unresolved"] is True
    assert entry["resolved"] is None
    assert entry["resolved"] != str(decoy.resolve())


def test_build_file_imports_dunder_import_explicit_level_positional_stays_external(
    tmp_path: Path,
) -> None:
    """Same as above, but `level` passed as the 5th POSITIONAL argument
    (`__import__(name, globals, locals, fromlist, level)`) -- the full stdlib call shape."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    subpkg = pkg / "subpkg"
    subpkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (subpkg / "__init__.py").write_text("", encoding="utf-8")
    decoy = project / "sibling.py"
    decoy.write_text("DECOY = True\n", encoding="utf-8")
    consumer = subpkg / "loader.py"
    consumer.write_text(
        'def load():\n    return __import__("sibling", globals(), locals(), [], 1)\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    dynamic_entries = [current for current in payload["imports"] if current.get("dynamic")]
    assert len(dynamic_entries) == 1
    entry = dynamic_entries[0]
    assert entry["dynamic_unresolved"] is True
    assert entry["resolved"] is None
    assert entry["resolved"] != str(decoy.resolve())


def test_build_file_imports_dunder_import_explicit_level_zero_still_resolves(
    tmp_path: Path,
) -> None:
    """Regression guard: an explicit but ZERO `level=0` keyword is still the safe absolute case
    (Python's own default) -- must not be swept into the relative fail-closed path just because a
    `level` keyword is textually present."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = pkg / "helpers.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "loader.py"
    consumer.write_text(
        'mod = __import__("pkg.helpers", level=0)\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "pkg.helpers")
    assert entry["dynamic"] is True
    assert entry["dynamic_unresolved"] is False
    assert entry["resolved"] == str(target.resolve())


def test_build_file_imports_dynamic_import_resolves_through_sys_path_insert_root(
    tmp_path: Path,
) -> None:
    """Composition with #152: a module ONLY reachable via a `sys.path.insert` hack must still
    resolve when reached through `importlib.import_module(...)` instead of a static `from x
    import y` -- both raw-entry shapes (`_python_imports_with_lines`'s static loop and its
    `_python_dynamic_import_entries` extension) feed the SAME `_resolve_raw_import_entry` ->
    `_python_module_candidates` resolver, which tries the sys-path-hacked roots first."""
    project = tmp_path / "project"
    lib_dir = project / "lib"
    lib_dir.mkdir(parents=True)
    mymod_path = lib_dir / "mymod.py"
    mymod_path.write_text("def x():\n    return 1\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys, os, importlib\n"
        'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))\n'
        "def load():\n"
        '    return importlib.import_module("mymod")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(app_py)

    entry = next(current for current in payload["imports"] if current["module"] == "mymod")
    assert entry["dynamic"] is True
    assert entry["dynamic_unresolved"] is False
    assert entry["resolved"] == str(mymod_path.resolve())
    assert entry["external"] is False
    assert entry["provenance"] == ["sys-path-insert"]


def test_build_file_importers_dynamic_import_finds_importer_through_sys_path_insert_root(
    tmp_path: Path,
) -> None:
    """Reverse direction of the composition-with-#152 test above: `tg importers` must find the
    sys.path-hacking consumer as a confirmed importer via its DYNAMIC `import_module(...)` call,
    with the sys-path-hack provenance honestly reported on the edge (mirrors the static #155 fix
    at `test_build_file_importers_finds_sys_path_insert_hacked_importer`)."""
    project = tmp_path / "project"
    lib_dir = project / "lib"
    lib_dir.mkdir(parents=True)
    mymod_path = lib_dir / "mymod.py"
    mymod_path.write_text("def x():\n    return 1\n", encoding="utf-8")
    app_py = project / "app.py"
    app_py.write_text(
        "import sys, os, importlib\n"
        'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))\n'
        "def load():\n"
        '    return importlib.import_module("mymod")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_importers(mymod_path, project)

    assert str(app_py.resolve()) in set(payload["importer_files"])
    edge = next(
        current for current in payload["importers"] if current["file"] == str(app_py.resolve())
    )
    assert edge["dynamic"] is True
    assert edge["dynamic_unresolved"] is False
    assert edge["module"] == "mymod"
    assert edge["path_provenance"] == "sys-path-insert"


def test_build_file_importers_recalls_bare_import_module_call(tmp_path: Path) -> None:
    """Reverse-direction coverage for the bare `from importlib import import_module` alias form
    -- the forward side already covers this
    (`test_build_file_imports_detects_bare_import_module_call`); the reverse side had no
    dedicated test (only the `importlib.import_module` attribute form did, via
    `test_build_file_importers_recalls_dynamic_importlib_import_module`)."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = pkg / "helpers.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "loader.py"
    consumer.write_text(
        "from importlib import import_module\n\n"
        "def load():\n"
        '    return import_module("pkg.helpers")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_importers(target, project)

    assert str(consumer.resolve()) in set(payload["importer_files"])
    edge = next(
        current for current in payload["importers"] if current["file"] == str(consumer.resolve())
    )
    assert edge["dynamic"] is True
    assert edge["dynamic_unresolved"] is False
    assert edge["module"] == "pkg.helpers"


def test_build_file_importers_recalls_dunder_import_to_local_file(tmp_path: Path) -> None:
    """Reverse-direction coverage for `__import__(...)` resolving to a LOCAL repo file -- the
    existing `__import__` reverse test only exercised the JS `import(...)` call form; the
    existing Python `__import__` test only exercised the forward direction against a stdlib name
    (`json`, always external)."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = pkg / "helpers.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "loader.py"
    consumer.write_text('mod = __import__("pkg.helpers")\n', encoding="utf-8")

    payload = repo_map.build_file_importers(target, project)

    assert str(consumer.resolve()) in set(payload["importer_files"])
    edge = next(
        current for current in payload["importers"] if current["file"] == str(consumer.resolve())
    )
    assert edge["dynamic"] is True
    assert edge["dynamic_unresolved"] is False
    assert edge["module"] == "pkg.helpers"


def test_build_file_imports_detects_dynamic_import_nested_in_conditional_inside_function(
    tmp_path: Path,
) -> None:
    """Deeper-nesting recall: a dynamic-import call two scopes down (function -> if -> try), not
    just directly in a function body like the other fixtures in this file -- `ast.walk` visits
    every depth uniformly, this locks that in explicitly."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = pkg / "helpers.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "loader.py"
    consumer.write_text(
        "import importlib\n\n"
        "def load(flag):\n"
        "    if flag:\n"
        "        try:\n"
        '            return importlib.import_module("pkg.helpers")\n'
        "        except ImportError:\n"
        "            return None\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "pkg.helpers")
    assert entry["dynamic"] is True
    assert entry["resolved"] == str(target.resolve())
    assert entry["line"] == 6


def test_build_file_imports_detects_dynamic_import_at_module_top_level(tmp_path: Path) -> None:
    """Opposite extreme from the nested fixtures: a dynamic-import call with NO function wrapper
    at all, directly at module scope -- `ast.walk` doesn't require nesting either."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = pkg / "helpers.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "loader.py"
    consumer.write_text(
        'import importlib\nmod = importlib.import_module("pkg.helpers")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "pkg.helpers")
    assert entry["dynamic"] is True
    assert entry["resolved"] == str(target.resolve())
    assert entry["line"] == 2


def test_build_file_imports_unresolvable_dynamic_literal_stays_external(tmp_path: Path) -> None:
    """A literal (resolvable-in-principle) module name that simply doesn't exist anywhere in the
    search roots must be honestly `external`, decoupled from the existing dunder-import test's
    stdlib-name ambiguity (`json` is external partly because it's a real stdlib module tg doesn't
    special-case -- this uses a name that is definitely not any real package)."""
    project = tmp_path / "project"
    project.mkdir()
    consumer = project / "app.py"
    consumer.write_text(
        "import importlib\n"
        'mod = importlib.import_module("totally.nonexistent.repo_local_module")\n',
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    entry = next(
        current
        for current in payload["imports"]
        if current["module"] == "totally.nonexistent.repo_local_module"
    )
    assert entry["dynamic"] is True
    assert entry["dynamic_unresolved"] is False
    assert entry["external"] is True
    assert entry["resolved"] is None
    assert "totally.nonexistent.repo_local_module" in payload["external_modules"]


# ---------------------------------------------------------------------------------------------
# #703 gate NIT-1 (banked follow-up, independent Opus gate on PR #703 -- "SHIP-WITH-NITS"):
# `_python_imports_and_symbols`'s prefilter emission (repo_map.py:1988, just above the false-edge
# test block above) keys on `dynamic_entry["module"]` truthiness ALONE, ignoring
# `dynamic_unresolved` -- so the SAME unresolved relative literal (`".sibling"`) that
# `test_build_file_imports_relative_import_module_literal_stays_external_no_false_edge` and
# `test_build_file_importers_relative_import_module_literal_asserts_no_edge` above prove stays
# honestly EXTERNAL through the precise forward/reverse resolvers still lands in `imports_by_file`
# -- the alias graph `tg blast-radius`'s reverse SCORING prefilter reads. `_import_alias_
# candidates(".sibling")` -> `{".sibling", "sibling"}`, and the substring test inside
# `_import_graph_bonus` (repo_map.py:~7827) then fuzzy-matches ANY top-level `sibling.py` --
# planting the loader file into blast-radius `affected_files`/`radius_files` for a symbol DEFINED
# in the decoy `sibling.py`, even though the precise edge (proven above) correctly excludes it.
# `affected_files` is a deliberately broad proximity superset, not the exact edge list -- a
# QUALITY tightening, not a correctness emergency -- but narrowing the graph can reorder
# `dependent_files` for the LEGITIMATE dependents (repo_map.py:7916-7924), so the gate mandated a
# ranking PIN test before the guard changes anything.
# ---------------------------------------------------------------------------------------------


def _build_decoy_dynamic_import_blast_radius_project(tmp_path: Path) -> dict[str, Path]:
    """Mirrors the exact #703 decoy shape (`test_build_file_imports_relative_import_module_
    literal_stays_external_no_false_edge` above): a top-level `sibling.py` decoy (which also
    defines the symbol this block runs `tg blast-radius` against) plus a `pkg/subpkg/loader.py`
    whose ONLY reference to it is the unresolved RELATIVE dynamic literal
    `import_module(".sibling", package="pkg.subpkg")`. Adds a genuine 2-hop static importer chain
    -- `consumer.py` calls the decoy's symbol directly (depth 1, a real caller); `chain_consumer.py`
    calls `consumer.py`, never referencing the decoy directly (depth 2, graph-only) -- plus 2
    bystander files with no import relationship to the decoy at all."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    subpkg = pkg / "subpkg"
    subpkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (subpkg / "__init__.py").write_text("", encoding="utf-8")

    decoy = project / "sibling.py"
    decoy.write_text("def decoy_target():\n    return 1\n", encoding="utf-8")

    loader = subpkg / "loader.py"
    loader.write_text(
        "from importlib import import_module\n\n"
        "def load():\n"
        '    return import_module(".sibling", package="pkg.subpkg")\n',
        encoding="utf-8",
    )

    consumer = project / "consumer.py"
    consumer.write_text(
        "from sibling import decoy_target\n\ndef use_it():\n    return decoy_target()\n",
        encoding="utf-8",
    )

    chain_consumer = project / "chain_consumer.py"
    chain_consumer.write_text(
        "from consumer import use_it\n\ndef use_it_too():\n    return use_it()\n",
        encoding="utf-8",
    )

    bystander_one = project / "bystander_one.py"
    bystander_one.write_text("def noop():\n    return 0\n", encoding="utf-8")
    bystander_two = project / "bystander_two.py"
    bystander_two.write_text("def noop_two():\n    return 0\n", encoding="utf-8")

    return {
        "project": project,
        "decoy": decoy,
        "loader": loader,
        "consumer": consumer,
        "chain_consumer": chain_consumer,
        "bystander_one": bystander_one,
        "bystander_two": bystander_two,
    }


def test_blast_radius_legitimate_dependent_ranking_pin(tmp_path: Path) -> None:
    """THE PIN (gate NIT-1 mandated order, step 1): membership + RELATIVE order for the
    legitimately-related files must survive the repo_map.py:1988 guard fix byte-identical --
    narrowing the reverse-scoring graph can reorder `dependent_files` (repo_map.py:7916-7924), and
    this test exists to CATCH that if it ever happens, not to assert what the order should be.
    Deliberately does NOT assert anything about the decoy's fuzzy-matched loader file (present in
    `dependent_files` on the unfixed base, between `consumer` and `chain_consumer` -- see the gate
    finding above) -- that is `test_blast_radius_excludes_unresolved_dynamic_literal_fuzzy_match`
    below. Must stay GREEN both before and after the guard fix; this is the pin, not a red test."""
    paths = _build_decoy_dynamic_import_blast_radius_project(tmp_path)

    payload = repo_map.build_symbol_blast_radius_render("decoy_target", paths["project"])

    affected_files = set(payload["affected_files"])
    decoy_str = str(paths["decoy"].resolve())
    consumer_str = str(paths["consumer"].resolve())
    chain_str = str(paths["chain_consumer"].resolve())
    bystander_strs = {str(paths["bystander_one"].resolve()), str(paths["bystander_two"].resolve())}

    # Membership: the definition + the genuine 2-hop static chain are always present; unrelated
    # bystanders (zero import relationship to the decoy) never enter the graph at all.
    assert {decoy_str, consumer_str, chain_str} <= affected_files
    assert bystander_strs.isdisjoint(affected_files)

    # Order: the direct depth-1 caller must rank ahead of the depth-2 transitive dependent in
    # `dependent_files` -- a RELATIVE check (not a hardcoded literal list), because the loader's
    # own position in this list is exactly what the guard fix is expected to remove.
    dependent_files = list(payload["edit_plan_seed"]["dependent_files"])
    assert consumer_str in dependent_files
    assert chain_str in dependent_files
    assert dependent_files.index(consumer_str) < dependent_files.index(chain_str)


def test_blast_radius_excludes_unresolved_dynamic_literal_fuzzy_match(tmp_path: Path) -> None:
    """THE TIGHTENING (gate NIT-1 mandated order, step 4): a file whose ONLY reference to the
    decoy is an UNRESOLVED dynamic literal must never enter `tg blast-radius` `affected_files` (or
    the edit-plan seed's `dependent_files`) via the reverse-scoring prefilter's fuzzy alias match
    -- the precise `tg importers` edge already excludes it
    (`test_build_file_importers_relative_import_module_literal_asserts_no_edge` above); this pins
    the same honesty for the proximity-SCORING consumer. RED on the unfixed base (the loader IS
    present, proving the gate's finding is real, not hypothetical); GREEN after the
    repo_map.py:1988 guard fix."""
    paths = _build_decoy_dynamic_import_blast_radius_project(tmp_path)

    payload = repo_map.build_symbol_blast_radius_render("decoy_target", paths["project"])

    loader_str = str(paths["loader"].resolve())
    assert loader_str not in set(payload["affected_files"])
    assert loader_str not in payload["edit_plan_seed"]["dependent_files"]


# ---------------------------------------------------------------------------------------------
# Proximity-tiered reverse-import candidate ordering. Dogfood flap (v1.81.15 PASS
# -> v1.81.17 INCOMPLETE "0 importers @ 330/1035 files scanned" on a 50k-file WSL multi-repo
# workspace) traced to `build_file_importers_from_map` slicing its CALLER_SCAN_FILE_CEILING /
# --deadline budget off a PLAIN lexicographic path sort (`sorted(reverse_map.get(target_file,
# set()))`), which buckets candidates by absolute-path string -- i.e. by REPO NAME alphabetically
# on a multi-repo ROOT -- with zero preference for TARGET's own repo. `_tier_reverse_importer_candidates`
# replaces that with a 4-tier proximity order (same dir/ancestor dirs < rest of project < other
# project same language < everything else) so a partial scan covers the highest-yield candidates
# first, while an unbounded scan still finds the identical result set.
# ---------------------------------------------------------------------------------------------


def test_tier_reverse_importer_candidates_orders_by_proximity(tmp_path: Path) -> None:
    """Direct unit coverage of the 4 tiers: same-dir/ancestor-dir (0) < rest of same project (1)
    < other project, same language (2) < everything else (3); stable path sort within a tier."""
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    pkg = proj / "src" / "pkg"
    pkg.mkdir(parents=True)
    target = pkg / "target.py"
    target.write_text("", encoding="utf-8")

    sibling = pkg / "sibling.py"  # tier 0: same directory as target
    sibling.write_text("", encoding="utf-8")
    top_level_sibling = proj / "src" / "top_level_sibling.py"  # tier 0: target's ancestor dir
    top_level_sibling.write_text("", encoding="utf-8")
    same_project_elsewhere = proj / "tests" / "test_target.py"  # tier 1: same project, NOT an
    same_project_elsewhere.parent.mkdir(parents=True)  # ancestor of target's own directory
    same_project_elsewhere.write_text("", encoding="utf-8")
    other_repo_same_language = tmp_path / "other_proj" / "other.py"  # tier 2: other repo, .py
    other_repo_same_language.parent.mkdir(parents=True)
    other_repo_same_language.write_text("", encoding="utf-8")
    other_repo_other_language = tmp_path / "other_proj" / "notes.md"  # tier 3: other repo, .md
    other_repo_other_language.write_text("", encoding="utf-8")

    candidates = {
        str(sibling.resolve()),
        str(top_level_sibling.resolve()),
        str(same_project_elsewhere.resolve()),
        str(other_repo_same_language.resolve()),
        str(other_repo_other_language.resolve()),
    }

    ordered = repo_map._tier_reverse_importer_candidates(candidates, str(target))

    assert set(ordered[:2]) == {str(sibling.resolve()), str(top_level_sibling.resolve())}
    assert ordered[2] == str(same_project_elsewhere.resolve())
    assert ordered[3] == str(other_repo_same_language.resolve())
    assert ordered[4] == str(other_repo_other_language.resolve())


def test_tier_reverse_importer_candidates_is_deterministic(tmp_path: Path) -> None:
    """Two calls over the same candidate MEMBERSHIP produce byte-identical output -- guaranteed
    by construction (the function always ends in a full `sorted(..., key=(tier, path))`, a total
    order with no ties beyond the path string itself), but pinned here as an explicit regression
    guard against a future change that stops fully sorting the result."""
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    target = proj / "target.py"
    target.write_text("", encoding="utf-8")
    paths: list[str] = []
    for idx in range(30):
        current = proj / f"file_{idx:02d}.py"
        current.write_text("", encoding="utf-8")
        paths.append(str(current.resolve()))

    first = repo_map._tier_reverse_importer_candidates(set(paths), str(target))
    second = repo_map._tier_reverse_importer_candidates(set(paths), str(target))

    assert first == second
    assert first == sorted(paths)  # all same-project here -- collapses to a plain path sort


def _build_synthetic_multi_repo_workspace(
    tmp_path: Path,
    *,
    decoy_repo_count: int = 36,
    decoy_files_per_repo: int = 10,
) -> dict[str, Any]:
    """A ~40-repo/few-hundred-file synthetic ROOT for the importers ceiling-bounded-scan
    coverage: `decoy_repo_count` unrelated repos, each with several files that ALIAS-prefilter
    match the target via a plain `import helpers` (the real `_reverse_importers` prefilter keys
    on basename only -- see `_module_aliases_for_path` -- so this is exactly the kind of
    same-named-but-unrelated noise a huge multi-repo ROOT produces), one importer reached only
    from a DISTANT repo via a sys.path hack, and the target's own small repo with 3 real
    importers (one same-directory, two elsewhere in the same project). Decoy repo names
    ("repo_00".."repo_NN") are chosen to sort BEFORE the target repo name ("zzz_target_repo") in
    plain lexicographic order, so a pre-fix plain-alphabetical candidate sort exhausts a partial
    budget entirely on decoys before ever reaching a real importer -- reproducing the reported
    flap deterministically."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    target_repo = workspace / "zzz_target_repo"
    (target_repo / ".git").mkdir(parents=True)
    pkg = target_repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target_file = pkg / "helpers.py"
    target_file.write_text("def foo():\n    return 1\n", encoding="utf-8")

    same_dir_importer = pkg / "main.py"  # tier 0
    same_dir_importer.write_text("import pkg.helpers\n", encoding="utf-8")

    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    nested_importer = sub / "consumer.py"  # tier 1 (not an ancestor dir of target's own dir)
    nested_importer.write_text("from pkg.helpers import foo\n", encoding="utf-8")

    other = target_repo / "other"
    other.mkdir()
    sibling_importer = other / "another_consumer.py"  # tier 1 (sibling subtree, same project)
    sibling_importer.write_text("import pkg.helpers\n", encoding="utf-8")

    same_repo_importers = {
        str(same_dir_importer.resolve()),
        str(nested_importer.resolve()),
        str(sibling_importer.resolve()),
    }

    decoy_repo_dirs = []
    for repo_idx in range(decoy_repo_count):
        repo_dir = workspace / f"repo_{repo_idx:02d}"
        repo_dir.mkdir()
        decoy_repo_dirs.append(repo_dir)
        for file_idx in range(decoy_files_per_repo):
            decoy = repo_dir / f"decoy_{file_idx:02d}.py"
            # Aliases to "helpers" (target's basename) via _module_aliases_for_path, so this
            # file enters the reverse-import PREFILTER -- but nothing at any of ITS OWN
            # candidate roots is a real `helpers.py`, so it never CONFIRMS as a real edge.
            decoy.write_text("import helpers\n", encoding="utf-8")

    distant_importer = decoy_repo_dirs[0] / "distant_consumer.py"
    # Only a statically-resolvable sys.path hack lets an import from a SIBLING repo resolve --
    # the natural "ancestor directory up to repo root" search (_python_candidate_roots) never
    # crosses a repo boundary on its own.
    distant_importer.write_text(
        "import sys, os\n"
        'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "zzz_target_repo", "pkg"))\n'
        "from helpers import foo\n",
        encoding="utf-8",
    )

    total_candidates = decoy_repo_count * decoy_files_per_repo + 1 + len(same_repo_importers)
    return {
        "workspace": workspace,
        "target_file": target_file,
        "same_repo_importers": same_repo_importers,
        "distant_importer": str(distant_importer.resolve()),
        "total_candidates": total_candidates,
    }


def test_build_file_importers_ceiling_bounded_scan_finds_same_repo_importers_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof: on a ~40-repo/few-hundred-file ROOT, a CALLER_SCAN_FILE_CEILING
    bounded to ~30% of the prefiltered candidates still finds all 3 real same-repo importers
    (proximity-tiered first) and honestly stamps the result incomplete -- the exact shape of the
    "0 importers @ 330/1035 files scanned" dogfood flap, now with the real importers surviving
    the cut instead of being stranded behind alphabetically-earlier decoy repos."""
    fixture = _build_synthetic_multi_repo_workspace(tmp_path)
    budget = max(1, int(fixture["total_candidates"] * 0.3))
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", budget)

    payload = repo_map.build_file_importers(fixture["target_file"], fixture["workspace"])

    assert payload["result_incomplete"] is True
    caller_scan_limit = payload["caller_scan_limit"]
    assert caller_scan_limit["possibly_truncated"] is True
    assert caller_scan_limit["files_total"] == fixture["total_candidates"]
    importer_files = set(payload["importer_files"])
    assert fixture["same_repo_importers"] <= importer_files


def test_build_file_importers_ceiling_bounded_scan_misses_importers_without_tiering(
    tmp_path: Path,
) -> None:
    """Regression guard / causal proof for the fix above: simulate the PRE-FIX plain
    lexicographic order (what `prefiltered = sorted(reverse_map.get(target_file, set()))` used
    to do) over the IDENTICAL candidate set and budget -- the 3 same-repo importers, whose repo
    name ("zzz_target_repo") sorts AFTER all decoy repo names ("repo_00".."repo_35"), are
    entirely stranded past the cut line. This is the exact reported dogfood flap this fix closes."""
    fixture = _build_synthetic_multi_repo_workspace(tmp_path)
    budget = max(1, int(fixture["total_candidates"] * 0.3))

    rmap = repo_map.build_repo_map(fixture["workspace"])
    all_files = [str(current) for current in rmap.get("files", [])]
    imports_by_file = {
        str(current["file"]): list(
            dict.fromkeys(str(name) for name in current.get("imports", []) if name)
        )
        for current in rmap.get("imports", [])
    }
    reverse_map = repo_map._reverse_importers(
        all_files, imports_by_file, include_directory_index_aliases=True
    )
    target_str = str(fixture["target_file"].resolve())
    pre_fix_order = sorted(reverse_map.get(target_str, set()))
    assert len(pre_fix_order) == fixture["total_candidates"]
    pre_fix_bounded = set(pre_fix_order[:budget])

    assert fixture["same_repo_importers"].isdisjoint(pre_fix_bounded)


def test_build_file_importers_unbounded_result_set_unaffected_by_tiering(tmp_path: Path) -> None:
    """Regression guard: when nothing is truncated (no ceiling/deadline hit), the tiered
    candidate order is a pure REORDERING of the exact same membership -- the found result set
    must equal what the OLD plain lexicographic order would ALSO find, since every candidate
    gets confirmed either way and `build_file_importers_from_map` re-sorts `edges` by
    (file, line) before returning."""
    fixture = _build_synthetic_multi_repo_workspace(tmp_path)
    assert fixture["total_candidates"] < repo_map.CALLER_SCAN_FILE_CEILING  # no ceiling hit here

    payload = repo_map.build_file_importers(fixture["target_file"], fixture["workspace"])

    assert payload.get("result_incomplete") is not True
    importer_files = set(payload["importer_files"])
    expected = fixture["same_repo_importers"] | {fixture["distant_importer"]}
    assert importer_files == expected


# ---------------------------------------------------------------------------------------------
# opt10 campaign #4: `tg imports` speed bundle.
#
# F4.3: `_python_module_candidates` (the Python absolute-import resolver, shared by the forward
# `tg imports` primitive and the reverse `tg importers` confirm step) fast-paths a bare
# top-level stdlib import (`import os` / `import sys` / `import json` -- 59-100% of imports in
# sampled real files) past its ~10-12-candidate filesystem probe, returning the SAME
# provenance/confidence shape the general path always sets for a level==0 absolute import,
# unconditionally, before any candidate is even constructed. THE CORRECTNESS RISK: a repo that
# SHADOWS a stdlib name with a same-named local top-level module/package must still resolve to
# THAT local file -- never silently swallowed by the fast path. The fast path is therefore gated
# behind a cheap `.is_file()`/`.is_dir()` shadow probe over the exact same roots
# `_python_candidate_roots` already computes for the general path, and narrowed to
# `len(parts) == 1` (a dotted `import os.path` always takes the general path).
#
# F4.2: `_python_imports_with_lines` used to do TWO full-tree `ast.walk()` passes over the same
# parsed AST -- its own static-import scan, plus a second whole-tree walk buried inside
# `_python_dynamic_import_entries` (called for the dynamic-import shape). The per-node dynamic-
# import-detection logic was extracted into `_python_dynamic_import_entry_for_call` (a pure,
# stateless per-node check) so `_python_imports_with_lines` can fold it into its EXISTING walk
# instead of triggering a second one -- while `_python_dynamic_import_entries` itself, at the
# time, stayed UNCHANGED (same signature, same behavior, still doing its own single walk) for
# its OTHER caller, `_python_imports_and_symbols` (the reverse-import alias-graph prefilter).
#
# opt10 lever-1 (see tests further below): that OTHER caller was later migrated to call
# `_python_dynamic_import_entry_for_call` directly too, folding its own dynamic-import check into
# ITS single merged walk -- leaving `_python_dynamic_import_entries` with zero callers, so it was
# removed as dead code. `test_python_dynamic_import_entries_unchanged_after_extraction` (the
# regression guard for this function's post-extraction standalone behavior) was removed alongside
# it for the same reason: there is no longer any caller whose behavior it could regress.
# ---------------------------------------------------------------------------------------------


def test_python_module_candidates_stdlib_fastpath_returns_general_path_shape(
    tmp_path: Path,
) -> None:
    """F4.3 direct unit test: a bare top-level stdlib import with no local shadow must get an
    empty `paths` list plus EXACTLY the `provenance`/`confidence`/`path_provenance` values the
    general (non-relative) branch below always sets for ANY level==0 absolute import -- so
    `_resolve_raw_import_entry`/`_python_module_match_details` read the identical values off
    this dict as they would off the general path's result for the same genuinely-external
    module (captured from the pre-change baseline; see the PR body)."""
    project = tmp_path / "project"
    project.mkdir()
    importer = project / "app.py"
    importer.write_text("import os\n", encoding="utf-8")

    for module_name in ("os", "sys", "json"):
        info = repo_map._python_module_candidates(importer, module_name, project)
        assert info == {
            "paths": [],
            "provenance": ["python-path-heuristic"],
            "confidence": 0.7,
            "path_provenance": {},
        }, module_name


def test_python_module_candidates_dotted_stdlib_access_does_not_take_fastpath(
    tmp_path: Path,
) -> None:
    """The fast path is narrowed to `len(parts) == 1` -- a dotted stdlib access like
    `collections.abc` must still go through the general multi-root candidate search, proven here
    by a non-empty `paths` list (only the general path ever populates candidates)."""
    project = tmp_path / "project"
    project.mkdir()
    importer = project / "app.py"
    importer.write_text("import collections.abc\n", encoding="utf-8")

    info = repo_map._python_module_candidates(importer, "collections.abc", project)

    assert info["paths"] != []
    assert info["provenance"] == ["python-path-heuristic"]
    assert info["confidence"] == 0.7


def test_build_file_imports_stdlib_shadowed_by_local_module_resolves_to_local_file(
    tmp_path: Path,
) -> None:
    """THE load-bearing F4.3 correctness test. A repo that ships a same-named top-level module
    shadowing a stdlib name (here: a local `json.py` at the repo root) must still resolve
    `import json` to THAT LOCAL FILE, never to the stdlib fast-path's external/unresolved shape.

    Proven to actually exercise the shadow gate (not just pass vacuously): temporarily
    stripping the shadow-probe loop out of `_python_module_candidates` down to an unconditional
    `if level == 0 and len(parts) == 1 and parts[0] in sys.stdlib_module_names: return
    {"paths": [], ...}` makes this test FAIL with `external=True, resolved=None` instead of
    resolving to the local file -- the probe loop over `_python_candidate_roots` is what makes
    it pass."""
    project = tmp_path / "project"
    project.mkdir()
    local_json = project / "json.py"
    local_json.write_text("def local_marker():\n    return 'LOCAL'\n", encoding="utf-8")
    consumer = project / "app.py"
    consumer.write_text("import json\n", encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "json")
    assert entry["external"] is False
    assert entry["resolved"] == str(local_json.resolve())
    assert entry["resolution_confidence"] == 0.7


def test_build_file_imports_stdlib_shadowed_by_local_package_resolves_to_local_file(
    tmp_path: Path,
) -> None:
    """Sibling shadow shape: a local PACKAGE (a directory with `__init__.py`) rather than a bare
    module file, shadowing a stdlib name -- `_python_module_candidates`'s general path resolves
    a bare `import queue` to either `<root>/queue.py` OR `<root>/queue/__init__.py`, so the
    shadow probe must catch both shapes, not just the module-file one."""
    project = tmp_path / "project"
    pkg_dir = project / "queue"
    pkg_dir.mkdir(parents=True)
    local_init = pkg_dir / "__init__.py"
    local_init.write_text("def local_marker():\n    return 'LOCAL_PKG'\n", encoding="utf-8")
    consumer = project / "app.py"
    consumer.write_text("import queue\n", encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    entry = next(current for current in payload["imports"] if current["module"] == "queue")
    assert entry["external"] is False
    assert entry["resolved"] == str(local_init.resolve())


def test_build_file_imports_relative_import_of_stdlib_named_sibling_still_resolves(
    tmp_path: Path,
) -> None:
    """The stdlib fast-path only applies when `level == 0` (absolute import) -- a RELATIVE
    import of a stdlib-named sibling module (`from . import json`, `level=1`) must resolve to
    the local sibling file exactly as before, never mistaken for the stdlib fast-path shape."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    sibling = pkg / "json.py"
    sibling.write_text("def x():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text("from . import json\n", encoding="utf-8")

    payload = repo_map.build_file_imports(consumer)

    entry = payload["imports"][0]
    assert entry["module"] == "json"
    assert entry["resolved"] == str(sibling.resolve())
    assert entry["external"] is False
    assert entry["provenance"] == ["relative"]


def test_build_file_imports_mixed_imports_results_identical_to_pre_fastpath_baseline(
    tmp_path: Path,
) -> None:
    """Results-identical golden test for opt10 #4 (F4.3 + F4.2): a file with a realistic mix of
    bare stdlib imports (single-part, the fast-path's target shape), a multi-part stdlib access,
    a non-stdlib external, a real local dotted import, and a relative import. Expected values
    were captured by running `build_file_imports` against the UNMODIFIED pre-change code on this
    exact fixture (see the PR body for the captured receipts) -- every field
    (module/line/resolved/external/provenance/resolution_confidence) must stay byte-identical
    after the change; only the WORK done to get there should shrink."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    consumer = pkg / "main.py"
    consumer.write_text(
        "import os\n"
        "import sys\n"
        "import json\n"
        "import collections.abc\n"
        "import numpy\n"
        "import pkg.helpers\n"
        "from . import helpers\n",
        encoding="utf-8",
    )

    payload = repo_map.build_file_imports(consumer)

    resolved_helpers = str(helpers_path.resolve())
    expected = [
        {
            "module": "os",
            "line": 1,
            "resolved": None,
            "provenance": ["python-path-heuristic"],
            "resolution_confidence": 0.0,
            "external": True,
        },
        {
            "module": "sys",
            "line": 2,
            "resolved": None,
            "provenance": ["python-path-heuristic"],
            "resolution_confidence": 0.0,
            "external": True,
        },
        {
            "module": "json",
            "line": 3,
            "resolved": None,
            "provenance": ["python-path-heuristic"],
            "resolution_confidence": 0.0,
            "external": True,
        },
        {
            "module": "collections.abc",
            "line": 4,
            "resolved": None,
            "provenance": ["python-path-heuristic"],
            "resolution_confidence": 0.0,
            "external": True,
        },
        {
            "module": "numpy",
            "line": 5,
            "resolved": None,
            "provenance": ["python-path-heuristic"],
            "resolution_confidence": 0.0,
            "external": True,
        },
        {
            "module": "pkg.helpers",
            "line": 6,
            "resolved": resolved_helpers,
            "provenance": ["python-path-heuristic"],
            "resolution_confidence": 0.7,
            "external": False,
        },
        {
            "module": "helpers",
            "line": 7,
            "resolved": resolved_helpers,
            "provenance": ["relative"],
            "resolution_confidence": 1.0,
            "external": False,
        },
    ]

    actual = [
        {
            "module": entry["module"],
            "line": entry["line"],
            "resolved": entry["resolved"],
            "provenance": entry["provenance"],
            "resolution_confidence": entry["resolution_confidence"],
            "external": entry["external"],
        }
        for entry in payload["imports"]
    ]
    assert actual == expected


def test_python_imports_with_lines_merges_dynamic_walk_into_single_ast_walk_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4.2 walk-count assertion: a file mixing STATIC imports (module top-level and nested
    inside an `if TYPE_CHECKING:` block) with DYNAMIC import calls (a non-literal
    `importlib.import_module(name)` and a literal `__import__('re')`) must produce the exact
    same entries, in the exact same order (all static entries first in AST-discovery order, then
    all dynamic entries in AST-discovery order -- matching the pre-merge
    `entries.extend(_python_dynamic_import_entries(tree))` shape), while calling `ast.walk`
    exactly ONCE per file (was two: the static loop's own walk, plus a second whole-tree walk
    buried inside `_python_dynamic_import_entries`)."""
    import ast as ast_module

    project = tmp_path / "project"
    project.mkdir()
    source_file = project / "mixed.py"
    source_file.write_text(
        "import os\n"
        "from typing import TYPE_CHECKING\n"
        "import importlib\n"
        "\n"
        "def load(name):\n"
        "    return importlib.import_module(name)\n"
        "\n"
        "def load_builtin():\n"
        "    return __import__('re')\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    import collections.abc\n",
        encoding="utf-8",
    )

    walk_calls = 0
    real_walk = ast_module.walk

    def counting_walk(tree):
        nonlocal walk_calls
        walk_calls += 1
        return real_walk(tree)

    # `repo_map` did `import ast` at module scope, so `repo_map.ast is ast_module` -- patching
    # the shared stdlib module object's `walk` attribute affects the call
    # `_python_imports_with_lines` makes internally too.
    monkeypatch.setattr(ast_module, "walk", counting_walk)

    entries = repo_map._python_imports_with_lines(source_file)

    assert walk_calls == 1, f"expected exactly 1 ast.walk call, got {walk_calls}"
    assert entries == [
        {"module": "os", "line": 1, "level": 0},
        {"module": "typing", "line": 2, "level": 0},
        {"module": "importlib", "line": 3, "level": 0},
        {"module": "collections.abc", "line": 12, "level": 0},
        {
            "module": "",
            "line": 6,
            "level": 0,
            "dynamic": True,
            "dynamic_unresolved": True,
        },
        {
            "module": "re",
            "line": 9,
            "level": 0,
            "dynamic": True,
            "dynamic_unresolved": False,
        },
    ]

    # NOTE: `test_python_dynamic_import_entries_unchanged_after_extraction` used to live here --
    # a regression guard pinning `_python_dynamic_import_entries`'s standalone behavior for its
    # then-last caller, `_python_imports_and_symbols`. opt10 lever-1 (below) migrated that caller
    # to call `_python_dynamic_import_entry_for_call` directly instead, leaving
    # `_python_dynamic_import_entries` with zero callers; it was removed as dead code, and this
    # test was removed with it since there is no longer any caller whose behavior it could pin.


def test_python_imports_and_symbols_merges_all_three_walks_into_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lever-1 walk-count assertion (mirrors
    test_python_imports_with_lines_merges_dynamic_walk_into_single_ast_walk_pass above):
    `_python_imports_and_symbols` used to call `ast.walk(tree)` THREE separate times -- once for
    the imports scan (module-level function body, ~1953), once for the symbols scan (~1977), and
    once more buried inside `_python_dynamic_import_entries` (called from a third loop, ~2026) --
    all three walking the identical tree. This was `_python_dynamic_import_entries`'s last
    remaining caller; once migrated to call the per-node `_python_dynamic_import_entry_for_call`
    helper directly (like the merged walk below does), that whole-tree function had zero callers
    left and was removed as dead code. A file mixing a module-top-level import, a
    TYPE_CHECKING-nested import, a relative `from . import x`, a class, a sync function, an
    async function, a RESOLVABLE dynamic `importlib.import_module("os.path")` call, and an
    UNRESOLVABLE dynamic `import_module(name)` call (non-literal argument) must produce the exact
    same `(imports, symbols)` tuple as before the merge (values captured from the pre-merge
    implementation), while calling `ast.walk` exactly ONCE."""
    import ast as ast_module

    project = tmp_path / "project"
    project.mkdir()
    source_file = project / "mixed.py"
    source_file.write_text(
        "import os\n"
        "from typing import TYPE_CHECKING\n"
        "from importlib import import_module\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    import collections.abc\n"
        "\n"
        "from . import sibling\n"
        "\n"
        "\n"
        "class Widget:\n"
        "    pass\n"
        "\n"
        "\n"
        "def build(name):\n"
        "    return import_module('os.path')\n"
        "\n"
        "\n"
        "async def build_async(name):\n"
        "    return import_module(name)\n",
        encoding="utf-8",
    )

    walk_calls = 0
    real_walk = ast_module.walk

    def counting_walk(tree):
        nonlocal walk_calls
        walk_calls += 1
        return real_walk(tree)

    # `repo_map` did `import ast` at module scope, so `repo_map.ast is ast_module` -- patching
    # the shared stdlib module object's `walk` attribute affects the call
    # `_python_imports_and_symbols` makes internally too (its only `ast.walk` call now that the
    # merge folds the dynamic-import check into the same walk via the per-node
    # `_python_dynamic_import_entry_for_call` helper instead of a separate whole-tree call).
    monkeypatch.setattr(ast_module, "walk", counting_walk)

    imports, symbols = repo_map._python_imports_and_symbols(source_file)

    assert walk_calls == 1, f"expected exactly 1 ast.walk call, got {walk_calls}"
    assert imports == [
        "collections.abc",
        "importlib",
        "importlib.import_module",
        "os",
        "os.path",
        "sibling",
        "typing",
        "typing.TYPE_CHECKING",
    ]
    assert symbols == [
        {
            "name": "Widget",
            "kind": "class",
            "file": str(source_file),
            "line": 11,
            "start_line": 11,
            "end_line": 12,
        },
        {
            "name": "build",
            "kind": "function",
            "file": str(source_file),
            "line": 15,
            "start_line": 15,
            "end_line": 16,
        },
        {
            "name": "build_async",
            "kind": "function",
            "file": str(source_file),
            "line": 19,
            "start_line": 19,
            "end_line": 20,
        },
    ]
