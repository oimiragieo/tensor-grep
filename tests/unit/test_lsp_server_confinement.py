"""Workspace-root confinement tests for LSP rename edits (audit MED).

An external LSP provider's rename response was applied verbatim (``WorkspaceEdit(**result)``)
with no check that the edited document URIs stayed inside the resolved workspace root, so a
malicious/buggy provider could drive an edit to a file outside the workspace. Both the
external and native rename branches now confine every edit target to the workspace root.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.lsp_server import (
    _path_to_uri,
    _path_within_root,
    _uri_within_root,
    _workspace_edit_target_uris,
)


def test_path_and_uri_within_root_confine_and_reject(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    inside = root / "pkg" / "mod.py"
    inside.write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("x", encoding="utf-8")

    assert _path_within_root(inside, root) is True
    assert _path_within_root(outside, root) is False
    assert _uri_within_root(_path_to_uri(inside), root) is True
    assert _uri_within_root(_path_to_uri(outside), root) is False


def test_workspace_edit_target_uris_extracts_both_shapes() -> None:
    result = {
        "changes": {"file:///a.py": [], "file:///b.py": []},
        "documentChanges": [
            {"textDocument": {"uri": "file:///c.py"}, "edits": []},
        ],
    }
    assert set(_workspace_edit_target_uris(result)) == {
        "file:///a.py",
        "file:///b.py",
        "file:///c.py",
    }
