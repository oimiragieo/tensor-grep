"""Generation publishes a bounded manifest only after its locked artifact writes."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tensor_grep.cli import codemap as cm
from tensor_grep.cli import codemap_artifacts as artifacts
from tensor_grep.cli.codemap_pinned import PinnedDirectory
from tensor_grep.cli.codemap_publication import LOCK_NAME, Publication, publication_lock


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "alpha.py").write_text(
        '"""Résumé Ω."""\ndef alpha():\n    return "é"\n', encoding="utf-8"
    )
    return repo, repo / "maps", repo / "index.md"


def _build(project, **kwargs):
    repo, output, index = project
    kwargs.setdefault("_revision_identity", lambda root: {"status": "unavailable"})
    kwargs.setdefault("_now", lambda: datetime(2026, 1, 1, tzinfo=UTC))
    return cm.build_codemap(repo, out=output, index=index, **kwargs)


def test_locks_precede_revision_and_scan_and_manifest_is_last(project, monkeypatch) -> None:
    repo, output, index = project
    phases, writes = [], []
    original_scan, original_write = cm._repo_map.build_repo_map, PinnedDirectory.write

    def held():
        for path in {output / LOCK_NAME, index.parent / LOCK_NAME}:
            assert path.is_file()
            with pytest.raises(OSError, match="lock is contended or changed"):
                with publication_lock(path.parent, path.parent / "probe.md", create=False):
                    pytest.fail("generation did not hold the publication lock")

    def revision(root):
        assert root == repo
        held()
        phases.append("revision")
        return {"status": "unavailable"}

    def scan(*args, **kwargs):
        held()
        phases.append("scan")
        return original_scan(*args, **kwargs)

    def write(pin, name, data, *, validate):
        writes.append((pin.path / name, data))
        return original_write(pin, name, data, validate=validate)

    monkeypatch.setattr(cm._repo_map, "build_repo_map", scan)
    monkeypatch.setattr(PinnedDirectory, "write", write)
    public = _build(project, _revision_identity=revision)
    stamp_path = output / cm._COVERAGE_FILENAME
    stamp = json.loads(stamp_path.read_bytes())
    assert phases == ["revision", "scan"]
    assert writes[0] == (stamp_path, b'{"partial": true}\n')
    assert writes[-1][0] == stamp_path
    assert json.loads(writes[-1][1]) == stamp
    manifest = stamp["artifact_manifest"]
    expected = {index: manifest["index"]["sha256"]}
    expected.update({output / name: sha for name, sha in manifest["pages"].items()})
    assert [path for path, _ in writes[1:-1]] == [output / "_root.md", index]
    for path, sha in expected.items():
        data = path.read_bytes()
        data.decode("utf-8")
        assert hashlib.sha256(data).hexdigest() == sha
    with publication_lock(output, index, create=False) as state:
        assert artifacts.verify(stamp, output, index, state.output, state.index_parent)
    assert "artifact_manifest" not in public
    json_public = json.loads(cm.build_codemap_json(repo, out=output, index=index))
    assert "artifact_manifest" not in json_public


@pytest.mark.parametrize("failed_name", ["_root.md", "index.md"])
def test_interrupted_artifact_write_invalidates_existing_stamp(project, monkeypatch, failed_name):
    _build(project)
    original = PinnedDirectory.write

    def fail(pin, name, data, *, validate):
        if name == failed_name:
            raise OSError("injected artifact write failure")
        return original(pin, name, data, validate=validate)

    monkeypatch.setattr(PinnedDirectory, "write", fail)
    with pytest.raises(OSError, match="injected artifact write failure"):
        _build(project)
    assert json.loads((project[1] / cm._COVERAGE_FILENAME).read_bytes()) == {"partial": True}


def test_changed_publication_context_refuses_before_stamp(project, monkeypatch):
    def revision(root):
        monkeypatch.setattr(Publication, "unchanged", lambda self: False)
        return {"status": "unavailable"}

    with pytest.raises(OSError, match="publication directories or locks changed"):
        _build(project, _revision_identity=revision)
    assert not (project[1] / cm._COVERAGE_FILENAME).exists()
    assert not project[2].exists()


@pytest.mark.parametrize(
    "name",
    [LOCK_NAME, "_coverage.json", "_COVERAGE.JSON"]
    + ([".codemap.lock. ", "_coverage.json. ", "_coverage.json:extra"] if os.name == "nt" else []),
)
def test_reserved_index_refused_before_output_creation(project, name):
    repo, output, _ = project
    with pytest.raises(ValueError, match="reserved metadata"):
        cm.build_codemap(repo, out=output, index=name)
    assert not output.exists()


@pytest.mark.parametrize("existing", [False, True])
def test_directory_index_refused_before_output_creation(project, existing):
    repo, output, _ = project
    index = repo / "existing" if existing else output
    if existing:
        index.mkdir()
    with pytest.raises(ValueError, match="file destination"):
        cm.build_codemap(repo, out=output, index=index)
    assert not output.exists()


@pytest.mark.parametrize("collision", ["casefold", "index-page"])
def test_generated_name_collision_refused_before_stamp_or_artifacts(
    project, monkeypatch, collision
):
    repo, output, index = project
    if collision == "casefold":
        (repo / "other").mkdir()
        (repo / "other" / "beta.py").write_text("value = 1\n", encoding="utf-8")
        monkeypatch.setattr(cm, "_folder_slug", lambda folder: "Page" if folder else "page")
    else:
        index = output / "_root.md"
    with pytest.raises(ValueError, match="filenames collide"):
        _build((repo, output, index))
    assert {path.name for path in output.iterdir()} == {LOCK_NAME}
    assert not index.exists()


@pytest.mark.parametrize(
    "limit", ["MAX_PAGES", "MAX_ARTIFACT_BYTES", "MAX_TOTAL_BYTES", "MAX_STAMP_BYTES"]
)
def test_generation_limits_allow_boundary_and_refuse_one_less(project, monkeypatch, limit):
    _build(project)
    _, output, index = project
    stamp_path = output / cm._COVERAGE_FILENAME
    stamp_bytes = stamp_path.read_bytes()
    manifest = json.loads(stamp_bytes)["artifact_manifest"]
    sizes = [
        len(index.read_bytes()),
        *[len((output / name).read_bytes()) for name in manifest["pages"]],
    ]
    boundary = {
        "MAX_PAGES": len(manifest["pages"]),
        "MAX_ARTIFACT_BYTES": max(sizes),
        "MAX_TOTAL_BYTES": sum(sizes),
        "MAX_STAMP_BYTES": len(stamp_bytes),
    }[limit]
    monkeypatch.setattr(artifacts, limit, boundary)
    assert _build(project)["partial"] is False
    stamp_bytes = stamp_path.read_bytes()
    monkeypatch.setattr(artifacts, limit, boundary - 1)
    with pytest.raises(ValueError, match="verification"):
        _build(project)
    if limit == "MAX_PAGES":
        assert stamp_path.read_bytes() == stamp_bytes
    else:
        assert json.loads(stamp_path.read_bytes()) == {"partial": True}
