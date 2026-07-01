"""Path-containment regression tests for the session store (audit HIGH).

``session_id`` reached ``_session_payload_path`` from the CLI, the MCP
``tg_session_show`` / ``tg_session_refresh`` tools, and the token-authenticated
session daemon with no validation. An absolute or ``..``-shaped id resets pathlib's
join and escapes the sessions dir, giving arbitrary ``.json`` read via ``get_session``
and destructive overwrite via ``refresh_session``. These import ONLY ``session_store``
so they run standalone in CI, mirroring ``test_checkpoint_containment.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tensor_grep.cli import session_store


@pytest.mark.parametrize(
    "evil_id",
    ["../escape", "../../escape", "sub/../../escape", ".."],
)
def test_session_payload_path_refuses_traversal(tmp_path: Path, evil_id: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(ValueError):
        session_store._session_payload_path(root, evil_id)


def test_session_payload_path_refuses_absolute(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    victim = tmp_path / "victim"
    with pytest.raises(ValueError):
        session_store._session_payload_path(root, str(victim))


def test_get_session_refuses_traversal_to_external_file(tmp_path: Path) -> None:
    """An unvalidated session_id let get_session read a .json OUTSIDE the sessions dir."""
    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "leak.json").write_text(json.dumps({"secret": "x"}), encoding="utf-8")

    sessions_dir = session_store._sessions_dir(root)
    # id such that f"{id}.json" resolves to external/leak.json
    evil_id = os.path.relpath(external / "leak", sessions_dir)
    with pytest.raises(ValueError):
        session_store.get_session(evil_id, str(root))


def test_session_payload_path_allows_legit_id(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    payload_path = session_store._session_payload_path(
        root, "session-20260101000000000000-repo-deadbeef"
    )
    sessions_dir_resolved = session_store._sessions_dir(root).resolve()
    assert sessions_dir_resolved in payload_path.parents
