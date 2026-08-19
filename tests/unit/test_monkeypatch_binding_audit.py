"""Tests for the monkeypatch binding auditor -- the red arm every split wave needs.

The auditor's job is to find early-binding hazards before a module split converts
them from harmless into a silent false green. A hazard detector that cannot be
shown FIRING is worth nothing, so most of this file is controls: synthetic source
with a known hazard, and synthetic source deliberately without one.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = REPO_ROOT / "scripts" / "monkeypatch_binding_audit.py"

_spec = importlib.util.spec_from_file_location("monkeypatch_binding_audit", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
mpa = importlib.util.module_from_spec(_spec)
sys.modules["monkeypatch_binding_audit"] = mpa
_spec.loader.exec_module(mpa)


# --------------------------------------------------------------------------
# Positive controls.
# --------------------------------------------------------------------------


def test_collector_finds_a_large_real_surface() -> None:
    """An empty collection would make every 'no hazards' claim vacuous.

    This repo is known to patch heavily; a small number here means the AST walk
    broke, not that the surface is clean. The script itself exits 2 on zero for
    the same reason.
    """
    sites = mpa.collect_patch_sites()
    assert len(sites) > 500, (
        f"collected only {len(sites)} patch sites; the AST walk is probably broken. "
        "An undercount silently shrinks the surface a split has to preserve."
    )


def test_all_three_patch_forms_are_collected() -> None:
    """A collector matching one syntactic form undercounts the surface.

    This has now been wrong twice, in the same direction, which is why the
    assertion is on the STYLE MIX rather than on a total:

      * a regex matching only `setattr(mod, "X", v)` reported 658 sites where the
        AST walk finds >2100;
      * the first AST version still missed plain `mod.X = v` rebinding, and
        reported ZERO exposure for a module whose tests patch it that way
        throughout. A split brief was written on that false zero (wave 2).

    A missing style is not a smaller number -- it is a whole shape of exposure
    reported as absent.
    """
    styles = {site.style for site in mpa.collect_patch_sites()}
    assert styles == {"string", "object", "assign"}, f"only collected {styles}"


def test_control_direct_attribute_assignment_is_collected(tmp_path: Path) -> None:
    """Mutation control for Form C, the form that was missed.

    `module.X = fake` is not a monkeypatch call, so nothing about the setattr
    matcher would ever surface it -- yet it mutates the same module attribute and
    a split breaks it identically.
    """
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        "from tensor_grep.cli import repo_map\n\n\ndef test_x():\n    repo_map.build_thing = None\n",
        encoding="utf-8",
    )
    tree = mpa._parse(test_file)
    assert tree is not None
    assigns = [
        n
        for n in __import__("ast").walk(tree)
        if isinstance(n, __import__("ast").Assign)
        and isinstance(n.targets[0], __import__("ast").Attribute)
    ]
    assert len(assigns) == 1, "fixture does not contain the shape under test"
    assert assigns[0].targets[0].attr == "build_thing"


def test_known_heavily_patched_module_is_present() -> None:
    reports = mpa.build_reports(mpa.collect_patch_sites())
    assert "tensor_grep.cli.repo_map" in reports
    assert len(reports["tensor_grep.cli.repo_map"].sites) > 50


# --------------------------------------------------------------------------
# Hazard detection -- the controls that matter.
# --------------------------------------------------------------------------


def test_control_detects_an_import_by_value_hazard(tmp_path: Path) -> None:
    """RED-side control: a symbol imported by value IS reported."""
    module = tmp_path / "victim.py"
    module.write_text(
        "from .helpers import build_thing\n\ndef run():\n    return build_thing()\n",
        encoding="utf-8",
    )
    hazards = mpa.find_early_bound_imports(module, {"build_thing"})
    assert "build_thing" in hazards, (
        "an early-bound import of a patched symbol was not flagged -- this is the "
        "exact shape that turns green after a split while production reads the "
        "unpatched original"
    )


def test_control_late_attribute_lookup_is_not_a_hazard(tmp_path: Path) -> None:
    """GREEN-side control: the SAFE shape must NOT be reported.

    Without this arm the detector could flag everything and still look correct on
    the test above.
    """
    module = tmp_path / "safe.py"
    module.write_text(
        "from . import helpers\n\ndef run():\n    return helpers.build_thing()\n",
        encoding="utf-8",
    )
    assert mpa.find_early_bound_imports(module, {"build_thing"}) == {}


def test_control_unpatched_symbols_are_ignored(tmp_path: Path) -> None:
    """Only symbols tests actually patch are hazards; the rest are ordinary imports."""
    module = tmp_path / "m.py"
    module.write_text("from .helpers import unrelated\n", encoding="utf-8")
    assert mpa.find_early_bound_imports(module, {"build_thing"}) == {}


def test_control_aliased_import_is_flagged_under_its_local_name(tmp_path: Path) -> None:
    """`import X as Y` binds Y -- patching Y is what a test would do."""
    module = tmp_path / "m.py"
    module.write_text("from .helpers import build_thing as bt\n", encoding="utf-8")
    assert "bt" in mpa.find_early_bound_imports(module, {"bt"})


def test_control_unparseable_source_yields_no_false_hazards(tmp_path: Path) -> None:
    """A syntax error must not be reported as a clean module OR crash the run."""
    module = tmp_path / "broken.py"
    module.write_text("def (((\n", encoding="utf-8")
    assert mpa.find_early_bound_imports(module, {"anything"}) == {}


# --------------------------------------------------------------------------
# Classification / resolution.
# --------------------------------------------------------------------------


def test_module_to_path_resolves_a_real_module() -> None:
    resolved = mpa.module_to_path("tensor_grep.cli.repo_map")
    assert resolved is not None and resolved.is_file()
    assert resolved.name == "repo_map.py"


def test_module_to_path_returns_none_for_a_nonexistent_module() -> None:
    assert mpa.module_to_path("tensor_grep.cli.does_not_exist_xyz") is None


def test_cli_runs_clean_over_the_real_tree() -> None:
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "monkeypatch surface:" in result.stdout


def test_cli_module_filter_narrows_the_report() -> None:
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--module", "repo_map"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "repo_map" in result.stdout
    assert "tensor_grep.cli.bootstrap\n" not in result.stdout


# --------------------------------------------------------------------------
# The spec-loaded blind spot.
#
# The collector attributes patch sites to DOTTED MODULES. A file loaded via
# `spec_from_file_location` and patched on the resulting object never enters the
# tensor_grep namespace, so it reports ZERO sites -- a zero that says "safe to
# split" about a file whose every entry point is monkeypatched.
#
# That zero misled two separate refactor briefs, the second AFTER the limitation
# had been written into this tool's own PR message. These tests are the mechanism
# that replaces the note.
# --------------------------------------------------------------------------


def test_spec_loaded_targets_sees_a_substantial_population() -> None:
    """Positive control: an empty result would make every warning below vacuous."""
    targets = mpa.spec_loaded_targets()
    assert len(targets) > 3, (
        f"only found {len(targets)} spec-loaded targets; 37 test files use "
        "spec_from_file_location, so a near-empty result means the detector broke"
    )


def test_spec_loaded_targets_flags_the_file_that_misled_two_briefs() -> None:
    """The concrete regression: this file reports zero dotted-module sites."""
    targets = mpa.spec_loaded_targets()
    hit = [t for t in targets if "run_gpu_native_benchmarks" in t]
    assert hit, (
        "run_gpu_native_benchmarks.py is spec-loaded and heavily patched, but the "
        "detector no longer flags it -- the blind spot is back"
    )
    assert any("test_benchmark_scripts" in f for f in targets[hit[0]])


def test_filtered_run_warns_instead_of_reporting_a_bare_no_match() -> None:
    """`--module <spec-loaded file>` must WARN (exit 1), not say 'no match' (exit 2).

    This is the arm the first version of the fix got wrong: `main()` returned early
    on an empty dotted-module set, which is EXACTLY the state the blind spot
    produces -- so the warning never printed in the only case it existed for.
    """
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--module", "run_gpu_native"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, f"expected the spec-loaded warning path, got {result.returncode}"
    combined = result.stdout + result.stderr
    assert "SPEC-LOADED" in combined
    assert "UNRESOLVED" in combined


def test_a_genuinely_absent_module_still_reports_no_match() -> None:
    """The discriminating control.

    Without this, a detector that flagged everything would pass the test above and
    make `--module` useless -- every miss would look like a blind-spot hit.
    """
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--module", "totally_nonexistent_xyz"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, "a real miss must stay exit 2, not become a warning"
    assert "SPEC-LOADED" not in (result.stdout + result.stderr)
