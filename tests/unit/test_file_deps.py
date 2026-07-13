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

    importers_size = len(json.dumps(importers_payload))
    map_size = len(json.dumps(map_payload))

    assert importers_size < 0.1 * map_size, (
        f"importers payload ({importers_size}B) is not <0.1x the map payload ({map_size}B)"
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
    assert payload["importer_count"] == 0
