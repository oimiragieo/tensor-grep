"""TG-6 (real-AI-use feedback): `tg blast-radius --mermaid` renders the EXISTING caller graph
as a Mermaid `graph TD` diagram. An AI doing a doc audit wanted a visual/agent-consumable graph;
the data already exists (blast-radius --json), so this is a faithful formatter over `callers[]`
(exact file+line call sites) -- no fabricated transitive edges.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import tensor_grep.cli.repo_map as repo_map
from tensor_grep.cli.main import _render_blast_radius_mermaid, app

runner = CliRunner()


def _payload(symbol: str, callers: list[dict[str, Any]], path: str = "/repo", **extra: Any) -> dict:
    p: dict[str, Any] = {"symbol": symbol, "path": path, "callers": callers}
    p.update(extra)
    return p


def test_mermaid_renders_graph_td_with_caller_edges_to_the_symbol() -> None:
    out = _render_blast_radius_mermaid(
        _payload(
            "SearchConfig",
            [
                {"file": "/repo/a/config.py", "line": 10},
                {"file": "/repo/b/sidecar.py", "line": 431},
            ],
        )
    )
    lines = out.splitlines()
    assert lines[0] == "graph TD"
    assert "SearchConfig" in out
    # both caller files appear (as forward-slashed relpaths) and point at the symbol node.
    assert "a/config.py" in out
    assert "b/sidecar.py" in out
    assert out.count("-->") == 2  # one edge per unique caller file
    assert "target" in out  # edges point at the symbol node
    assert "\\" not in out  # never emit Windows backslashes into a mermaid label


def test_mermaid_dedups_multiple_call_sites_in_one_file_into_one_node() -> None:
    out = _render_blast_radius_mermaid(
        _payload("Foo", [{"file": "/repo/x.py", "line": 1}, {"file": "/repo/x.py", "line": 9}])
    )
    assert out.count('x.py"]') == 1  # single node for the file
    assert "2 calls" in out  # edge annotated with the call count
    assert out.count("-->") == 1


def test_mermaid_is_deterministic_and_escapes_quotes() -> None:
    callers = [{"file": "/repo/z.py", "line": 2}, {"file": "/repo/a.py", "line": 1}]
    first = _render_blast_radius_mermaid(_payload('Sym"quote', callers))
    second = _render_blast_radius_mermaid(_payload('Sym"quote', callers))
    assert first == second  # stable output (sorted nodes) -> diff-friendly
    assert "Sym'quote" in first  # the raw double-quote is neutralized to a single-quote
    assert 'Sym"quote' not in first  # no raw double-quote that would break the mermaid label


def test_mermaid_handles_no_callers_without_fabricating_edges() -> None:
    out = _render_blast_radius_mermaid(_payload("Lonely", []))
    assert out.splitlines()[0] == "graph TD"
    assert "Lonely" in out
    assert "no callers" in out.lower()
    assert "-->" not in out  # no invented edges


def test_mermaid_notes_truncation_when_result_incomplete() -> None:
    out = _render_blast_radius_mermaid(
        _payload("Big", [{"file": "/repo/a.py", "line": 1}], result_incomplete=True)
    )
    assert "truncated" in out.lower()


def test_blast_radius_command_supports_mermaid_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius",
        lambda *a, **k: {
            "symbol": "Sym",
            "path": str(tmp_path),
            "definitions": [],
            "callers": [{"file": str(tmp_path / "c.py"), "line": 3}],
            "files": [],
            "tests": [],
        },
    )
    result = runner.invoke(app, ["blast-radius", str(tmp_path), "Sym", "--mermaid"])
    assert result.exit_code == 0, result.stdout
    assert "graph TD" in result.stdout
    assert "-->" in result.stdout
    # `--mermaid` alone must stay prose, never JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)


def test_blast_radius_json_alone_has_no_mermaid_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Preservation: `--json` alone must stay byte-identical to today -- no `mermaid` key
    # unless `--mermaid` was ALSO requested.
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius",
        lambda *a, **k: {
            "symbol": "Sym",
            "path": str(tmp_path),
            "definitions": [],
            "callers": [{"file": str(tmp_path / "c.py"), "line": 3}],
            "files": [],
            "tests": [],
        },
    )
    result = runner.invoke(app, ["blast-radius", str(tmp_path), "Sym", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "mermaid" not in payload


def test_blast_radius_json_and_mermaid_combined_embeds_mermaid_in_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # task #164: `--json --mermaid` together must NOT silently drop the JSON (the pre-fix
    # if/elif let `--mermaid` short-circuit `--json`, so an agent asking for both got only the
    # human-readable diagram and a `json.loads` on stdout raised). Embed-don't-refuse: the
    # rendered mermaid text is folded into the JSON payload under a `mermaid` key so one call
    # returns both the machine graph and the diagram.
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius",
        lambda *a, **k: {
            "symbol": "Sym",
            "path": str(tmp_path),
            "definitions": [],
            "callers": [{"file": str(tmp_path / "c.py"), "line": 3}],
            "files": [],
            "tests": [],
        },
    )
    result = runner.invoke(app, ["blast-radius", str(tmp_path), "Sym", "--json", "--mermaid"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)  # must be valid JSON -- this is the core regression
    assert payload.get("callers")  # the machine graph is still present
    assert isinstance(payload.get("mermaid"), str) and payload["mermaid"]
    assert payload["mermaid"].startswith("graph TD")
    assert "-->" in payload["mermaid"]


def test_blast_radius_json_and_mermaid_combined_still_honors_not_found_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The exit-2 (scan-incomplete) / exit-1 (not_found) contract sits AFTER the output block
    # (main.py) and must still apply on the both-flags path -- the embed-both fix must not
    # early-return before it.
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius",
        lambda *a, **k: {
            "symbol": "Ghost",
            "path": str(tmp_path),
            "definitions": [],
            "callers": [],
            "files": [],
            "tests": [],
        },
    )
    result = runner.invoke(app, ["blast-radius", str(tmp_path), "Ghost", "--json", "--mermaid"])
    assert result.exit_code == 1, result.stdout  # not_found still exits 1, not swallowed
    payload = json.loads(result.stdout)  # the JSON+mermaid embed still happened before the exit
    assert payload["not_found"] is True
    assert isinstance(payload.get("mermaid"), str) and payload["mermaid"]
