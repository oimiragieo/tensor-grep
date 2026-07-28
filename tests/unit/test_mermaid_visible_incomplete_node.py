"""A `--mermaid` truncation disclosure must survive RENDERING, not just source-reading.

Task #329 moved this disclosure to LEAD its graph, which fixed the reader who consumes the raw
text. It did nothing for the reader looking at the picture: Mermaid's own flowchart documentation
states that comments "will be ignored by the parser", so a `%%` line is absent from every rendered
diagram. That reader -- a human glancing at a caller graph -- is precisely the one most likely to
trust it at a glance, and `--mermaid` exists to serve them.

So the disclosure is now BOTH: the `%%` comment (machine-greppable, carries the full remedy) and a
real node (renders, carries the cause). The node is declared with no edge, so the "no invented
edges" guard and every `-->` count stay untouched, and it is emitted only when incomplete, so a
complete graph stays byte-identical.
"""

from __future__ import annotations

from typing import Any

from tensor_grep.cli.main import (
    _MERMAID_INCOMPLETE_LABEL_LIMIT,
    _mermaid_incomplete_label,
    _render_blast_radius_mermaid,
)

_NODE = "tg_incomplete["


def _payload(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "Target",
        "path": "/repo",
        "callers": [{"file": "/repo/pkg/caller.py", "line": 12}],
    }
    payload.update(extra)
    return payload


def _scan_cap() -> dict[str, Any]:
    return {
        "max_repo_files": 512,
        "scanned_files": 512,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
    }


def test_a_truncated_graph_carries_a_node_not_only_a_comment() -> None:
    out = _render_blast_radius_mermaid(_payload(scan_limit=_scan_cap(), result_incomplete=True))
    # Premise: the graph really rendered, so the assertions below are not vacuous.
    assert "pkg/caller.py" in out
    assert _NODE in out, "the disclosure exists only as a %% comment, which no renderer shows"
    assert "512-file cap" in out.split(_NODE, 1)[1], "the node must carry the CAUSE"


def test_the_comment_is_kept_alongside_the_node() -> None:
    # Not either/or. The comment carries the full remedy and is what a grep over the source finds;
    # dropping it to "clean up" would trade one audience for the other.
    out = _render_blast_radius_mermaid(_payload(scan_limit=_scan_cap(), result_incomplete=True))
    assert "%% warning: INCOMPLETE RESULT:" in out
    assert "--max-repo-files" in out  # the remedy survives, in the comment


def test_the_node_leads_the_graph_content() -> None:
    out = _render_blast_radius_mermaid(_payload(scan_limit=_scan_cap(), result_incomplete=True))
    lines = out.splitlines()
    assert lines[0] == "graph TD"  # the diagram-type declaration still opens the block
    assert lines[1].startswith("  %% warning:")
    assert lines[2].startswith(f"  {_NODE}")
    assert out.index(_NODE) < out.index("pkg/caller.py")


def test_the_node_adds_no_edge() -> None:
    # The existing contract: `--mermaid` never invents edges. A disclosure that fabricated one
    # would be a lie in the shape of a warning.
    out = _render_blast_radius_mermaid(_payload(scan_limit=_scan_cap(), result_incomplete=True))
    assert out.count("-->") == 1  # only the real caller edge
    assert "tg_incomplete -->" not in out
    assert "--> tg_incomplete" not in out


def test_a_complete_graph_is_byte_identical_to_no_disclosure_at_all() -> None:
    # CONTROL ARM. Without it, "a node appeared" would be true in every arm.
    out = _render_blast_radius_mermaid(_payload())
    assert "-->" in out  # premise: the graph rendered
    assert _NODE not in out
    assert "%%" not in out
    assert "INCOMPLETE" not in out


def test_a_zero_caller_graph_keeps_its_own_trailing_advisory() -> None:
    # The advisory half of the split is untouched: that scan COMPLETED and found nothing, so its
    # note still trails and no incomplete node is added.
    out = _render_blast_radius_mermaid(_payload(callers=[]))
    assert out.splitlines()[-1].strip().startswith("%% no callers found")
    assert _NODE not in out


def test_the_label_drops_the_log_prefix_and_the_remedy() -> None:
    label = _mermaid_incomplete_label(
        "warning: INCOMPLETE RESULT: the scan stopped at a 9-file cap, so callers/definitions "
        "may be missing. A zero or small count here is NOT trustworthy. Remedy: raise things."
    )
    assert label == "INCOMPLETE RESULT: the scan stopped at a 9-file cap"
    assert "warning:" not in label  # a log convention, meaningless inside a box
    assert "Remedy" not in label  # long enough to distort the graph it warns about


def test_a_pathological_cause_cannot_distort_the_diagram() -> None:
    # The cause clause is producer-controlled. An unbounded label would make the WORST-truncated
    # graph render worst -- the signal degrading exactly where it matters most.
    label = _mermaid_incomplete_label("warning: INCOMPLETE RESULT: " + "x" * 5000)
    assert len(label) <= _MERMAID_INCOMPLETE_LABEL_LIMIT
    assert label.endswith("...")  # the truncation is disclosed, not silent


def test_the_label_is_always_one_line() -> None:
    # A newline would close the node declaration and inject live graph statements.
    label = _mermaid_incomplete_label(
        'warning: INCOMPLETE RESULT: bad\n  evil["pwn"]\n  evil --> x'
    )
    assert "\n" not in label
