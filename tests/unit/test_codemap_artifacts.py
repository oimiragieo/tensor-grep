"""Hermetic checks for the inert codemap artifact verifier."""

from pathlib import Path

import pytest

from tensor_grep.cli import codemap_artifacts as artifacts
from tensor_grep.cli.codemap_pinned import PinnedDirectory


@pytest.fixture
def artifact_set(tmp_path: Path) -> tuple[Path, Path, dict]:  # type: ignore[type-arg]
    tmp_path = tmp_path.resolve()
    output = tmp_path / "output"
    output.mkdir()
    index = output / "index.md"
    index.write_text("index", encoding="utf-8")
    (output / "_root.md").write_text("page", encoding="utf-8")
    coverage = {
        "folders_total": 1,
        "artifact_manifest": {
            "version": 1,
            "index": {"path": str(index), "sha256": artifacts.digest("index")},
            "pages": {"_root.md": artifacts.digest("page")},
        },
    }
    return output, index, coverage


def _verify(output: Path, index: Path, coverage: dict) -> bool:  # type: ignore[type-arg]
    with PinnedDirectory(output) as pin:
        return artifacts.verify(coverage, output, index, pin, pin)


def test_valid_manifest_and_content_drift(artifact_set: tuple[Path, Path, dict]) -> None:  # type: ignore[type-arg]
    output, index, coverage = artifact_set
    assert _verify(output, index, coverage) is True
    (output / "_root.md").write_text("changed", encoding="utf-8")
    assert _verify(output, index, coverage) is False


@pytest.mark.parametrize(
    "mutation",
    ["version", "wrong_index", "bad_hash", "traversal", "count", "duplicate", "index_collision"],
)
def test_invalid_manifest_refused(artifact_set: tuple[Path, Path, dict], mutation: str) -> None:  # type: ignore[type-arg]
    output, index, coverage = artifact_set
    assert _verify(output, index, coverage) is True
    manifest = coverage["artifact_manifest"]
    if mutation == "version":
        manifest["version"] = True
    elif mutation == "wrong_index":
        manifest["index"]["path"] = str(output / "other.md")
    elif mutation == "bad_hash":
        manifest["index"]["sha256"] = "not-a-hash"
    elif mutation == "traversal":
        manifest["pages"] = {"../outside.md": artifacts.digest("page")}
    elif mutation == "count":
        coverage["folders_total"] = 2
    elif mutation == "duplicate":
        manifest["pages"]["_ROOT.md"] = artifacts.digest("page")
        coverage["folders_total"] = 2
    else:
        manifest["pages"] = {"index.md": artifacts.digest("index")}
    assert _verify(output, index, coverage) is False


def test_missing_and_symlinked_page_refused(
    artifact_set: tuple[Path, Path, dict], tmp_path: Path
) -> None:  # type: ignore[type-arg]
    output, index, coverage = artifact_set
    assert _verify(output, index, coverage) is True
    page = output / "_root.md"
    page.unlink()
    assert _verify(output, index, coverage) is False
    outside = tmp_path / "outside.md"
    outside.write_text("page", encoding="utf-8")
    try:
        page.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    assert _verify(output, index, coverage) is False


def test_limits_and_stamp_reads(
    artifact_set: tuple[Path, Path, dict], monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[type-arg]
    output, index, coverage = artifact_set
    assert artifacts.add_size("abc", 2) == 5
    with PinnedDirectory(output) as pin:
        assert artifacts.read_stamp(pin, "missing.json") is None
        (output / "stamp.json").write_text("stamp", encoding="utf-8")
        assert artifacts.read_stamp(pin, "stamp.json") == b"stamp"
        monkeypatch.setattr(artifacts, "MAX_STAMP_BYTES", 1)
        assert artifacts.read_stamp(pin, "stamp.json") is None
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 3)
    with pytest.raises(ValueError, match="byte limits"):
        artifacts.add_size("four", 0)
    assert _verify(output, index, coverage) is False


def test_page_count_and_total_byte_caps(
    artifact_set: tuple[Path, Path, dict], monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[type-arg]
    output, index, coverage = artifact_set
    assert _verify(output, index, coverage) is True
    monkeypatch.setattr(artifacts, "MAX_PAGES", 0)
    assert _verify(output, index, coverage) is False
    monkeypatch.setattr(artifacts, "MAX_PAGES", 1)
    monkeypatch.setattr(artifacts, "MAX_TOTAL_BYTES", 8)
    assert _verify(output, index, coverage) is False
    with pytest.raises(ValueError, match="byte limits"):
        artifacts.add_size("five!", 4)


def test_empty_pages_and_external_index(tmp_path: Path) -> None:
    tmp_path = tmp_path.resolve()
    output = tmp_path / "output"
    output.mkdir()
    external = tmp_path / "index.md"
    external.write_text("café", encoding="utf-8")
    coverage = {
        "folders_total": 0,
        "artifact_manifest": {
            "version": 1,
            "index": {"path": str(external), "sha256": artifacts.digest("café")},
            "pages": {},
        },
    }
    with PinnedDirectory(output) as output_pin, PinnedDirectory(tmp_path) as index_pin:
        assert artifacts.verify(coverage, output, external, output_pin, index_pin) is True
        external.write_text("cafe", encoding="utf-8")
        assert artifacts.verify(coverage, output, external, output_pin, index_pin) is False
