from __future__ import annotations

import time
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map, session_store


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _build_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"

    core_path = _write(
        src_dir / "core.py",
        "def create_invoice(total):\n"
        "    return total + 1\n",
    )
    service_path = _write(
        src_dir / "service.py",
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
    )
    helper_path = _write(
        src_dir / "helpers.py",
        "def format_invoice_label(invoice_id):\n"
        "    return f'invoice-{invoice_id}'\n",
    )
    test_path = _write(
        tests_dir / "test_service.py",
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
    )

    return {
        "project": project,
        "core": core_path,
        "service": service_path,
        "helper": helper_path,
        "test": test_path,
    }


def _open_session(project: Path) -> str:
    return session_store.open_session(str(project)).session_id


def _session_payload(project: Path, session_id: str) -> dict[str, object]:
    return session_store.get_session(session_id, str(project))


def _changeset_for_session(project: Path, session_id: str) -> dict[str, list[str]]:
    changeset = session_store._stale_changeset(_session_payload(project, session_id))
    assert changeset is not None
    return changeset


def _empty_changeset() -> dict[str, list[str]]:
    return {"added": [], "modified": [], "removed": []}


def test_stale_changeset_returns_empty_lists_for_fresh_session(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])

    assert _changeset_for_session(paths["project"], session_id) == _empty_changeset()


def test_stale_changeset_detects_modified_file(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n"
        "    subtotal = total + 1\n"
        "    return subtotal\n",
        encoding="utf-8",
    )

    assert _changeset_for_session(paths["project"], session_id) == {
        "added": [],
        "modified": [str(paths["core"].resolve())],
        "removed": [],
    }


