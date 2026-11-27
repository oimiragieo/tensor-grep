"""Split tests for file_deps (paths, blast radius, workspace, aliases)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import repo_map
from tensor_grep.cli.main import app

runner = CliRunner()


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
# #152 fix : `sys.path.insert`/`sys.path.append` path-hacked
# modules. Before this fix, a file that made a sibling/vendored directory importable via a
# same-repo path hack (e.g. `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))`
# then `from mymod import x`) was left `external`/`resolved=None` on the forward side
# (`build_file_imports`) and its importer was invisible to the reverse side
# (`build_file_importers` reported no importer at all, the CLI's `not_found: true`).
# ---------------------------------------------------------------------------------------------


def test_build_file_imports_resolves_sys_path_insert_hacked_module(tmp_path: Path) -> None:
    """The exact dogfood repro shape: `sys.path.insert(0, os.path.join(
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
# dogfood feature 6 -- the dynamic-import LITERAL slice. The base recall (#93 SUB-1, block
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
