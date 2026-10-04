from pathlib import Path

import pytest

from tensor_grep.cli import session_store as ss
from tensor_grep.cli.freshness import check_freshness


def _open_empty(tmp_path: Path):
    root = tmp_path.resolve()
    (root / ".git").mkdir()
    opened = ss.open_session(str(root))
    return root, ss.get_session(opened.session_id, str(root))


def test_added_file_makes_an_empty_session_stale(tmp_path: Path) -> None:
    root, payload = _open_empty(tmp_path)
    assert not payload.get("snapshot")
    (root / "new_mod.py").write_text("def brand_new():\n    pass\n", encoding="utf-8")
    with pytest.raises(ss.SessionStaleError):
        ss._ensure_session_not_stale(payload, detect_added_files=True)
    assert check_freshness(root)["stale_count"] == 1


def test_empty_session_default_path_stays_cheap(tmp_path: Path) -> None:
    root, payload = _open_empty(tmp_path)
    (root / "new_mod.py").write_text("x = 1\n", encoding="utf-8")
    ss._ensure_session_not_stale(payload)  # detect_added_files=False must not walk or raise


def test_untouched_empty_session_is_current(tmp_path: Path) -> None:
    root, _ = _open_empty(tmp_path)
    assert check_freshness(root)["stale_count"] == 0
