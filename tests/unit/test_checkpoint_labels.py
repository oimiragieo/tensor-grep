"""Checkpoint labels are durable annotations; checkpoint IDs remain undo authority."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import checkpoint_store
from tensor_grep.cli.main import app


def _project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample.py").write_text("value = 'before'\n", encoding="utf-8")
    return root


def test_checkpoint_label_is_trimmed_and_persisted_through_cli_store_and_list(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "project")
    label = "  再確認 🧭  "

    result = CliRunner().invoke(
        app,
        ["checkpoint", "create", str(project), "--label", label, "--json"],
    )

    assert result.exit_code == 0, result.output
    created = json.loads(result.stdout)
    assert created["label"] == "再確認 🧭"
    record = checkpoint_store.list_checkpoints(str(project))[0]
    assert record.label == "再確認 🧭"

    storage = project / ".tensor-grep" / "checkpoints" / created["checkpoint_id"]
    metadata = json.loads((storage / "metadata.json").read_text(encoding="utf-8"))
    index = json.loads(
        (project / ".tensor-grep" / "checkpoints" / "index.json").read_text(encoding="utf-8")
    )
    assert metadata["label"] == "再確認 🧭"
    assert index[0]["label"] == "再確認 🧭"

    listed = CliRunner().invoke(app, ["checkpoint", "list", str(project)])
    assert listed.exit_code == 0, listed.output
    assert "再確認 🧭" in listed.stdout


def test_checkpoint_label_accepts_the_120_character_boundary(tmp_path: Path) -> None:
    project = _project(tmp_path / "project")

    created = checkpoint_store.create_checkpoint(str(project), label="x" * 120)

    assert created.label == "x" * 120
    assert checkpoint_store.list_checkpoints(str(project))[0].label == "x" * 120


@pytest.mark.parametrize("label", ["", " \t ", "line\nbreak", "bad\x7f", "x" * 121])
def test_invalid_label_is_rejected_before_checkpoint_state_or_copy(
    tmp_path: Path, label: str
) -> None:
    project = _project(tmp_path / "project")

    with pytest.raises(ValueError, match="label"):
        checkpoint_store.create_checkpoint(str(project), label=label)

    assert (project / "sample.py").read_text(encoding="utf-8") == "value = 'before'\n"
    assert not (project / ".tensor-grep").exists()


def test_legacy_checkpoint_callers_and_old_index_records_default_to_no_label(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "project")
    created = checkpoint_store.create_checkpoint(str(project))
    index_path = project / ".tensor-grep" / "checkpoints" / "index.json"
    legacy_index = json.loads(index_path.read_text(encoding="utf-8"))
    for entry in legacy_index:
        entry.pop("label", None)
    index_path.write_text(json.dumps(legacy_index), encoding="utf-8")

    assert checkpoint_store.list_checkpoints(str(project))[0].label is None
    assert created.label is None
    metadata_path = (
        project / ".tensor-grep" / "checkpoints" / created.checkpoint_id / "metadata.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("label", None)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    index_path.unlink()
    loaded_metadata = checkpoint_store.load_checkpoint_metadata(created.checkpoint_id, str(project))
    assert loaded_metadata["label"] is None
    recovered = checkpoint_store.discover_checkpoint_scopes_result(str(tmp_path), full=True)
    assert recovered.scopes[0].checkpoints[0].label is None
    assert (
        asdict(
            checkpoint_store.CheckpointRecord(
                1,
                "ckpt-legacy",
                "filesystem-snapshot",
                str(project),
                "2026-01-01T00:00:00+00:00",
                1,
            )
        )["label"]
        is None
    )
    assert (
        checkpoint_store.CheckpointCreateResult(
            "ckpt-positional",
            "filesystem-snapshot",
            str(project),
            "2026-01-01T00:00:00+00:00",
            1,
            [],
            "tg checkpoint undo ckpt-positional",
        ).label
        is None
    )


def test_malformed_persisted_labels_load_as_none_without_hiding_checkpoint(tmp_path: Path) -> None:
    project = _project(tmp_path / "project")
    created = checkpoint_store.create_checkpoint(str(project), label="good")
    store = project / ".tensor-grep" / "checkpoints"
    metadata_path = store / created.checkpoint_id / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["label"] = "bad\nlabel"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert (
        checkpoint_store.load_checkpoint_metadata(created.checkpoint_id, str(project))["label"]
        is None
    )
    index_path = store / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index[0]["label"] = 123
    index_path.write_text(json.dumps(index), encoding="utf-8")
    records = checkpoint_store.list_checkpoints(str(project))
    assert len(records) == 1
    assert records[0].checkpoint_id == created.checkpoint_id
    assert records[0].label is None

    index_path.unlink()
    recovered = checkpoint_store.discover_checkpoint_scopes_result(str(tmp_path), full=True)
    assert recovered.scopes[0].checkpoints[0].checkpoint_id == created.checkpoint_id
    assert recovered.scopes[0].checkpoints[0].label is None


def test_label_survives_metadata_index_recovery_and_scope_discovery(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = _project(workspace / "nested" / "project")
    created = checkpoint_store.create_checkpoint(str(project), label="recovered label")
    (project / ".tensor-grep" / "checkpoints" / "index.json").unlink()

    found = checkpoint_store.discover_checkpoint_scopes_result(str(workspace), full=True)

    assert len(found.scopes) == 1
    assert found.scopes[0].root == str(project.resolve())
    assert found.scopes[0].checkpoints[0].checkpoint_id == created.checkpoint_id
    assert found.scopes[0].checkpoints[0].label == "recovered label"


def test_duplicate_labels_survive_retention_and_undo_still_uses_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path / "project")
    monkeypatch.setenv("TG_CHECKPOINT_MAX", "2")

    first = checkpoint_store.create_checkpoint(str(project), label="same name")
    (project / "sample.py").write_text("value = 'middle'\n", encoding="utf-8")
    second = checkpoint_store.create_checkpoint(str(project), label="same name")
    assert first.checkpoint_id != second.checkpoint_id
    assert [record.label for record in checkpoint_store.list_checkpoints(str(project))] == [
        "same name",
        "same name",
    ]

    (project / "sample.py").write_text("value = 'after'\n", encoding="utf-8")
    latest = checkpoint_store.create_checkpoint(str(project), label="after edit")
    retained_ids = {
        record.checkpoint_id for record in checkpoint_store.list_checkpoints(str(project))
    }
    assert retained_ids == {second.checkpoint_id, latest.checkpoint_id}
    undone = checkpoint_store.undo_checkpoint(second.checkpoint_id, str(project))

    assert undone.checkpoint_id == second.checkpoint_id
    assert (project / "sample.py").read_text(encoding="utf-8") == "value = 'middle'\n"


def test_scoped_checkpoint_label_does_not_expand_undo_scope(tmp_path: Path) -> None:
    project = _project(tmp_path / "project")
    other = project / "other.py"
    other.write_text("other = 'before'\n", encoding="utf-8")
    created = checkpoint_store.create_checkpoint(
        str(project), paths=["sample.py"], label="single file snapshot"
    )
    (project / "sample.py").write_text("value = 'after'\n", encoding="utf-8")
    other.write_text("other = 'after'\n", encoding="utf-8")

    checkpoint_store.undo_checkpoint(created.checkpoint_id, str(project))

    assert (project / "sample.py").read_text(encoding="utf-8") == "value = 'before'\n"
    assert other.read_text(encoding="utf-8") == "other = 'after'\n"


def test_checkpoint_label_is_available_on_cli_paths_option(tmp_path: Path) -> None:
    project = _project(tmp_path / "project")
    result = CliRunner().invoke(
        app,
        [
            "checkpoint",
            "create",
            str(project),
            "--paths",
            "sample.py",
            "--label",
            "cli-scoped",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["label"] == "cli-scoped"


def test_checkpoint_create_text_output_shows_label(tmp_path: Path) -> None:
    project = _project(tmp_path / "project")

    result = CliRunner().invoke(
        app, ["checkpoint", "create", str(project), "--label", "before change"]
    )

    assert result.exit_code == 0, result.output
    assert "label='before change'" in result.stdout


def test_mcp_checkpoint_label_schema_and_legacy_tool_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import mcp_server

    project = _project(tmp_path / "project")
    monkeypatch.chdir(tmp_path)
    schemas = {tool.name: tool.inputSchema for tool in asyncio.run(mcp_server.mcp.list_tools())}
    for tool_name in ("tg_checkpoint_create", "tg_checkpoint"):
        properties = schemas[tool_name]["properties"]
        schema = properties["label"]
        assert "string" in {
            schema.get("type"),
            *(sub.get("type") for sub in schema.get("anyOf", ())),
        }
        assert "label" not in schemas[tool_name].get("required", [])

    content, _meta = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_checkpoint_create", {"path": str(project), "label": "mcp label"}
        )
    )
    payload = json.loads(cast(Any, content)[0].text)
    assert payload["label"] == "mcp label"
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION

    meta_content, _meta = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_checkpoint", {"action": "create", "path": str(project), "label": "meta label"}
        )
    )
    meta_payload = json.loads(cast(Any, meta_content)[0].text)
    assert meta_payload["label"] == "meta label"


@pytest.mark.parametrize("bad_label", [123, True, [], object()])
def test_mcp_label_bad_types_are_structured_invalid_input_for_direct_legacy_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_label: object
) -> None:
    from tensor_grep.cli import mcp_server

    project = _project(tmp_path / "project")
    monkeypatch.chdir(tmp_path)

    payload = json.loads(
        mcp_server.tg_checkpoint_create(path=str(project), label=cast(Any, bad_label))
    )

    assert payload["error"]["code"] == "invalid_input"


@pytest.mark.parametrize(
    ("action", "arguments"),
    [
        ("list", {"label": "meaningless here"}),
        ("undo", {"checkpoint_id": "ckpt-missing", "label": "meaningless here"}),
        ("create", {"label": 123}),
    ],
)
def test_mcp_checkpoint_rejects_invalid_label_use_as_structured_input_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    arguments: dict[str, object],
) -> None:
    from tensor_grep.cli import mcp_server

    project = _project(tmp_path / "project")
    monkeypatch.chdir(tmp_path)

    payload = json.loads(mcp_server.tg_checkpoint(action=action, path=str(project), **arguments))

    assert payload["error"]["code"] == "invalid_input"
