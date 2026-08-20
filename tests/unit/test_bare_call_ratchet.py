"""Tests for the bare-call ratchet (scripts/bare_call_ratchet.py).

The design this gate serves (docs/design/2026-08-19-split-floor-escape.md, §4) requires it to be
SEEN TO FAIL on a deliberately reintroduced bare call before it is trusted, per the repo's
standing rule that a gate never observed firing is a comment.

So the weight here is on the negative arms, and on one arm in particular: `test_measurement_*`
mutates a real module on disk and proves the AST query's count MOVES. Every other test exercises
`evaluate()`, which is pure policy over a dict -- policy tests pass just as happily when the
measurement underneath them is blind, which is the failure this file exists to rule out.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load_ratchet():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "bare_call_ratchet", SCRIPTS / "bare_call_ratchet.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ratchet = _load_ratchet()


# --------------------------------------------------------------------------------------
# The measurement. These are the arms that prove the AST query can DISCRIMINATE.
# --------------------------------------------------------------------------------------


def _write_module(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "subject.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_measurement_counts_a_bare_call(tmp_path: Path) -> None:
    from cost_split_floor_routes import bare_call_sites

    mod = _write_module(
        tmp_path,
        "def caller():\n    return resolve_native_tg_binary()\n",
    )
    total, per_symbol = bare_call_sites(mod, {"resolve_native_tg_binary"})
    assert total == 1, "a bare call to a patched name must be counted"
    assert per_symbol == {"resolve_native_tg_binary": 1}


def test_measurement_does_not_count_the_route_a_fix(tmp_path: Path) -> None:
    """The whole point: `_self.X()` must read as CONVERTED, not as an offender.

    If this counted, the ratchet could never reach zero and the fix it exists to drive would
    look like a regression.
    """
    from cost_split_floor_routes import bare_call_sites

    mod = _write_module(
        tmp_path,
        "def caller():\n    return _self.resolve_native_tg_binary()\n",
    )
    total, _ = bare_call_sites(mod, {"resolve_native_tg_binary"})
    assert total == 0, "an attribute call is late-binding and is NOT an offender"


def test_measurement_moves_when_a_bare_call_is_injected(tmp_path: Path) -> None:
    """The perturbation proof, both directions, on one file.

    Same file, one call site changed: converted -> 0, bare -> 1. A measurement that returned the
    same number for both would make every other test in this file vacuous.
    """
    from cost_split_floor_routes import bare_call_sites

    converted = "def caller():\n    return _self.build_repo_map()\n"
    reintroduced = "def caller():\n    return build_repo_map()\n"

    mod = _write_module(tmp_path, converted)
    before, _ = bare_call_sites(mod, {"build_repo_map"})

    mod.write_text(reintroduced, encoding="utf-8")
    after, _ = bare_call_sites(mod, {"build_repo_map"})

    assert (before, after) == (0, 1), (
        f"the AST query must distinguish the two forms; got before={before} after={after}"
    )


def test_measurement_ignores_a_name_that_is_not_patched(tmp_path: Path) -> None:
    from cost_split_floor_routes import bare_call_sites

    mod = _write_module(tmp_path, "def caller():\n    return some_other_helper()\n")
    total, _ = bare_call_sites(mod, {"resolve_native_tg_binary"})
    assert total == 0, "only PATCHED symbols weld a function to its module"


def test_measurement_is_not_fooled_by_the_symbol_in_a_docstring(tmp_path: Path) -> None:
    """A grep would count this. An AST walk must not.

    This repo has been burned by exactly this: after a fix lands, the docstring EXPLAINING it
    contains the symbol, so a substring check reports the corrected file as broken.
    """
    from cost_split_floor_routes import bare_call_sites

    mod = _write_module(
        tmp_path,
        '''def caller():
    """Calls build_repo_map() -- converted to _self.build_repo_map() for Route A."""
    return _self.build_repo_map()
''',
    )
    total, _ = bare_call_sites(mod, {"build_repo_map"})
    assert total == 0, "prose mentioning the symbol is not a call to it"


# --------------------------------------------------------------------------------------
# The policy. Fail-closed in BOTH directions.
# --------------------------------------------------------------------------------------

PINS = {"a.py": 10, "b.py": 5}


def test_holds_when_every_module_sits_on_its_pin() -> None:
    assert ratchet.evaluate({"a.py": 10, "b.py": 5}, PINS) == []


def test_fails_when_a_module_grows() -> None:
    failures = ratchet.evaluate({"a.py": 11, "b.py": 5}, PINS)
    assert len(failures) == 1
    assert "RATCHET REGRESSION" in failures[0]
    assert "a.py" in failures[0]
    assert "_self." in failures[0], "the failure must name the fix, not just the problem"


def test_fails_when_a_module_shrinks_without_banking_the_pin() -> None:
    """The unusual half, and the reason it exists.

    A pin left above the real count accepts a RANGE. A later regression back up to the old pin
    would then pass, which is precisely the regression this gate is for.
    """
    failures = ratchet.evaluate({"a.py": 7, "b.py": 5}, PINS)
    assert len(failures) == 1
    assert "BANK THE PROGRESS" in failures[0]
    assert "to 7" in failures[0], "it must say what to change the pin TO"


def test_fails_when_a_converted_module_is_still_pinned() -> None:
    failures = ratchet.evaluate({"a.py": 0, "b.py": 5}, PINS)
    assert len(failures) == 1
    assert "RETIRE THE ENTRY" in failures[0]


def test_fails_on_a_pin_whose_module_is_gone() -> None:
    failures = ratchet.evaluate({"b.py": 5}, PINS)
    assert len(failures) == 1
    assert "STALE PIN" in failures[0]


def test_fails_on_an_offender_that_was_never_pinned() -> None:
    failures = ratchet.evaluate({"a.py": 10, "b.py": 5, "c.py": 3}, PINS)
    assert len(failures) == 1
    assert "UNPINNED OFFENDER" in failures[0]
    assert "c.py" in failures[0]


def test_an_unpinned_module_at_zero_is_not_an_offender() -> None:
    assert ratchet.evaluate({"a.py": 10, "b.py": 5, "clean.py": 0}, PINS) == []


def test_reports_every_failure_not_just_the_first() -> None:
    failures = ratchet.evaluate({"a.py": 11, "b.py": 0}, PINS)
    assert len(failures) == 2, f"expected both a regression and a retire, got {failures}"


# --------------------------------------------------------------------------------------
# The live repo.
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_every_target_is_either_pinned_or_converted() -> None:
    """A target may be absent from the pins ONLY because it reached zero.

    The first version of this test asserted every target carries a pin. That was written when
    no module had been converted, and it went red the moment one was -- correctly describing
    the old world, not the invariant. Retiring a converted module is REQUIRED by the ratchet's
    third rule, so "pinned" cannot be the universal condition.

    The invariant that survives conversion: a target is pinned, or it measures zero. That still
    fails if an offender is quietly deleted from the pins file, which is the thing worth
    guarding -- an unpinned module with a non-zero count is how the gate would be silently
    switched off for that module.
    """
    from cost_split_floor_routes import TARGETS

    pins = ratchet.load_pins()
    counts = ratchet.measure()
    assert counts, "measured nothing -- an empty count set can never fail"

    for rel, _dotted in TARGETS:
        if rel in pins:
            continue
        assert counts.get(rel, 0) == 0, (
            f"{rel} is a Route A target with {counts.get(rel)} bare calls and NO pin -- "
            f"either it was dropped from the pins file, or the conversion regressed"
        )


def test_pins_file_is_not_empty() -> None:
    """An empty pin set can never fail, so emptiness is only legitimate at full conversion."""
    from cost_split_floor_routes import TARGETS

    pins = ratchet.load_pins()
    if not pins:
        counts = ratchet.measure()
        assert all(counts.get(rel, 0) == 0 for rel, _ in TARGETS), (
            "the pins file is empty but targets still have bare calls -- the gate is off"
        )


def test_pins_are_positive_integers() -> None:
    for rel, n in ratchet.load_pins().items():
        assert isinstance(n, int) and n > 0, f"{rel} pinned at {n}; a 0 pin must be retired"


@pytest.mark.slow
def test_the_repo_currently_holds() -> None:
    """The live arm. Slow because it parses the whole test tree to find patch sites."""
    counts = ratchet.measure()
    assert counts, "measured nothing -- an empty count set can never fail (false green)"
    failures = ratchet.evaluate(counts, ratchet.load_pins())
    assert failures == [], "\n".join(failures)


def test_pins_json_parses_and_has_the_documented_shape() -> None:
    raw = json.loads((SCRIPTS / "bare_call_pins.json").read_text(encoding="utf-8"))
    assert "bare_calls" in raw
    assert "_comment" in raw, "the pins file must explain the ratchet direction to its next reader"
