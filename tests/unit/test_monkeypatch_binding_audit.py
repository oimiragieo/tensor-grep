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


def test_both_setattr_forms_are_collected() -> None:
    """A collector matching one syntactic form undercounts the surface.

    Measured: a regex matching only the object form reported 658 sites where the
    AST walk finds >2000 -- which is why this asserts on the STYLE mix rather
    than on a total.
    """
    styles = {site.style for site in mpa.collect_patch_sites()}
    assert styles == {"string", "object"}, f"only collected {styles}"


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
