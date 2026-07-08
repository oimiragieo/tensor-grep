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