def test_stale_changeset_detects_added_file(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    added_path = _write(
        paths["project"] / "src" / "billing.py",
        "from src.core import create_invoice\n\n"
        "def issue_invoice(total):\n"
        "    return create_invoice(total)\n",
    )

    assert _changeset_for_session(paths["project"], session_id) == {
        "added": [str(added_path.resolve())],
        "modified": [],
        "removed": [],
    }


def test_stale_changeset_detects_removed_file(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["helper"].unlink()

    assert _changeset_for_session(paths["project"], session_id) == {
        "added": [],
        "modified": [],
        "removed": [str(paths["helper"].resolve())],
    }


def test_stale_changeset_detects_added_modified_and_removed_files(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["service"].write_text(
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    amount = create_invoice(total)\n"
        "    return amount\n",
        encoding="utf-8",
    )
    added_path = _write(
        paths["project"] / "src" / "api.py",
        "from src.service import build_invoice\n\n"
        "def present_invoice(total):\n"
        "    return build_invoice(total)\n",
    )
    paths["helper"].unlink()

    assert _changeset_for_session(paths["project"], session_id) == {
        "added": [str(added_path.resolve())],
        "modified": [str(paths["service"].resolve())],
        "removed": [str(paths["helper"].resolve())],
    }


def test_build_repo_map_incremental_matches_full_build_for_mixed_changes(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    previous_map = _session_payload(paths["project"], session_id)["repo_map"]
    assert isinstance(previous_map, dict)

    paths["service"].write_text(
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    amount = create_invoice(total)\n"
        "    return amount + 2\n",
        encoding="utf-8",
    )
    _write(
        paths["project"] / "src" / "billing.py",
        "from src.core import create_invoice\n\n"
        "def issue_invoice(total):\n"
        "    return create_invoice(total)\n",
    )
    paths["helper"].unlink()

    changeset = _changeset_for_session(paths["project"], session_id)
    incremental_map = repo_map.build_repo_map_incremental(previous_map, changeset)
    full_map = repo_map.build_repo_map(paths["project"])

    assert incremental_map == full_map


def test_build_repo_map_incremental_only_reparses_changed_files(tmp_path: Path, monkeypatch) -> None:
    paths = _build_project(tmp_path)
    previous_map = repo_map.build_repo_map(paths["project"])

    paths["service"].write_text(
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total) + 5\n",
        encoding="utf-8",
    )
    added_path = _write(
        paths["project"] / "src" / "billing.py",
        "def issue_invoice(total):\n    return total\n",
    )

    parsed_paths: list[str] = []
    original = repo_map._imports_and_symbols_for_path

    def tracking_parser(path: Path) -> tuple[list[str], list[dict[str, object]]]:
        parsed_paths.append(str(path.resolve()))
        return original(path)

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", tracking_parser)

    incremental_map = repo_map.build_repo_map_incremental(
        previous_map,
        {
            "added": [str(added_path.resolve())],
            "modified": [str(paths["service"].resolve())],
            "removed": [],
        },
    )

    assert set(parsed_paths) == {
        str(added_path.resolve()),
        str(paths["service"].resolve()),
    }
    helper_symbols_before = [
        symbol for symbol in previous_map["symbols"] if symbol["file"] == str(paths["helper"].resolve())
    ]
    helper_symbols_after = [
        symbol for symbol in incremental_map["symbols"] if symbol["file"] == str(paths["helper"].resolve())
    ]
    assert helper_symbols_after == helper_symbols_before


def test_build_repo_map_incremental_removes_deleted_entries_from_payload(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    previous_map = repo_map.build_repo_map(paths["project"])
    paths["helper"].unlink()
    paths["test"].unlink()

    incremental_map = repo_map.build_repo_map_incremental(
        previous_map,
        {
            "added": [],
            "modified": [],
            "removed": [
                str(paths["helper"].resolve()),
                str(paths["test"].resolve()),
            ],
        },
    )

    assert str(paths["helper"].resolve()) not in incremental_map["files"]
    assert str(paths["test"].resolve()) not in incremental_map["tests"]
    assert str(paths["helper"].resolve()) not in incremental_map["related_paths"]
    assert all(entry["file"] != str(paths["helper"].resolve()) for entry in incremental_map["imports"])
    assert all(symbol["file"] != str(paths["helper"].resolve()) for symbol in incremental_map["symbols"])


def test_incremental_repo_map_matches_full_graph_outputs_after_import_change(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    previous_map = repo_map.build_repo_map(paths["project"])
    api_path = _write(
        paths["project"] / "src" / "api.py",
        "from src.service import build_invoice\n\n"
        "def present_invoice(total):\n"
        "    return build_invoice(total)\n",
    )
    changeset = {"added": [str(api_path.resolve())], "modified": [], "removed": []}

    incremental_map = repo_map.build_repo_map_incremental(previous_map, changeset)
    full_map = repo_map.build_repo_map(paths["project"])

    assert repo_map.build_context_pack_from_map(incremental_map, "invoice") == repo_map.build_context_pack_from_map(
        full_map,
        "invoice",
    )


def test_refresh_session_uses_incremental_builder_when_changeset_available(
    tmp_path: Path, monkeypatch
) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n"
        "    return total + 2\n",
        encoding="utf-8",
    )

    incremental_calls = {"count": 0}
    full_calls = {"count": 0}
    original_incremental = session_store.build_repo_map_incremental

    def tracking_incremental(previous_map: dict[str, object], changeset: dict[str, list[str]]) -> dict[str, object]:
        incremental_calls["count"] += 1
        return original_incremental(previous_map, changeset)

    def unexpected_full_build(path: str | Path = ".") -> dict[str, object]:
        full_calls["count"] += 1
        return repo_map.build_repo_map(path)

    monkeypatch.setattr(session_store, "build_repo_map_incremental", tracking_incremental)
    monkeypatch.setattr(session_store, "build_repo_map", unexpected_full_build)

    refreshed = session_store.refresh_session(session_id, str(paths["project"]))

    assert refreshed.refresh_type == "incremental"
    assert incremental_calls["count"] == 1
    assert full_calls["count"] == 0


def test_refresh_session_falls_back_to_full_rebuild_when_incremental_fails(
    tmp_path: Path, monkeypatch
) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n"
        "    return total + 3\n",
        encoding="utf-8",
    )

    full_calls = {"count": 0}

    def failing_incremental(previous_map: dict[str, object], changeset: dict[str, list[str]]) -> dict[str, object]:
        raise RuntimeError("boom")

    def tracking_full_build(path: str | Path = ".") -> dict[str, object]:
        full_calls["count"] += 1
        return repo_map.build_repo_map(path)

    monkeypatch.setattr(session_store, "build_repo_map_incremental", failing_incremental)
    monkeypatch.setattr(session_store, "build_repo_map", tracking_full_build)

    refreshed = session_store.refresh_session(session_id, str(paths["project"]))

    assert refreshed.refresh_type == "full"
    assert full_calls["count"] == 1


def test_refresh_session_persists_changeset_and_refresh_type_in_payload(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["service"].write_text(
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total) + 4\n",
        encoding="utf-8",
    )
    added_path = _write(paths["project"] / "src" / "billing.py", "def issue_invoice(total):\n    return total\n")
    paths["helper"].unlink()

    refreshed = session_store.refresh_session(session_id, str(paths["project"]))
    payload = _session_payload(paths["project"], session_id)

    assert refreshed.refresh_type == "incremental"
    assert refreshed.changeset == {
        "added": [str(added_path.resolve())],
        "modified": [str(paths["service"].resolve())],
        "removed": [str(paths["helper"].resolve())],
    }
    assert payload["refresh_type"] == "incremental"
    assert payload["changeset"] == refreshed.changeset


def test_refresh_session_incremental_repo_map_matches_full_rebuild(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n"
        "    base = total + 1\n"
        "    return base * 2\n",
        encoding="utf-8",
    )
    _write(
        paths["project"] / "src" / "billing.py",
        "from src.core import create_invoice\n\n"
        "def issue_invoice(total):\n"
        "    return create_invoice(total)\n",
    )

    refreshed = session_store.refresh_session(session_id, str(paths["project"]))
    payload = _session_payload(paths["project"], session_id)

    assert refreshed.refresh_type == "incremental"
    assert payload["repo_map"] == repo_map.build_repo_map(paths["project"])


def test_session_context_raises_stale_error_with_changeset_summary(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n"
        "    return total + 10\n",
        encoding="utf-8",
    )

    with pytest.raises(session_store.SessionStaleError, match="changed on disk") as exc_info:
        session_store.session_context(session_id, "invoice", str(paths["project"]))

    assert str(paths["core"].resolve()) in str(exc_info.value)


def test_incremental_refresh_preserves_plan_seed_for_unchanged_symbol(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])

    before = session_store.session_context_render(
        session_id,
        "create invoice",
        str(paths["project"]),
        max_files=3,
        max_sources=3,
    )

    paths["helper"].write_text(
        "def format_invoice_label(invoice_id):\n"
        "    prefix = 'invoice'\n"
        "    return f'{prefix}-{invoice_id}'\n",
        encoding="utf-8",
    )
    refreshed = session_store.refresh_session(session_id, str(paths["project"]))
    after = session_store.session_context_render(
        session_id,
        "create invoice",
        str(paths["project"]),
        max_files=3,
        max_sources=3,
    )

    assert refreshed.refresh_type == "incremental"
    assert after["edit_plan_seed"] == before["edit_plan_seed"]


def test_incremental_repo_map_is_faster_than_full_rebuild_for_small_changes(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(80):
        _write(
            src_dir / f"module_{index}.py",
            f"def value_{index}():\n"
            f"    return {index}\n",
        )

    previous_map = repo_map.build_repo_map(project)
    changed_path = src_dir / "module_0.py"
    changed_path.write_text("def value_0():\n    return 999\n", encoding="utf-8")

    original = repo_map._imports_and_symbols_for_path

    def slow_parser(path: Path) -> tuple[list[str], list[dict[str, object]]]:
        time.sleep(0.002)
        return original(path)

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", slow_parser)

    start = time.perf_counter()
    repo_map.build_repo_map_incremental(
        previous_map,
        {"added": [], "modified": [str(changed_path.resolve())], "removed": []},
    )
    incremental_duration = time.perf_counter() - start

    start = time.perf_counter()
    repo_map.build_repo_map(project)
    full_duration = time.perf_counter() - start

    assert incremental_duration < (full_duration * 0.5)
