"""Split tests for file_deps (paths, blast radius, workspace, aliases)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import repo_map

runner = CliRunner()


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
# #703 gate NIT-1 :
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
