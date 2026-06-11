"""Regression tests for L3 (defs class/score) and L4 (impact preferred_command_reason).

L3 — build_symbol_defs / build_symbol_defs_from_map: each definition now carries
     ``class`` (enclosing class name or None) and ``score`` (float confidence).

L4 — build_symbol_impact_from_map: ``preferred_command_reason`` documents the
     by-design redirect to blast-radius with actionable detail; assessed as
     intentional architecture (impact = fast planning signal).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli.repo_map import (
    _definition_confidence_score,
    _enclosing_class_for_definition,
    build_repo_map,
    build_symbol_defs_from_map,
    build_symbol_impact_from_map,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def two_class_fixture(tmp_path: Path) -> Path:
    """A Python file with two classes each defining a method named ``run``,
    plus a bare module-level function also named ``run``."""
    (tmp_path / "two_classes.py").write_text(
        """\
class Alpha:
    def run(self):
        pass

class Beta:
    def run(self):
        pass

def run():
    pass
""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def simple_fixture(tmp_path: Path) -> Path:
    (tmp_path / "mod.py").write_text(
        """\
def process():
    pass
""",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# L3 — _enclosing_class_for_definition (unit)
# ---------------------------------------------------------------------------


class TestEnclosingClassHelper:
    def test_method_inside_class(self) -> None:
        all_symbols = [
            {
                "name": "MyClass",
                "kind": "class",
                "file": "f.py",
                "line": 1,
                "start_line": 1,
                "end_line": 10,
            },
            {
                "name": "run",
                "kind": "function",
                "file": "f.py",
                "line": 3,
                "start_line": 3,
                "end_line": 5,
            },
        ]
        defn = {"name": "run", "kind": "function", "file": "f.py", "line": 3}
        result = _enclosing_class_for_definition(defn, all_symbols)
        assert result == "MyClass"

    def test_module_level_function_returns_none(self) -> None:
        all_symbols = [
            {
                "name": "MyClass",
                "kind": "class",
                "file": "f.py",
                "line": 1,
                "start_line": 1,
                "end_line": 5,
            },
            {
                "name": "run",
                "kind": "function",
                "file": "f.py",
                "line": 10,
                "start_line": 10,
                "end_line": 12,
            },
        ]
        defn = {"name": "run", "kind": "function", "file": "f.py", "line": 10}
        result = _enclosing_class_for_definition(defn, all_symbols)
        assert result is None

    def test_different_file_ignored(self) -> None:
        all_symbols = [
            {
                "name": "MyClass",
                "kind": "class",
                "file": "other.py",
                "line": 1,
                "start_line": 1,
                "end_line": 20,
            },
        ]
        defn = {"name": "run", "kind": "function", "file": "f.py", "line": 5}
        result = _enclosing_class_for_definition(defn, all_symbols)
        assert result is None

    def test_nested_classes_returns_innermost(self) -> None:
        all_symbols = [
            {
                "name": "Outer",
                "kind": "class",
                "file": "f.py",
                "line": 1,
                "start_line": 1,
                "end_line": 20,
            },
            {
                "name": "Inner",
                "kind": "class",
                "file": "f.py",
                "line": 5,
                "start_line": 5,
                "end_line": 15,
            },
        ]
        defn = {"name": "run", "kind": "function", "file": "f.py", "line": 8}
        result = _enclosing_class_for_definition(defn, all_symbols)
        assert result == "Inner"


# ---------------------------------------------------------------------------
# L3 — _definition_confidence_score (unit)
# ---------------------------------------------------------------------------


class TestDefinitionConfidenceScore:
    def test_plain_exact_match_is_1(self) -> None:
        defn = {
            "name": "run",
            "kind": "function",
            "file": "src/mod.py",
            "line": 5,
            "provenance": "python-ast",
        }
        assert _definition_confidence_score(defn, "run") == 1.0

    def test_heuristic_provenance_reduces_score(self) -> None:
        defn = {
            "name": "run",
            "kind": "function",
            "file": "src/mod.py",
            "line": 5,
            "provenance": "regex-heuristic",
        }
        score = _definition_confidence_score(defn, "run")
        assert score < 1.0
        assert score >= 0.0

    def test_test_file_reduces_score(self) -> None:
        defn = {
            "name": "run",
            "kind": "function",
            "file": "tests/test_mod.py",
            "line": 5,
            "provenance": "python-ast",
        }
        score = _definition_confidence_score(defn, "run")
        assert score < 1.0
        assert score >= 0.0

    def test_lsp_provenance_does_not_exceed_1(self) -> None:
        defn = {
            "name": "run",
            "kind": "function",
            "file": "src/mod.py",
            "line": 5,
            "provenance": "lsp-python",
        }
        score = _definition_confidence_score(defn, "run")
        assert score <= 1.0
        assert score >= 0.0


# ---------------------------------------------------------------------------
# L3 — build_symbol_defs_from_map integration
# ---------------------------------------------------------------------------


class TestBuildSymbolDefsClassAndScore:
    def test_definitions_carry_class_field(self, two_class_fixture: Path) -> None:
        repo_map = build_repo_map(str(two_class_fixture))
        result = build_symbol_defs_from_map(repo_map, "run")
        definitions = result.get("definitions", [])
        assert len(definitions) >= 2, "Expected at least 2 definitions for 'run'"
        for defn in definitions:
            assert "class" in defn, f"Missing 'class' key in definition: {defn}"

    def test_definitions_carry_score_field(self, two_class_fixture: Path) -> None:
        repo_map = build_repo_map(str(two_class_fixture))
        result = build_symbol_defs_from_map(repo_map, "run")
        for defn in result.get("definitions", []):
            assert "score" in defn, f"Missing 'score' key in definition: {defn}"
            assert isinstance(defn["score"], float)
            assert 0.0 <= defn["score"] <= 1.0

    def test_alpha_method_gets_alpha_class(self, two_class_fixture: Path) -> None:
        repo_map = build_repo_map(str(two_class_fixture))
        result = build_symbol_defs_from_map(repo_map, "run")
        defs_by_line = {int(d["line"]): d for d in result.get("definitions", [])}
        # Alpha.run is on line 2, Beta.run on line 6, module run on line 9
        assert defs_by_line[2]["class"] == "Alpha"

    def test_beta_method_gets_beta_class(self, two_class_fixture: Path) -> None:
        repo_map = build_repo_map(str(two_class_fixture))
        result = build_symbol_defs_from_map(repo_map, "run")
        defs_by_line = {int(d["line"]): d for d in result.get("definitions", [])}
        assert defs_by_line[6]["class"] == "Beta"

    def test_module_level_function_class_is_none(self, two_class_fixture: Path) -> None:
        repo_map = build_repo_map(str(two_class_fixture))
        result = build_symbol_defs_from_map(repo_map, "run")
        defs_by_line = {int(d["line"]): d for d in result.get("definitions", [])}
        assert defs_by_line[9]["class"] is None

    def test_existing_keys_preserved(self, two_class_fixture: Path) -> None:
        repo_map = build_repo_map(str(two_class_fixture))
        result = build_symbol_defs_from_map(repo_map, "run")
        for defn in result.get("definitions", []):
            assert "name" in defn
            assert "kind" in defn
            assert "file" in defn
            assert "line" in defn


# ---------------------------------------------------------------------------
# L4 — build_symbol_impact_from_map: preferred_command_reason (integration)
# ---------------------------------------------------------------------------


class TestBuildSymbolImpactPreferredCommandReason:
    def _reason_is_descriptive(self, reason: str) -> bool:
        # Must mention what impact provides AND what blast-radius adds
        lower = reason.lower()
        return "blast-radius" in lower and (
            "caller" in lower or "caller_tree" in lower or "blast_radius_score" in lower
        )

    def test_normal_symbol_has_descriptive_reason(self, simple_fixture: Path) -> None:
        repo_map = build_repo_map(str(simple_fixture))
        result = build_symbol_impact_from_map(repo_map, "process")
        reason = result.get("preferred_command_reason", "")
        assert self._reason_is_descriptive(reason), f"Reason not descriptive enough: {reason!r}"

    def test_no_match_symbol_has_descriptive_reason(self, simple_fixture: Path) -> None:
        repo_map = build_repo_map(str(simple_fixture))
        result = build_symbol_impact_from_map(repo_map, "nonexistent_xyz_sym_9999")
        assert result.get("no_match") is True
        reason = result.get("preferred_command_reason", "")
        assert self._reason_is_descriptive(reason), f"Reason not descriptive enough: {reason!r}"

    def test_preferred_command_still_blast_radius(self, simple_fixture: Path) -> None:
        repo_map = build_repo_map(str(simple_fixture))
        result = build_symbol_impact_from_map(repo_map, "process")
        assert result.get("preferred_command") == "blast-radius"

    def test_trust_level_still_planning_signal(self, simple_fixture: Path) -> None:
        repo_map = build_repo_map(str(simple_fixture))
        result = build_symbol_impact_from_map(repo_map, "process")
        assert result.get("trust_level") == "planning-signal"
