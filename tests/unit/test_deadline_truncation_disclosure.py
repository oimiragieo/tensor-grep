"""A `--deadline` cutoff must DISCLOSE, not just exit 2 (the ABSENT half of the class).

`_scan_truncation_warning` read only `scan_limit` / `caller_scan_limit` / `output_limit`. A
`--deadline` cutoff sets `partial` / `deadline_limit` and none of those, so it returned None and
every emitter wired to it stayed silent -- while `_scan_incomplete`, which DOES fire on `partial`,
exited 2. Measured as a paired arm through `blast_radius`, one variable moving:

    ARM A  scan_limit cap                exit 2 + "warning: INCOMPLETE RESULT: ..."
    ARM B  partial + deadline_limit      exit 2 + nothing at all

Exit 2 with silent stdout is worse than a mispositioned warning: a reader who never sees a line has
nothing to be late about. Found by the independent gate on task #329 as a pre-existing in-class gap.

Fixing it at the SHARED source means the three emitters already wired to
`_completeness_caveat_lines` (symbol commands, `blast-radius` counts, `--mermaid`) all gain the
disclosure from one change -- which is the point of modelling the class instead of patching
emitters one at a time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer

from tensor_grep.cli import main as main_mod
from tensor_grep.cli.main import (
    _render_blast_radius_mermaid,
    _scan_incomplete,
    _scan_truncation_warning,
)

_BLAST_HEADER = "Blast radius for"


def _payload(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "definitions": [{"path": "a.py", "line": 1}],
        "callers": [{"path": "b.py", "line": 9}],
        "files": ["a.py"],
        "tests": [],
        "import_graph_consumers": [],
    }
    payload.update(extra)
    return payload


def _deadline_limit(**over: Any) -> dict[str, Any]:
    base = {"deadline_exceeded": True, "files_scanned": 37, "files_total": 900}
    base.update(over)
    return base


def _scan_cap() -> dict[str, Any]:
    return {
        "max_repo_files": 512,
        "scanned_files": 512,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
    }


# ------------------------------------------------------------------ the shared source (unit)


def test_deadline_payload_now_yields_a_truncation_warning() -> None:
    # PREMISE: the exit gate already considered this truncated, which is what made the silence a
    # contradiction rather than a consistent (if lax) policy.
    payload = _payload(partial=True, deadline_limit=_deadline_limit())
    assert _scan_incomplete(payload) is True

    warning = _scan_truncation_warning(payload)
    assert warning is not None
    assert "INCOMPLETE RESULT" in warning
    assert "37 of 900 files" in warning  # the counts the payload actually carried


def test_partial_without_a_deadline_limit_still_discloses() -> None:
    # Fail-closed: `partial` alone is a scan that stopped early. Keying only on the richer
    # `deadline_limit` would go silent exactly where the producer recorded least.
    warning = _scan_truncation_warning(_payload(partial=True))
    assert warning is not None
    assert "INCOMPLETE RESULT" in warning


def test_the_deadline_remedy_names_the_deadline_and_denies_the_budget_knob() -> None:
    # WRONG-KNOB arm. `_TRUNCATION_REMEDY` names --max-repo-files/--max-callers/--max-files, and
    # every one is the wrong dial for a scan that ran out of TIME: a bigger file budget lets it
    # read more files inside the same expired deadline. Reusing that string would have
    # reintroduced the #762/#822 failure in the commit that closes this gap.
    warning = _scan_truncation_warning(_payload(partial=True, deadline_limit=_deadline_limit()))
    assert warning is not None
    assert "--deadline" in warning
    assert "does NOT help" in warning


def test_a_file_cap_still_wins_over_the_deadline_branch() -> None:
    # The deadline branch is written LAST so it cannot mask a more specific cause. A payload
    # carrying BOTH must report the cap, which names the actionable knob.
    both = _payload(partial=True, deadline_limit=_deadline_limit(), scan_limit=_scan_cap())
    warning = _scan_truncation_warning(both)
    assert warning is not None
    assert "512-file cap" in warning
    assert "--deadline elapsed" not in warning


def test_a_complete_payload_still_yields_no_warning() -> None:
    # CONTROL ARM: without it, "a warning appeared" would hold in every arm.
    assert _scan_truncation_warning(_payload()) is None


def test_an_output_cap_alone_is_not_a_deadline_truncation() -> None:
    # Boundary guard: an OUTPUT cap is a COMPLETE analysis capped for display. It must not be
    # dragged into the deadline branch, whose message would tell the reader to raise --deadline
    # for a scan that finished.
    warning = _scan_truncation_warning(
        _payload(
            output_limit={
                "callers_truncated": True,
                "total_callers": 9,
                "returned_callers": 4,
            }
        )
    )
    assert warning is not None
    assert "output was capped" in warning
    assert "--deadline" not in warning


# ------------------------------------------------------- the emitters that inherit it (class)


def _stub(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    from tensor_grep.cli import repo_map

    monkeypatch.setattr(
        repo_map, "build_symbol_blast_radius", lambda *a, **k: dict(payload), raising=True
    )
    monkeypatch.setattr(
        main_mod, "_maybe_symbol_command_via_running_daemon", lambda **k: None, raising=True
    )


def _run_blast_radius(tmp_path: Path, *, mermaid: bool = False) -> None:
    main_mod.blast_radius(
        path=str(tmp_path),
        symbol_arg="x",
        symbol=None,
        provider="native",
        max_depth=3,
        max_repo_files=512,
        max_callers=None,
        max_files=None,
        deadline=None,
        json_output=False,
        mermaid_output=mermaid,
    )


def test_deadline_truncation_leads_the_blast_radius_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, _payload(partial=True, deadline_limit=_deadline_limit()))
    with pytest.raises(typer.Exit):
        _run_blast_radius(tmp_path)
    out = capsys.readouterr().out
    assert _BLAST_HEADER in out  # premise: the counts block really rendered
    assert out.splitlines()[0].startswith("warning: INCOMPLETE RESULT:")
    assert out.index("warning:") < out.index(_BLAST_HEADER)


def test_deadline_truncation_reaches_the_mermaid_twin_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The class payoff: fixing the shared source means the --mermaid renderer gains the deadline
    # disclosure without its own edit. If this ever fails while the counts test passes, the
    # emitters have drifted apart again.
    _stub(monkeypatch, _payload(partial=True, deadline_limit=_deadline_limit()))
    with pytest.raises(typer.Exit):
        _run_blast_radius(tmp_path, mermaid=True)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "graph TD"
    assert lines[1].startswith("  %% warning: INCOMPLETE RESULT:")
    assert "--deadline" in lines[1]


def test_renderer_level_deadline_banner_is_present_and_single_line() -> None:
    out = _render_blast_radius_mermaid({
        "symbol": "T",
        "path": "/repo",
        "callers": [{"file": "/repo/c.py", "line": 4}],
        "partial": True,
        "deadline_limit": _deadline_limit(),
    })
    # TWO disclosure lines now, not one: the `%%` comment AND the rendered `tg_incomplete[...]`
    # node that #836 added, because a Mermaid comment is stripped by the parser and never reaches
    # a rendered diagram. Counting lines that mention INCOMPLETE RESULT was the MECHANISM; the
    # property is that NEITHER disclosure may be split across lines by an injected newline.
    #
    # This assertion was written in #835 and #836 was authored against a tree that did not yet
    # contain it. Both PRs were green alone, git merged them with no textual conflict, and main
    # went red on the semantic collision. The identical assertion in
    # `test_leading_truncation_banner.py` WAS updated by #836 -- the sibling here was missed
    # because it lived in a file that PR never opened. When a change alters an output SHAPE, grep
    # the whole test suite for assertions about that shape, not just the file you are editing.
    lines = out.splitlines()
    assert lines[0] == "graph TD"
    comment_lines = [ln for ln in lines if ln.lstrip().startswith("%% warning:")]
    node_lines = [ln for ln in lines if "tg_incomplete[" in ln]
    assert len(comment_lines) == 1, f"comment spans multiple lines: {comment_lines}"
    assert len(node_lines) == 1, f"node spans multiple lines: {node_lines}"
    # Premise: the deadline cause really reached both, or this proves nothing about it.
    assert "--deadline" in comment_lines[0]
    assert "INCOMPLETE RESULT" in node_lines[0]


# ------------------------------------------------------------------- the class ratchet


def _every_incompleteness_cause() -> dict[str, dict[str, Any]]:
    """Every payload field that makes `_scan_incomplete` fire, one per entry.

    Kept as an explicit map rather than derived, so ADDING a cause to `_scan_incomplete` without
    adding it here is itself caught by `test_the_cause_map_covers_every_field_the_exit_gate_reads`
    below. Derivation would silently cover a new field and defeat the point.
    """
    return {
        "scan_limit.possibly_truncated": {"scan_limit": _scan_cap()},
        "caller_scan_limit.possibly_truncated": {
            "caller_scan_limit": {"possibly_truncated": True, "ceiling": 512, "files_total": 1941}
        },
        # `partial` + `deadline_limit` are ONE cause, not two: both producers in `repo_map`
        # (`build_repo_map` and the #304 session rebuild) set them TOGETHER, and say so in their
        # own comments -- "a top-level `partial` flag ... plus a `deadline_limit` sibling". A row
        # for `deadline_limit` alone was tried and the premise assertion below rejected it: the
        # field does not trip `_scan_incomplete` on its own, so that row tested a shape production
        # cannot emit. Left recorded rather than deleted, because the obvious "fix" -- teaching
        # `_scan_incomplete` to read `deadline_limit` -- would be hardening against an unreachable
        # state, and should be a deliberate choice if anyone wants it.
        "partial + deadline_limit (the production pair)": {
            "partial": True,
            "deadline_limit": _deadline_limit(),
        },
        "partial alone": {"partial": True},
        "caller_scan_truncated": {"caller_scan_truncated": True},
    }


def test_exit_two_never_happens_silently_for_any_cause() -> None:
    """THE INVARIANT: `_scan_incomplete` implies a message. No cause may exit 2 in silence.

    Two predicates decide two halves of one contract -- `_scan_incomplete` the exit code,
    `_scan_truncation_warning` the message -- and nothing made them agree. On `origin/main` TWO
    fields reached the first and not the second (`partial`, and `caller_scan_truncated`, the
    second found only while fixing the first). Enumerating causes is what produced the gap: each
    branch was added when its cause arrived, and the next cause arrived without one.
    """
    silent = []
    for name, extra in _every_incompleteness_cause().items():
        payload = _payload(**extra)
        # Premise: this fixture really does trip the exit gate. A cause that does not is not
        # exercising the invariant at all, and would make its row vacuously pass.
        assert _scan_incomplete(payload) is True, f"{name} does not trip the exit gate"
        if _scan_truncation_warning(payload) is None:
            silent.append(name)
    assert not silent, (
        f"these causes exit 2 with NO message: {silent}. Every `_scan_incomplete` cause must "
        "produce a warning -- add a specific branch to `_scan_truncation_warning` (preferred, it "
        "can name the right knob) or confirm the fail-closed tail covers it."
    )


def test_the_cause_map_covers_every_field_the_exit_gate_reads() -> None:
    # The ratchet's own ratchet. If `_scan_incomplete` learns a new field and nobody adds it to
    # `_every_incompleteness_cause`, the invariant test above silently stops covering it -- a
    # ratchet that quietly narrows is worse than none, because it still reads as green.
    import inspect

    source = inspect.getsource(_scan_incomplete)
    covered = " ".join(_every_incompleteness_cause())
    for field in ("scan_limit", "caller_scan_limit", "partial", "caller_scan_truncated"):
        assert field in source, f"{field} is no longer read by _scan_incomplete; re-derive this map"
        assert field in covered, (
            f"`_scan_incomplete` reads {field!r} but `_every_incompleteness_cause` has no entry "
            "for it, so the exit-2-never-silent invariant does not cover it"
        )
