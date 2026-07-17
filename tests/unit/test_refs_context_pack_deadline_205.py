"""#205: build_symbol_refs_from_map threads the shared warm-daemon deadline into its INTERNAL
build_context_pack_from_map call.

The refs handler's DOMINANT reference-scan loop was already deadline-bounded, but it invoked
``build_context_pack_from_map(repo_map, symbol)`` BARE -- leaving that stage's own in-memory
symbol-scoring + pagerank loop unbounded on a very large session repo, unlike the sibling
callers/impact handlers which thread the deadline (repo_map.py:16431 / 15011). Surfaced by the
#203/#652 Opus gate as a pre-existing parity gap.

This spy test isolates the fix from refs' already-bounded scan loop: a value-level overrun test
would pass vacuously (refs' dominant loop already trips ``partial`` on an expired deadline), so
we assert the kwargs actually forwarded to the internal call instead.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map


def _project(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "m.py").write_text(
        "def helper():\n    return 1\n\n\ndef other():\n    return helper()\n",
        encoding="utf-8",
    )
    return project.resolve()


def test_refs_threads_deadline_into_internal_context_pack(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project(tmp_path)
    rmap = repo_map.build_repo_map(str(project))

    captured: dict[str, Any] = {}
    original = repo_map.build_context_pack_from_map

    def _spy(rm: Any, symbol: str, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return original(rm, symbol, **kwargs)

    monkeypatch.setattr(repo_map, "build_context_pack_from_map", _spy)

    sentinel = time.monotonic() + 10_000.0
    repo_map.build_symbol_refs_from_map(rmap, "helper", deadline_monotonic=sentinel)

    # #205 (was a bare ``build_context_pack_from_map(repo_map, symbol)``): refs must forward its
    # deadline + a deadline_hit flag so the internal symbol-scoring / pagerank stage is bounded
    # and its early break folds into refs' partial signal (mirrors callers repo_map.py:16431).
    assert captured.get("deadline_monotonic") == sentinel
    assert "deadline_hit" in captured
