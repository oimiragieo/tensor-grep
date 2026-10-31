"""Publication metadata must not consume the source scan or hide index siblings."""

from pathlib import Path

from tensor_grep.cli import codemap


def test_metadata_is_excluded_before_caps_and_from_folder_census(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "one.py"
    source.write_text("value = 1\n", encoding="utf-8")
    output = tmp_path / "map"
    index = repo / "custom" / "index.md"
    result = codemap.build_codemap(repo, out=output, index=index, max_repo_files=2)
    assert result["files_total"] == 1
    assert result["folders_total"] == 1
    assert result["folders_with_no_mapped_files"] == 0
    assert result["partial"] is False
    assert codemap.check_codemap_freshness(repo, out=output, index=index)["fresh"]
    metadata = codemap._metadata_files(output, index)
    assert set(codemap._walk_only_universe(repo, max_repo_files=2, exclude_files=metadata)) == {
        str(source),
        str(index),
    }
    assert codemap._revision_exclude_files(index, output, repo) == [
        "custom/.codemap.lock",
        "custom/index.md",
    ]
    sibling = index.parent / "another.py"
    sibling.write_text("value = 2\n", encoding="utf-8")
    assert not codemap.check_codemap_freshness(repo, out=output, index=index)["fresh"]
    result = codemap.build_codemap(repo, out=output, index=index)
    assert result["files_total"] == 2
    assert codemap.check_codemap_freshness(repo, out=output, index=index)["fresh"]
