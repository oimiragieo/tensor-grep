"""A command that exits ``2`` must have SAID something on stdout.

Thirteen of the fourteen ``_scan_incomplete`` gates in ``cli/main.py`` raised ``typer.Exit(2)``
over text output that read exactly like a complete result -- ``codemap`` was the only one that
disclosed. An agent branching on the exit code was fine; every human, and every agent reading the
text, was told a truncated answer was the whole answer.

Two tests, doing different jobs:

* the RATCHET reads the source and pins that every gate's text branch emits the banner, keyed on
  the SAME payload variable the gate reads -- a static property that no behavioural test would
  cover for commands whose fixtures are expensive to build;
* the behavioural tests drive real commands, so the ratchet cannot pass over a helper that emits
  nothing.

The ratchet's variable check is not hypothetical. The first cut of this change was applied by a
script that inserted ``_emit_scan_incompleteness_banner(payload)`` into every text branch, and
three daemon fast paths gate on ``daemon_payload`` -- so those three sites disclosed the state of
the wrong object. A sweep is a hypothesis until each site is read; this test is that reading, made
permanent.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import typer

from tensor_grep.cli import main as main_mod
from tensor_grep.cli.main import _emit_scan_incompleteness_banner

_MAIN = Path(main_mod.__file__)
_CALL = "_emit_scan_incompleteness_banner"


def _annotation_disclosure_is_bound(
    source: str, gate_line: int, caveat_var: str, gate_var: str
) -> bool:
    tree = ast.parse(source)
    function = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.lineno <= gate_line <= (node.end_lineno or node.lineno)
        ),
        None,
    )
    if function is None:
        return False
    parents = {
        child: parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)
    }

    def _constant_truth(node: ast.AST) -> bool | None:
        if isinstance(node, ast.Constant):
            return bool(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            operand = _constant_truth(node.operand)
            return None if operand is None else not operand
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "bool"
            and len(node.args) == 1
            and not node.keywords
        ):
            return _constant_truth(node.args[0])
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            try:
                left = ast.literal_eval(node.left)
                right = ast.literal_eval(node.comparators[0])
            except (ValueError, TypeError):
                return None
            operator = node.ops[0]
            if isinstance(operator, ast.Eq):
                return left == right
            if isinstance(operator, ast.NotEq):
                return left != right
            if isinstance(operator, ast.Lt):
                return left < right
            if isinstance(operator, ast.LtE):
                return left <= right
            if isinstance(operator, ast.Gt):
                return left > right
            if isinstance(operator, ast.GtE):
                return left >= right
        if isinstance(node, ast.BoolOp):
            values = [_constant_truth(value) for value in node.values]
            if any(value is None for value in values):
                return None
            known = [bool(value) for value in values]
            if isinstance(node.op, ast.And):
                return all(known)
            if isinstance(node.op, ast.Or):
                return any(known)
        return None

    def _is_reachable(node: ast.AST) -> bool:
        child = node
        while child in parents:
            parent = parents[child]
            if isinstance(parent, ast.If):
                truth = _constant_truth(parent.test)
                if child in parent.body and truth is False:
                    return False
                if child in parent.orelse and truth is True:
                    return False
            child = parent
        return True

    annotation_line: int | None = None
    disclosure: tuple[int, str] | None = None
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Tuple)
            and [elt.id for elt in node.targets[0].elts if isinstance(elt, ast.Name)]
            == [caveat_var, gate_var]
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_annotate_result_completeness"
            and _is_reachable(node)
        ):
            annotation_line = node.lineno
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Tuple)
            and len(node.targets[0].elts) == 2
            and all(isinstance(elt, ast.Name) for elt in node.targets[0].elts)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_completeness_caveat_lines"
            and node.value.args
            and isinstance(node.value.args[0], ast.Name)
            and node.value.args[0].id == caveat_var
            and any(
                keyword.arg == "is_truncation"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == gate_var
                for keyword in node.value.keywords
            )
            and _is_reachable(node)
        ):
            continue
        leading = node.targets[0].elts[0]
        assert isinstance(leading, ast.Name)
        disclosure = (node.lineno, leading.id)
    if annotation_line is None or disclosure is None:
        return False
    disclosure_line, leading_var = disclosure
    for node in ast.walk(function):
        if not (annotation_line < getattr(node, "lineno", 0) < disclosure_line):
            continue
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id
            in {
                caveat_var,
                gate_var,
            }
        ):
            return False
    emitted_line: int | None = None
    for node in ast.walk(function):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "echo"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "typer"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == leading_var
            and disclosure_line < node.lineno < gate_line
            and _is_reachable(node)
        ):
            continue
        emitted_line = node.lineno
        break
    if emitted_line is None:
        return False
    for node in ast.walk(function):
        if not (disclosure_line < getattr(node, "lineno", 0) < emitted_line):
            continue
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id == leading_var
        ):
            return False
    for node in ast.walk(function):
        if not (disclosure_line < getattr(node, "lineno", 0) < gate_line):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == gate_var:
            return False
    return True


def _truncated() -> dict[str, Any]:
    return {
        "max_repo_files": 512,
        "scanned_files": 512,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
    }


# ------------------------------------------------------------------------------- the ratchet


def test_every_exit_two_gate_has_a_disclosure_on_its_text_branch() -> None:
    source = _MAIN.read_text(encoding="utf-8")
    lines = source.splitlines()
    gates: list[tuple[int, str, str | None, str | None]] = []
    for i, line in enumerate(lines):
        if not line.strip().startswith("if "):
            continue
        m = re.search(r"_scan_incomplete\((\w+)\)", line)
        # A GATE is defined by BEHAVIOUR -- it exits 2 -- not by mentioning `_scan_incomplete`.
        # Two other uses exist and neither owes a disclosure: this file's own helper guards on it
        # (`if not _scan_incomplete(...): return False`) and `_scan_truncation_warning` ends with
        # a fail-closed tail that RETURNS a message on it. An earlier cut exempted the first by
        # name; the second then appeared from another PR and was flagged as a missing disclosure
        # in the one function whose entire job is producing that disclosure.
        #
        # Keying on `typer.Exit(2)` in the body is self-maintaining: a new non-gate use needs no
        # exemption, and a new real gate cannot dodge the check by living somewhere unexpected.
        # 8 lines, not 4: the two `agent` gates put `raise typer.Exit(2)` 5-7 lines below their
        # `if`, and a 4-line window silently dropped BOTH -- the ratchet covering less while
        # still reading green, which is exactly the failure this file warns about elsewhere.
        # Caught by the control arm naming 9 gates where the previous form named 12.
        body = "\n".join(lines[i : i + 8])
        if "Exit(2)" not in body:
            continue
        if m:
            gates.append((i, m.group(1), None, None))
            continue

        # The shared annotation path classifies the same payload once, then gates on the
        # returned boolean. Preserve that dataflow in the ratchet instead of exempting the
        # command by name: payload -> annotation -> is_truncation -> Exit(2), with disclosure
        # rendered from the paired caveat value.
        condition = re.fullmatch(r"if (\w+):", line.strip())
        if condition is None:
            continue
        start = next((j for j in range(i, 0, -1) if lines[j].startswith("def ")), 0)
        before_gate = "\n".join(lines[start:i])
        annotation = re.search(
            rf"(\w+),\s*{re.escape(condition.group(1))}\s*=\s*"
            r"_annotate_result_completeness\((\w+)\)",
            before_gate,
        )
        if annotation:
            gates.append((i, annotation.group(2), annotation.group(1), condition.group(1)))
    # PREMISE: the gates still exist and are plural. If a refactor renamed them this test would
    # otherwise pass over an empty list -- a ratchet that quietly covers nothing still reads green.
    assert len(gates) >= 12, f"expected the exit-2 gate family, found {len(gates)}"
    # By NAME, not just by count. A count floor cannot say WHICH gate vanished, and the window bug
    # above dropped precisely the two that sit furthest from their `raise`. These are commands
    # whose text output an agent is most likely to read as a finished answer.
    covered = {
        next((lines[j] for j in range(i, 0, -1) if lines[j].startswith("def ")), "").split("(")[0]
        for i, _, _, _ in gates
    }
    for command in ("def map", "def agent", "def context", "def edit_plan", "def prepare"):
        assert command in covered, (
            f"{command} is no longer recognised as an exit-2 gate; the gate detector narrowed "
            "and this ratchet has silently stopped covering it"
        )

    undisclosed = []
    for idx, var, caveat_var, gate_var in gates:
        # Scoped to the ENCLOSING FUNCTION, not a fixed line window. A 40-line window flagged
        # `prepare`, whose banner is correctly placed but sits ~50 lines above its gate with the
        # capsule-writing block in between. An arbitrary window makes the ratchet's verdict depend
        # on unrelated code length -- a false positive whose obvious cure is to widen the window
        # until it stops complaining, which is how a ratchet quietly stops ratcheting.
        start = next((j for j in range(idx, 0, -1) if lines[j].startswith("def ")), 0)
        window = "\n".join(lines[start:idx])
        if (
            caveat_var is not None
            and gate_var is not None
            and _annotation_disclosure_is_bound(source, idx + 1, caveat_var, gate_var)
        ):
            continue
        if f"{_CALL}({var})" in window:
            continue
        # `codemap` discloses through its own older `PARTIAL:` line; it is not silent, and
        # double-disclosing would be worse than either shape. Its POSITION (trailing) is a
        # separate, tracked defect and deliberately not changed here.
        if "PARTIAL:" in window:
            continue
        # `inventory` delegates its text to `render_inventory_text`, which ends with its own
        # "[!] truncated at max_files=..." notice. A source-only ratchet cannot see into the
        # callee, so the exemption is named here rather than inferred -- and naming it is what
        # stops the next reader "fixing" inventory into disclosing twice.
        if "render_inventory_text(" in window:
            continue
        undisclosed.append((idx + 1, var))
    assert not undisclosed, (
        f"these exit-2 gates have no disclosure on their text branch: {undisclosed}. "
        f"Add {_CALL}(<the same var the gate reads>) at the TOP of the `else` of the "
        "`if json_output` fork -- never beside the json.dumps, which would break json.loads."
    )


@pytest.mark.parametrize(
    "render_lines",
    [
        "leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation and False)\n    typer.echo(leading)",
        "caveat = None\n    leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)\n    typer.echo(leading)",
        "if False:\n        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)\n        typer.echo(leading)",
        "if not True:\n        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)\n        typer.echo(leading)",
        "if 0:\n        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)\n        typer.echo(leading)",
        "if True and False:\n        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)\n        typer.echo(leading)",
        "if bool(False):\n        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)\n        typer.echo(leading)",
        "if 1 == 0:\n        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)\n        typer.echo(leading)",
        "leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)\n    typer.echo(leading)\n    is_truncation = False",
    ],
)
def test_annotation_disclosure_binding_rejects_false_green_mutations(render_lines: str) -> None:
    source = (
        "def command(payload):\n"
        "    caveat, is_truncation = _annotate_result_completeness(payload)\n"
        f"    {render_lines}\n"
        "    if is_truncation:\n"
        "        raise typer.Exit(2)\n"
    )
    gate_line = next(
        index
        for index, line in enumerate(source.splitlines(), start=1)
        if line.strip() == "if is_truncation:"
    )
    assert not _annotation_disclosure_is_bound(source, gate_line, "caveat", "is_truncation")


def test_annotation_disclosure_binding_rejects_dead_annotation() -> None:
    source = (
        "def command(payload):\n"
        "    caveat, is_truncation = None, False\n"
        "    if False:\n"
        "        caveat, is_truncation = _annotate_result_completeness(payload)\n"
        "    leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)\n"
        "    typer.echo(leading)\n"
        "    if is_truncation:\n"
        "        raise typer.Exit(2)\n"
    )
    assert not _annotation_disclosure_is_bound(source, 7, "caveat", "is_truncation")


def test_the_banner_is_never_emitted_on_a_json_branch() -> None:
    # The one way this change could break a machine consumer: a stray line on stdout ahead of a
    # JSON document. Pinned structurally -- the call must not appear between `if json_output:` and
    # the `else:` that closes it.
    lines = _MAIN.read_text(encoding="utf-8").splitlines()
    offenders = []
    for i, line in enumerate(lines):
        if _CALL not in line or line.strip().startswith(("#", "*", '"')):
            continue
        if "def " in line:
            continue
        indent = len(line) - len(line.lstrip())
        for j in range(i - 1, max(0, i - 25), -1):
            stripped = lines[j].strip()
            cur = len(lines[j]) - len(lines[j].lstrip())
            if cur < indent and stripped.endswith(":"):
                if stripped.startswith("if json_output"):
                    offenders.append(i + 1)
                break
    assert not offenders, f"banner emitted on a --json branch at lines {offenders}"


# -------------------------------------------------------------------------- behaviour (unit)


def test_the_helper_emits_a_leading_warning_for_a_truncated_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emitted = _emit_scan_incompleteness_banner({"scan_limit": _truncated()})
    out = capsys.readouterr().out
    assert emitted is True
    assert out.startswith("warning: INCOMPLETE RESULT:")


def test_the_helper_is_silent_and_returns_false_for_a_complete_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # CONTROL ARM: without it, a helper that printed unconditionally would satisfy every other
    # assertion in this file and change output on every complete run.
    emitted = _emit_scan_incompleteness_banner({"files": ["a.py"]})
    assert emitted is False
    assert capsys.readouterr().out == ""


def test_map_discloses_a_truncated_scan_before_its_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tensor_grep.cli import repo_map

    payload = {
        "path": str(tmp_path),
        "files": ["a.py"],
        "tests": [],
        "symbols": [],
        "imports": [],
        "scan_limit": _truncated(),
    }
    monkeypatch.setattr(repo_map, "build_repo_map", lambda *a, **k: dict(payload), raising=True)
    with pytest.raises(typer.Exit) as exc:
        # EVERY parameter passed explicitly: an unpassed typer option arrives as an `OptionInfo`
        # sentinel, not its default, and the command dies on `int(OptionInfo)` -- an error, not a
        # red arm, and one that says nothing about the disclosure under test.
        main_mod.map(
            path=str(tmp_path),
            max_files=None,
            max_repo_files=512,
            deadline=None,
            no_deadline=False,
            json_output=False,
        )
    assert exc.value.exit_code == 2  # premise: this really is the exit-2 path
    out = capsys.readouterr().out
    assert "Repository map for" in out  # premise: the counts block rendered
    assert out.splitlines()[0].startswith("warning: INCOMPLETE RESULT:")
    assert out.index("warning:") < out.index("Repository map for")
